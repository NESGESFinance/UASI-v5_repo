"""Base común de los agentes especializados por lenguaje de la capa JEV.

Cada agente es **determinista** y **nunca ejecuta código**: sólo inspecciona el payload
`datos` recibido de JEV, infiere un tipo abstracto por campo, lo traduce al sistema de
tipos de su lenguaje y genera el código *binding* como texto, junto con un resumen
específico del tipo de tarea. La misma entrada produce siempre la misma salida y, por
tanto, el mismo `hash_resultado`.
"""

from __future__ import annotations

import math
import re
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from uasi_v5.core.utilidades import json_canonico, sha256_hex
from uasi_v5.esquemas.jev import ResultadoAgenteJEV

# Clave reservada en `metadatos` donde el dispatcher inyecta contexto interno de UASI
# (integridad del ledger, estado de salud). Nunca procede de JEV: el dispatcher la sobrescribe.
CLAVE_CONTEXTO = "_contexto_uasi"

MAX_LONGITUD_ERROR = 500


# ---------------------------------------------------------------------------
# Inferencia de tipos abstractos
# ---------------------------------------------------------------------------
def inferir_tipo(valor: Any) -> str:
    """Tipo abstracto de un valor JSON: bool, int, float, str, null, object, any o list:<T>."""
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, int):
        return "int"
    if isinstance(valor, float):
        return "float"
    if isinstance(valor, str):
        return "str"
    if valor is None:
        return "null"
    if isinstance(valor, dict):
        return "object"
    if isinstance(valor, list | tuple):
        internos = {inferir_tipo(v) for v in valor}
        if not internos:
            return "list:any"
        if len(internos) == 1:
            return f"list:{internos.pop()}"
        if internos == {"int", "float"}:
            return "list:float"
        return "list:any"
    return "any"


def inferir_campos(datos: dict[str, Any]) -> dict[str, str]:
    """Tipo abstracto de cada campo de primer nivel, ordenado por nombre (determinismo)."""
    return {str(k): inferir_tipo(datos[k]) for k in sorted(datos, key=str)}


# ---------------------------------------------------------------------------
# Identificadores
# ---------------------------------------------------------------------------
def _tokens(nombre: str) -> list[str]:
    separado = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", nombre)
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", separado) if t]


def identificador(nombre: str, estilo: str, reservadas: frozenset[str] = frozenset()) -> str:
    """Convierte un nombre arbitrario en un identificador válido (snake, camel o pascal)."""
    tokens = _tokens(nombre) or ["campo"]
    if estilo == "snake":
        ident = "_".join(tokens)
    elif estilo == "camel":
        ident = tokens[0] + "".join(t.capitalize() for t in tokens[1:])
    else:
        ident = "".join(t.capitalize() for t in tokens)
    if ident[0].isdigit():
        ident = ("F" if estilo == "pascal" else "f_") + ident
    if ident in reservadas:
        ident += "_"
    return ident


def nombre_tipo(tipo_tarea: str) -> str:
    """Nombre del tipo generado: SINCRONIZACION_RWA → JevSincronizacionRwa."""
    return "Jev" + identificador(tipo_tarea, "pascal")


# ---------------------------------------------------------------------------
# Resumen específico por tipo de tarea (independiente del lenguaje)
# ---------------------------------------------------------------------------
def _es_numero(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _estadisticas(valores: list[Any]) -> dict[str, Any]:
    n = len(valores)
    return {"n": n, "min": min(valores), "max": max(valores), "media": round(sum(valores) / n, 10)}


def resumir_tarea(tipo_tarea: str, datos: dict[str, Any], contexto: dict[str, Any]) -> dict[str, Any]:
    """Resumen determinista de la tarea; usa el contexto inyectado por el dispatcher si existe."""
    if tipo_tarea == "INFERENCIA_MULTILENGUAJE":
        entradas = datos.get("inputs")
        return {
            "modelo": str(datos.get("modelo", "no_especificado")),
            "n_entradas": len(entradas) if isinstance(entradas, list) else 0,
            "n_campos": len(datos),
        }
    if tipo_tarea == "ANALISIS_DATOS":
        series = {
            str(k): _estadisticas(v)
            for k, v in sorted(datos.items(), key=lambda kv: str(kv[0]))
            if isinstance(v, list) and v and all(_es_numero(x) for x in v)
        }
        return {"series_numericas": series, "n_series": len(series)}
    if tipo_tarea == "EVALUACION_MODELO":
        bruto_m = datos.get("metricas")
        metricas: dict[Any, Any] = bruto_m if isinstance(bruto_m, dict) else {}
        salud = contexto.get("salud")
        resumen_salud: dict[str, Any] | str = "no_disponible"
        if isinstance(salud, dict):
            componentes = salud.get("componentes") or {}
            resumen_salud = {
                "estado": salud.get("estado"),
                "ledger_integro": (componentes.get("ledger") or {}).get("integro"),
            }
        return {
            "metricas": {str(k): v for k, v in sorted(metricas.items(), key=lambda kv: str(kv[0])) if _es_numero(v)},
            "salud_uasi": resumen_salud,
        }
    if tipo_tarea == "AUDITORIA_LEDGER":
        integridad = contexto.get("integridad_ledger")
        if not isinstance(integridad, dict):
            return {"integridad_ledger": "no_disponible"}
        return {"integridad_ledger": {"integro": integridad.get("integro"), "bloques": integridad.get("bloques")}}
    if tipo_tarea == "SINCRONIZACION_RWA":
        bruto_a = datos.get("activos")
        activos: list[Any] = bruto_a if isinstance(bruto_a, list) else []
        valores: list[float] = [a["valor"] for a in activos if isinstance(a, dict) and _es_numero(a.get("valor"))]
        return {
            "red": str(datos.get("red", "bitcoin")),
            "n_activos": len(activos),
            "valor_total": round(sum(valores), 10) if valores else 0,
        }
    if tipo_tarea == "VALIDACION_CRUZADA":
        entradas = datos.get("inputs")
        n = len(entradas) if isinstance(entradas, list) else 0
        k_bruto = datos.get("k_folds", 5)
        k = min(max(int(k_bruto) if _es_numero(k_bruto) else 5, 2), 100)
        k = min(k, n) if n >= 2 else k
        tamanos = [n // k + (1 if i < n % k else 0) for i in range(k)] if n >= 2 else []
        return {"k_folds": k, "n_muestras": n, "tamanos_fold": tamanos}
    # GENERACION_BINDINGS
    return {"n_campos": len(datos), "nombre_tipo": nombre_tipo(tipo_tarea)}


# ---------------------------------------------------------------------------
# Agente base
# ---------------------------------------------------------------------------
class AgenteJEVBase(ABC):
    """Agente especializado en un lenguaje. Las subclases sólo definen tipos y plantilla."""

    lenguaje: ClassVar[str] = ""
    extension: ClassVar[str] = ""
    estilo_campo: ClassVar[str] = "snake"
    reservadas: ClassVar[frozenset[str]] = frozenset()
    TIPOS: ClassVar[dict[str, str]] = {}
    TIPOS_GENERICOS: ClassVar[dict[str, str]] = {}

    def mapear_tipo(self, abstracto: str, en_generico: bool = False) -> str:
        """Traduce un tipo abstracto al tipo del lenguaje (recursivo para listas)."""
        if abstracto.startswith("list:"):
            return self.formato_lista(self.mapear_tipo(abstracto[5:], en_generico=True))
        if en_generico and abstracto in self.TIPOS_GENERICOS:
            return self.TIPOS_GENERICOS[abstracto]
        return self.TIPOS.get(abstracto, self.TIPOS["any"])

    @abstractmethod
    def formato_lista(self, tipo_interno: str) -> str:
        """Tipo lista/array del lenguaje para un tipo interno ya traducido."""

    @abstractmethod
    def generar_binding(self, nombre: str, campos: list[tuple[str, str, str]]) -> str:
        """Código binding como texto. `campos` = [(nombre_original, identificador, tipo_lenguaje)]."""

    def procesar(self, tipo_tarea: str, datos: dict[str, Any], metadatos: dict[str, Any]) -> dict[str, Any]:
        """Inferencia de tipos + binding + resumen de la tarea. No ejecuta código."""
        if not isinstance(datos, dict):
            raise TypeError("datos debe ser un objeto JSON")
        contexto = metadatos.get(CLAVE_CONTEXTO) if isinstance(metadatos, dict) else None
        abstractos = inferir_campos(datos)
        campos: list[tuple[str, str, str]] = []
        inferencia: dict[str, dict[str, str]] = {}
        usados: set[str] = set()
        for original, abstracto in abstractos.items():
            ident = identificador(original, self.estilo_campo, self.reservadas)
            base_ident, sufijo = ident, 2
            while ident in usados:  # "a-b" y "a_b" no deben colisionar
                ident, sufijo = f"{base_ident}{sufijo}", sufijo + 1
            usados.add(ident)
            tipo = self.mapear_tipo(abstracto)
            campos.append((original, ident, tipo))
            inferencia[original] = {"tipo_abstracto": abstracto, "tipo_lenguaje": tipo, "identificador": ident}
        nombre = nombre_tipo(tipo_tarea)
        return {
            "lenguaje": self.lenguaje,
            "tipo_tarea": tipo_tarea,
            "nombre_tipo": nombre,
            "archivo_sugerido": f"{nombre}{self.extension}",
            "inferencia_tipos": inferencia,
            "codigo_binding": self.generar_binding(nombre, campos),
            "resumen": resumir_tarea(tipo_tarea, datos, contexto if isinstance(contexto, dict) else {}),
        }


def ejecutar_agente(
    agente: AgenteJEVBase, tipo_tarea: str, datos: dict[str, Any], metadatos: dict[str, Any]
) -> ResultadoAgenteJEV:
    """Ejecuta un agente aislando cualquier excepción en un ResultadoAgenteJEV con estado 'error'."""
    inicio = time.perf_counter()
    try:
        resultado = agente.procesar(tipo_tarea, datos, metadatos)
    except Exception as exc:  # aislamiento: un agente no tumba al resto
        return ResultadoAgenteJEV(
            lenguaje=agente.lenguaje,
            estado="error",
            mensaje_error=f"{type(exc).__name__}: {exc}"[:MAX_LONGITUD_ERROR],
            duracion_ms=round((time.perf_counter() - inicio) * 1000, 3),
        )
    return ResultadoAgenteJEV(
        lenguaje=agente.lenguaje,
        estado="exito",
        datos_resultado=resultado,
        duracion_ms=round((time.perf_counter() - inicio) * 1000, 3),
        hash_resultado=sha256_hex(json_canonico(resultado).encode()),
    )
