"""Gobernanza y guardrails [P1-04] [P1-05] [P3-05]."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, List, Tuple

import numpy as np

from uasi_v5.core.excepciones import ErrorGobernanza, ErrorValidacionDatos
from uasi_v5.core.utilidades import json_canonico
from uasi_v5.esquemas.solicitudes import FilaNumerica


def _normalizar_texto(texto: str) -> str:
    """Minúsculas y sin tildes para comparar intenciones de forma robusta."""
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class GuardiánGobernanzaSeguridad:
    """
    Política declarativa de gobernanza:
      1) ALLOW-LIST: solo se aceptan intenciones explícitamente permitidas por
         endpoint (en v1/streaming el campo `intencion` debe pertenecer al
         conjunto; en el master el prompt debe mapear a ≥1 categoría válida).
      2) DENY-LIST: patrones de inyección/abuso como defensa en profundidad.
      3) Límites estructurales sobre metadatos_contexto.
      4) Sanitización numérica NaN/±Inf/None con imputación por columna.
    """

    INTENCIONES_V1 = frozenset({"clasificacion", "clasificacion_binaria", "prediccion", "entrenamiento", "analisis"})
    INTENCIONES_STREAMING = frozenset(
        {"aprendizaje_online", "aprendizaje_online_continuo", "streaming", "entrenamiento_incremental"}
    )
    # Categorías semánticas del master → palabras clave (texto normalizado)
    CATEGORIAS_MASTER: Dict[str, Tuple[str, ...]] = {
        "DIAGNOSTICO": ("diagnostic", "salud", "health", "estado del sistema", "estado de los modelos"),
        "AUDITORIA": ("auditori", "ledger", "merkle", "trazabilidad"),
        "APRENDIZAJE_ONLINE_CONTINUO": ("streaming", "online", "continuo", "incremental", "tiempo real"),
        "EXPLICABILIDAD": ("explica", "interpret", "importancia", "transparen"),
        "OPTIMIZACION_EVOLUTIVA": ("optimiz", "evolut", "hiperparam", "genetic"),
        "APRENDIZAJE_PROFUNDO": ("profund", "deep", "neuronal"),
        "ANALISIS_METACOGNITIVO_AUTO": ("clasific", "predic", "entren", "analiz", "aprend", "modela"),
    }
    PATRONES_DENEGADOS = tuple(
        re.compile(p, re.IGNORECASE)
        for p in (
            r"ignora(r)?\s+(las\s+)?instrucciones", r"ignore\s+(all\s+)?previous", r"system\s*prompt",
            r"rm\s+-rf", r"drop\s+table", r";\s*shutdown", r"<\s*script", r"__import__", r"\beval\s*\(",
            r"\bexec\s*\(", r"os\.system", r"subprocess", r"exfiltr", r"\.\./", r"/etc/passwd", r"\bpickle\b",
        )
    )
    PATRON_CLAVE_META = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
    MAX_CLAVES_META = 50
    MAX_PROFUNDIDAD_META = 3
    MAX_BYTES_META = 16 * 1024

    # --- Texto ---------------------------------------------------------------
    def _verificar_denegados(self, texto: str, campo: str) -> None:
        for patron in self.PATRONES_DENEGADOS:
            if patron.search(texto):
                raise ErrorGobernanza(f"Contenido no permitido en '{campo}' (política de seguridad).")

    def validar_intencion(self, intencion: str, permitidas: frozenset) -> str:
        normal = _normalizar_texto(intencion).strip().replace(" ", "_")
        self._verificar_denegados(intencion, "intencion")
        if normal not in permitidas:
            raise ErrorGobernanza(f"Intención '{intencion}' no permitida. Permitidas: {sorted(permitidas)}.")
        return normal

    def clasificar_prompt_master(self, prompt: str) -> List[str]:
        """Allow-list semántica: devuelve las categorías detectadas (≥1 o error)."""
        self._verificar_denegados(prompt, "prompt_intencion")
        normal = _normalizar_texto(prompt)
        categorias = [c for c, claves in self.CATEGORIAS_MASTER.items() if any(k in normal for k in claves)]
        if not categorias:
            raise ErrorGobernanza(
                "El prompt no corresponde a ninguna intención permitida "
                f"({sorted(self.CATEGORIAS_MASTER)})."
            )
        return categorias

    # --- Metadatos -----------------------------------------------------------
    def validar_metadatos(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        if len(json_canonico(meta).encode()) > self.MAX_BYTES_META:
            raise ErrorGobernanza(f"metadatos_contexto excede {self.MAX_BYTES_META} bytes.")
        contador = [0]

        def recorrer(obj: Any, profundidad: int) -> None:
            if profundidad > self.MAX_PROFUNDIDAD_META:
                raise ErrorGobernanza("metadatos_contexto excede la profundidad máxima permitida.")
            if isinstance(obj, dict):
                for k, v in obj.items():
                    contador[0] += 1
                    if contador[0] > self.MAX_CLAVES_META:
                        raise ErrorGobernanza("metadatos_contexto excede el número máximo de claves.")
                    if not isinstance(k, str) or not self.PATRON_CLAVE_META.match(k):
                        raise ErrorGobernanza(f"Clave de metadatos inválida: {k!r}.")
                    recorrer(v, profundidad + 1)
            elif isinstance(obj, list):
                for v in obj:
                    recorrer(v, profundidad + 1)
            elif isinstance(obj, str):
                self._verificar_denegados(obj, "metadatos_contexto")
            elif isinstance(obj, float) and not math.isfinite(obj):
                raise ErrorGobernanza("metadatos_contexto contiene valores no finitos.")

        recorrer(meta, 1)
        return meta

    # --- Numérico ------------------------------------------------------------
    @staticmethod
    def sanitizar_matriz(inputs: List[FilaNumerica]) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Convierte a float64 y sustituye None/NaN por la media de la columna
        (0.0 si la columna no tiene valores finitos) y ±Inf por el máx/mín finito.
        Devuelve la matriz y un informe de lo saneado (transparencia).
        """
        X = np.array([[np.nan if v is None else v for v in fila] for fila in inputs], dtype=np.float64)
        if X.ndim != 2:
            raise ErrorValidacionDatos("inputs debe ser una matriz bidimensional.")
        n_nan = int(np.isnan(X).sum())
        n_inf = int(np.isinf(X).sum())
        if n_nan or n_inf:
            finitos = np.where(np.isfinite(X), X, np.nan)
            with np.errstate(all="ignore"):
                medias = np.nanmean(finitos, axis=0)
                maximos = np.nanmax(np.where(np.isfinite(X), X, -np.inf), axis=0)
                minimos = np.nanmin(np.where(np.isfinite(X), X, np.inf), axis=0)
            medias = np.where(np.isfinite(medias), medias, 0.0)
            maximos = np.where(np.isfinite(maximos), maximos, medias)
            minimos = np.where(np.isfinite(minimos), minimos, medias)
            filas, cols = np.where(np.isnan(X))
            X[filas, cols] = medias[cols]
            filas, cols = np.where(np.isposinf(X))
            X[filas, cols] = maximos[cols]
            filas, cols = np.where(np.isneginf(X))
            X[filas, cols] = minimos[cols]
        # Protección adicional contra magnitudes absurdas que desbordan el entrenamiento
        X = np.clip(X, -1e12, 1e12)
        return X, {"valores_nan_imputados": n_nan, "valores_inf_recortados": n_inf,
                   "estrategia": "media_columna / max-min finito"}


guardian = GuardiánGobernanzaSeguridad()
