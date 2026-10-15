#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
################################################################################
#                                                                              #
#   UASI v5 ENTERPRISE PRODUCTION                                              #
#   Sistema Unificado de Agentes de Aprendizaje con Supervisión Inteligente    #
#                                                                              #
#   Versión : 5.1.0-enterprise (revisión correctiva P1 + P2 + P3)              #
#   Fecha   : 2026-10-12                                                       #
#   Licencia: Uso interno                                                      #
#                                                                              #
################################################################################

RESUMEN DE CORRECCIONES APLICADAS (respecto al informe técnico ejecutivo)
-------------------------------------------------------------------------

P1 · CRÍTICO (inmediato)
  [P1-01] Autenticación JWT (Bearer) obligatoria en todos los endpoints salvo
          /api/v5/health. Si SECRET_JWT_KEY no está definida, el sistema
          "falla cerrado" (503) en lugar de quedar abierto.
  [P1-02] Todo el trabajo CPU-bound (entrenamiento, CV, verificación de
          cadena) se ejecuta en el threadpool (run_in_threadpool) para no
          bloquear el event loop de asyncio.
  [P1-03] Exclusión mutua en TODAS las escrituras a disco: threading.RLock
          (intra-proceso) + filelock.FileLock (inter-proceso / multi-worker).
          Escritura atómica mediante archivo temporal + os.replace.
  [P1-04] GuardiánGobernanzaSeguridad aplicado a TODOS los endpoints
          (v1, master, streaming) con allow-list de intenciones válidas
          + deny-list como defensa en profundidad.
  [P1-05] Sanitización NaN/±Inf/None en TODOS los flujos (incluido streaming)
          mediante imputación por columna documentada en la respuesta.
  [P1-06] Validación dimensional (matriz rectangular, len(inputs)==len(targets),
          coherencia con n_features del modelo persistido) y de binariedad.
  [P1-07] datetime.utcnow() sustituido por datetime.now(timezone.utc).
  [P1-08] Streaming con clases dinámicas (np.unique(y) + clases declaradas),
          sin [0, 1] hardcodeado.
  [P1-09] Rate limiting por IP con slowapi (RATE_LIMIT_PER_MINUTE, def. 30).
  [P1-10] Límite de payload (10 MB por defecto) aplicado a nivel ASGI.

P2 · CORTO PLAZO
  [P2-01] Métricas honestas: accuracy, precision, recall, F1 y ROC-AUC sobre
          hold-out estratificado cuando hay datos suficientes; si no, se
          etiquetan explícitamente como "entrenamiento_optimista".
  [P2-02] pickle sustituido por joblib + checksum SHA-256 (o HMAC-SHA256 si
          UASI_MODEL_HMAC_KEY está definida) verificado ANTES de deserializar.
  [P2-03] Red profunda: par pérdida/gradiente coherente (BCE + sigmoide,
          dz2 = (a2 - y)/n). Se reporta BCE, no MSE.
  [P2-04] Adam aplicado también a los biases y estado Adam completo
          (mw1, vw1, mw2, vw2, mb1, vb1, mb2, vb2, t) persistido.
  [P2-05] Early stopping real con instantánea y restauración de los mejores
          pesos (y del estado Adam asociado).
  [P2-06] CV segura en SVM: StratifiedKFold(min(3, min_clase)) con fallback a
          LeaveOneOut cuando hay < 3 muestras por clase.
  [P2-07] Tests pytest en test_uasi_v5.py.

P3 · ESTRATÉGICO
  [P3-01] Ledger hash-encadenado append-only (JSONL) con locks.
  [P3-02] Raíz de Merkle diaria (con separación de dominio hoja/nodo).
  [P3-03] Anclaje de la raíz diaria en Bitcoin L1 vía OpenTimestamps
          (calendarios públicos → transacción OP_RETURN agregada en Bitcoin).
          Se genera un archivo .ots por anclaje en PATH_WORKSPACE/ots_proofs/.
  [P3-04] Verificación de pruebas .ots: re-cálculo de la raíz desde el ledger,
          "upgrade" contra calendarios y contraste opcional de la cabecera de
          bloque Bitcoin con un explorador público (Esplora/Blockstream).
  [P3-05] Gobernanza declarativa (allow-list) y logging estructurado JSON.
  [P3-06] Metacognición operativa: la entropía de Shannon influye en el
          enrutamiento y el plan del orquestador semántico tiene efecto real.

NOTA SOBRE OPENTIMESTAMPS
  La librería oficial `opentimestamps` (python-opentimestamps) NO expone un
  módulo `opentimestamps.api`; la API real está en `opentimestamps.core.*` y
  `opentimestamps.calendar.RemoteCalendar`. Se intenta importar
  `opentimestamps.api` por compatibilidad y, si no existe, se usa la API real.
  Los calendarios públicos de OpenTimestamps anclan en Bitcoin MAINNET. Con
  BITCOIN_NETWORK=testnet se usa el explorador de testnet para la verificación
  y se requiere un calendario OTS propio en testnet (OTS_CALENDARS).

Arranque recomendado (ver DEPLOY_README.md):
  gunicorn uasi_v5_enterprise_production:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
"""

from __future__ import annotations

# =============================================================================
# 1. IMPORTS Y CONFIGURACIÓN
# =============================================================================
import asyncio
import copy
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sys
import threading
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple
from uuid import uuid4

import joblib
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from filelock import FileLock
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.concurrency import run_in_threadpool

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import (
    GridSearchCV,
    LeaveOneOut,
    StratifiedKFold,
    cross_val_score,
    train_test_split,
)
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

# --- Logging estructurado JSON (python-json-logger >= 3 usa pythonjsonlogger.json)
try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - versiones antiguas
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore

# --- OpenTimestamps (fallback gracioso si no está instalada) -----------------
OTS_DISPONIBLE = False
OTS_ERROR_IMPORT: Optional[str] = None
try:
    try:  # Compatibilidad: algunos entornos exponen un wrapper "api"
        import opentimestamps.api as ots_api  # type: ignore  # noqa: F401
    except ImportError:
        ots_api = None  # La librería oficial no incluye este módulo
    from opentimestamps.calendar import (
        DEFAULT_CALENDAR_WHITELIST,
        CommitmentNotFoundError,
        RemoteCalendar,
    )
    from opentimestamps.core.notary import (
        BitcoinBlockHeaderAttestation,
        PendingAttestation,
    )
    from opentimestamps.core.op import OpAppend, OpSHA256
    from opentimestamps.core.serialize import (
        StreamDeserializationContext,
        StreamSerializationContext,
    )
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    OTS_DISPONIBLE = True
except Exception as _exc:  # pragma: no cover - depende del entorno
    OTS_ERROR_IMPORT = f"{type(_exc).__name__}: {_exc}"

try:
    import httpx  # Para contrastar cabeceras de bloque con un explorador público

    HTTPX_DISPONIBLE = True
except ImportError:  # pragma: no cover
    HTTPX_DISPONIBLE = False


def _env_int(nombre: str, defecto: int) -> int:
    try:
        return int(os.getenv(nombre, str(defecto)))
    except ValueError:
        return defecto


def _env_float(nombre: str, defecto: float) -> float:
    try:
        return float(os.getenv(nombre, str(defecto)))
    except ValueError:
        return defecto


# --- Constantes de configuración (todas sobreescribibles por entorno) ---------
VERSION_UASI = "5.1.0-enterprise"
UASI_ENV = os.getenv("UASI_ENV", "development").lower()
PATH_WORKSPACE = os.path.abspath(os.getenv("PATH_WORKSPACE", "./uasi_workspace"))
PATH_MODELOS = os.path.join(PATH_WORKSPACE, "modelos")
PATH_LEDGER = os.path.join(PATH_WORKSPACE, "ledger")
PATH_OTS = os.path.join(PATH_WORKSPACE, "ots_proofs")
ARCHIVO_LEDGER = os.path.join(PATH_LEDGER, "ledger_auditoria.jsonl")

SECRET_JWT_KEY = os.getenv("SECRET_JWT_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("JWT_ISSUER", "uasi-v5")
JWT_EXP_MINUTES = _env_int("JWT_EXP_MINUTES", 60)

RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 30)
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
MAX_PAYLOAD_BYTES = _env_int("MAX_PAYLOAD_BYTES", 10 * 1024 * 1024)  # 10 MB
MAX_MUESTRAS = _env_int("UASI_MAX_MUESTRAS", 50_000)
MAX_FEATURES = _env_int("UASI_MAX_FEATURES", 512)

UASI_MODEL_HMAC_KEY = os.getenv("UASI_MODEL_HMAC_KEY", "")

BITCOIN_NETWORK = os.getenv("BITCOIN_NETWORK", "mainnet").lower()
if BITCOIN_NETWORK not in ("mainnet", "testnet"):
    BITCOIN_NETWORK = "mainnet"
_CALENDARIOS_DEFECTO = (
    "https://a.pool.opentimestamps.org,"
    "https://b.pool.opentimestamps.org,"
    "https://a.pool.eternitywall.com"
)
OTS_CALENDARS = [u.strip() for u in os.getenv("OTS_CALENDARS", _CALENDARIOS_DEFECTO).split(",") if u.strip()]
OTS_MIN_CALENDARS = _env_int("OTS_MIN_CALENDARS", 1)
OTS_TIMEOUT_SECONDS = _env_int("OTS_TIMEOUT_SECONDS", 10)
ESPLORA_API_URL = os.getenv(
    "ESPLORA_API_URL",
    "https://blockstream.info/testnet/api" if BITCOIN_NETWORK == "testnet" else "https://blockstream.info/api",
)

# Umbrales metacognitivos
UMBRAL_ENTROPIA_DESBALANCE = _env_float("UASI_UMBRAL_ENTROPIA_DESBALANCE", 0.85)
UMBRAL_ENTROPIA_FEATURES = _env_float("UASI_UMBRAL_ENTROPIA_FEATURES", 0.90)

# --- Logging -----------------------------------------------------------------
logger = logging.getLogger("uasi_v5")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "nivel"},
        )
    )
    logger.addHandler(_handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False


def ahora_utc() -> datetime:
    """[P1-07] Única fuente de tiempo del sistema: timezone-aware en UTC."""
    return datetime.now(timezone.utc)


def json_canonico(obj: Any) -> str:
    """Serialización JSON determinista (claves ordenadas, sin espacios)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# =============================================================================
# Excepciones de dominio (se traducen a códigos HTTP en los handlers)
# =============================================================================
class ErrorValidacionDatos(ValueError):
    """Datos de entrada incoherentes → HTTP 422."""


class ErrorGobernanza(PermissionError):
    """Petición rechazada por la política de gobernanza → HTTP 403."""


class IntegridadModeloError(RuntimeError):
    """Checksum de un artefacto no coincide → HTTP 500 (posible manipulación)."""


class ConflictoClasesStreaming(RuntimeError):
    """Clases nuevas incompatibles con el modelo online persistido → HTTP 409."""


# =============================================================================
# 2. AUTENTICACIÓN JWT  [P1-01]
# =============================================================================
api_key_header = APIKeyHeader(
    name="Authorization",
    auto_error=False,
    description="Cabecera 'Authorization: Bearer <JWT>'",
)


def emitir_token(sujeto: str, minutos: int = JWT_EXP_MINUTES, alcance: str = "uasi:full") -> str:
    """Emite un JWT firmado (HS256 por defecto) con iss/sub/iat/exp/jti."""
    if not SECRET_JWT_KEY:
        raise RuntimeError("SECRET_JWT_KEY no configurada")
    ahora = ahora_utc()
    claims = {
        "iss": JWT_ISSUER,
        "sub": sujeto,
        "scope": alcance,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=minutos)).timestamp()),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, SECRET_JWT_KEY, algorithm=JWT_ALGORITHM)


async def require_auth(authorization: Optional[str] = Security(api_key_header)) -> Dict[str, Any]:
    """
    Dependencia FastAPI (equivalente idiomático a un decorador) que valida el JWT.
    - Falla CERRADO (503) si el servidor no tiene SECRET_JWT_KEY.
    - 401 si falta la cabecera, el esquema no es Bearer, la firma es inválida,
      el emisor no coincide o el token ha expirado.
    """
    if not SECRET_JWT_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticación no configurada en el servidor (SECRET_JWT_KEY ausente).",
        )
    cabecera_auth = {"WWW-Authenticate": "Bearer"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token Bearer requerido.", headers=cabecera_auth)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(
            token,
            SECRET_JWT_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.", headers=cabecera_auth)
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido.", headers=cabecera_auth)
    return claims


# =============================================================================
# 3. RATE LIMITING  [P1-09]  y  LÍMITE DE PAYLOAD  [P1-10]
# =============================================================================
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{RATE_LIMIT_PER_MINUTE}/minute"],
    storage_uri=RATE_LIMIT_STORAGE_URI,  # usar redis:// en multi-worker para límite global
)


class LimiteTamanoPayloadMiddleware:
    """
    Middleware ASGI puro: lee el cuerpo hasta MAX_PAYLOAD_BYTES y lo re-inyecta.
    Rechaza con 413 tanto por Content-Length declarado como por bytes reales
    recibidos (protege también frente a chunked transfer sin Content-Length).
    """

    def __init__(self, app: Any, max_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _rechazar(self, send: Callable) -> None:
        cuerpo = json.dumps({"detail": f"Payload excede el límite de {self.max_bytes} bytes."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(cuerpo)).encode())]})
        await send({"type": "http.response.body", "body": cuerpo})

    async def __call__(self, scope: Dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        cabeceras = dict(scope.get("headers") or [])
        declarado = cabeceras.get(b"content-length")
        if declarado is not None:
            try:
                if int(declarado) > self.max_bytes:
                    await self._rechazar(send)
                    return
            except ValueError:
                await self._rechazar(send)
                return
        trozos: List[bytes] = []
        total = 0
        while True:
            mensaje = await receive()
            if mensaje["type"] == "http.disconnect":
                return
            cuerpo = mensaje.get("body", b"")
            total += len(cuerpo)
            if total > self.max_bytes:
                await self._rechazar(send)
                return
            trozos.append(cuerpo)
            if not mensaje.get("more_body", False):
                break
        cuerpo_completo = b"".join(trozos)
        entregado = False

        async def receive_reinyectado() -> Dict:
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": cuerpo_completo, "more_body": False}
            return await receive()

        await self.app(scope, receive_reinyectado, send)


# =============================================================================
# 4. MODELOS PYDANTIC  [P1-06] [P1-07]
# =============================================================================
FilaNumerica = List[Optional[float]]  # None/NaN/Inf se aceptan y se sanitizan después


def _validar_matriz(v: List[FilaNumerica]) -> List[FilaNumerica]:
    if not v:
        raise ValueError("inputs no puede estar vacío.")
    if len(v) > MAX_MUESTRAS:
        raise ValueError(f"inputs excede el máximo de {MAX_MUESTRAS} muestras.")
    n_cols = len(v[0])
    if n_cols == 0:
        raise ValueError("Cada muestra debe tener al menos 1 característica.")
    if n_cols > MAX_FEATURES:
        raise ValueError(f"inputs excede el máximo de {MAX_FEATURES} características.")
    for i, fila in enumerate(v):
        if len(fila) != n_cols:
            raise ValueError(f"Matriz no rectangular: la fila {i} tiene {len(fila)} columnas, se esperaban {n_cols}.")
    return v


class _BaseSolicitud(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_solicitud: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=ahora_utc)
    metadatos_contexto: Dict[str, Any] = Field(default_factory=dict)


class SolicitudProcesamientoV1(_BaseSolicitud):
    """Endpoint legacy v1: entrenamiento de agentes clásicos (clasificación binaria)."""

    tipo_agente: Literal["red_neuronal", "svm", "arbol_decision"]
    intencion: str = Field(default="clasificacion", max_length=64)
    inputs: List[FilaNumerica]
    targets: List[int]

    @field_validator("inputs")
    @classmethod
    def validar_inputs(cls, v: List[FilaNumerica]) -> List[FilaNumerica]:
        return _validar_matriz(v)

    @model_validator(mode="after")
    def validar_coherencia(self) -> "SolicitudProcesamientoV1":
        if len(self.inputs) != len(self.targets):
            raise ValueError(f"len(inputs)={len(self.inputs)} != len(targets)={len(self.targets)}.")
        invalidos = sorted({t for t in self.targets if t not in (0, 1)})
        if invalidos:
            raise ValueError(f"targets debe ser binario (0/1). Valores inválidos: {invalidos[:10]}.")
        if len(set(self.targets)) < 2:
            raise ValueError("targets debe contener ambas clases (0 y 1) para entrenar.")
        return self


class SolicitudMaestraV5(_BaseSolicitud):
    """Endpoint maestro: el orquestador semántico decide la ruta operativa."""

    prompt_intencion: str = Field(..., min_length=3, max_length=2000)
    inputs: Optional[List[FilaNumerica]] = None
    targets: Optional[List[int]] = None
    clases_declaradas: Optional[List[int]] = None

    @field_validator("inputs")
    @classmethod
    def validar_inputs(cls, v: Optional[List[FilaNumerica]]) -> Optional[List[FilaNumerica]]:
        return None if v is None else _validar_matriz(v)

    @model_validator(mode="after")
    def validar_coherencia(self) -> "SolicitudMaestraV5":
        if (self.inputs is None) != (self.targets is None):
            raise ValueError("inputs y targets deben enviarse juntos.")
        if self.inputs is not None:
            if len(self.inputs) != len(self.targets):  # type: ignore[arg-type]
                raise ValueError(f"len(inputs)={len(self.inputs)} != len(targets)={len(self.targets)}.")  # type: ignore[arg-type]
            if any(t < 0 for t in self.targets):  # type: ignore[union-attr]
                raise ValueError("targets debe contener enteros no negativos.")
        return self


class SolicitudStreaming(_BaseSolicitud):
    """Ingesta online: admite multiclase (clases dinámicas)."""

    intencion: str = Field(default="aprendizaje_online", max_length=64)
    inputs: List[FilaNumerica]
    targets: List[int]
    clases_declaradas: Optional[List[int]] = Field(default=None, description="Universo de clases conocido a priori.")
    reiniciar_modelo: bool = False

    @field_validator("inputs")
    @classmethod
    def validar_inputs(cls, v: List[FilaNumerica]) -> List[FilaNumerica]:
        return _validar_matriz(v)

    @model_validator(mode="after")
    def validar_coherencia(self) -> "SolicitudStreaming":
        if len(self.inputs) != len(self.targets):
            raise ValueError(f"len(inputs)={len(self.inputs)} != len(targets)={len(self.targets)}.")
        if not self.targets or any(t < 0 for t in self.targets):
            raise ValueError("targets debe contener enteros no negativos y no estar vacío.")
        if self.clases_declaradas is not None and any(c < 0 for c in self.clases_declaradas):
            raise ValueError("clases_declaradas debe contener enteros no negativos.")
        return self


# =============================================================================
# 5. GOBERNANZA Y GUARDRAILS  [P1-04] [P1-05] [P3-05]
# =============================================================================
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


# =============================================================================
# 6. PERSISTENCIA SEGURA: joblib + checksum + FileLock  [P1-03] [P2-02]
# =============================================================================
_LOCK_PROCESO = threading.RLock()  # Exclusión intra-proceso (complementa a FileLock)


def _digest_archivo(path: str) -> str:
    """SHA-256 (o HMAC-SHA256 si hay clave) del contenido del archivo."""
    if UASI_MODEL_HMAC_KEY:
        h = hmac.new(UASI_MODEL_HMAC_KEY.encode(), digestmod=hashlib.sha256)
        prefijo = "hmac-sha256:"
    else:
        h = hashlib.sha256()
        prefijo = "sha256:"
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return prefijo + h.hexdigest()


def _escritura_atomica_texto(path: str, contenido: str) -> None:
    tmp = f"{path}.tmp-{uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contenido)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def guardar_modelo_seguro(obj: Any, path: str) -> str:
    """
    Serializa con joblib de forma atómica y escribe `<path>.sha256` al lado.
    Todo bajo FileLock (multi-proceso) + RLock (multi-hilo).
    Devuelve el digest escrito.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK_PROCESO, FileLock(f"{path}.lock", timeout=60):
        tmp = f"{path}.tmp-{uuid4().hex}"
        joblib.dump(obj, tmp, compress=3)
        digest = _digest_archivo(tmp)
        os.replace(tmp, path)
        _escritura_atomica_texto(f"{path}.sha256", digest + "\n")
    return digest


def verificar_checksum(path: str) -> Tuple[bool, str]:
    """Comprueba el checksum sin deserializar. Devuelve (ok, motivo)."""
    ruta_sha = f"{path}.sha256"
    if not os.path.exists(path):
        return False, "inexistente"
    if not os.path.exists(ruta_sha):
        return False, "checksum_ausente"
    with open(ruta_sha, "r", encoding="utf-8") as f:
        esperado = f.read().strip()
    real = _digest_archivo(path)
    if not hmac.compare_digest(esperado, real):
        return False, "checksum_no_coincide"
    return True, "ok"


def cargar_modelo_seguro(path: str) -> Optional[Any]:
    """
    Verifica el checksum ANTES de deserializar (joblib usa pickle internamente,
    así que nunca se carga un artefacto cuya integridad no esté acreditada).
    Devuelve None si el modelo no existe; lanza IntegridadModeloError si falla.
    """
    if not os.path.exists(path):
        return None
    with _LOCK_PROCESO, FileLock(f"{path}.lock", timeout=60):
        ok, motivo = verificar_checksum(path)
        if not ok:
            logger.error("integridad_modelo_fallida", extra={"ruta": path, "motivo": motivo})
            raise IntegridadModeloError(f"Integridad del artefacto '{os.path.basename(path)}' comprometida: {motivo}.")
        return joblib.load(path)


def ruta_modelo(nombre: str) -> str:
    return os.path.join(PATH_MODELOS, f"{nombre}.joblib")


# =============================================================================
# Utilidades de evaluación honesta  [P2-01]
# =============================================================================
def calcular_metricas_clasificacion(
    y_true: np.ndarray, y_pred: np.ndarray, y_score: Optional[np.ndarray] = None
) -> Dict[str, Any]:
    """accuracy, precision, recall, F1 y ROC-AUC (binario) o macro (multiclase)."""
    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)
    clases = np.unique(np.concatenate([y_true, y_pred]))
    binario = set(clases.tolist()).issubset({0, 1})
    promedio = "binary" if binario else "macro"
    metricas: Dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "precision": round(float(precision_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "recall": round(float(recall_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "f1_score": round(float(f1_score(y_true, y_pred, average=promedio, zero_division=0)), 6),
        "promedio": promedio,
        "n_evaluadas": int(len(y_true)),
    }
    if y_score is not None and binario and len(np.unique(y_true)) == 2:
        metricas["auc_roc"] = round(float(roc_auc_score(y_true, y_score)), 6)
    else:
        metricas["auc_roc"] = None
        metricas["auc_roc_nota"] = "No calculable (una sola clase en evaluación, multiclase o sin puntuaciones)."
    return metricas


def dividir_holdout(X: np.ndarray, y: np.ndarray, semilla: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    """Hold-out estratificado 25 % si hay datos suficientes; si no, evaluación optimista explícita."""
    conteos = np.bincount(y)
    min_clase = int(conteos[conteos > 0].min())
    n_clases = int((conteos > 0).sum())
    n_test = int(math.ceil(0.25 * len(y)))
    if len(y) >= 10 and min_clase >= 2 and n_test >= n_clases and len(y) - n_test >= n_clases:
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, stratify=y, random_state=semilla)
        return X_tr, X_te, y_tr, y_te, "holdout_estratificado_25pct"
    return X, X, y, y, "entrenamiento_optimista (datos insuficientes para hold-out)"


def puntuaciones_modelo(modelo: Any, X: np.ndarray) -> Optional[np.ndarray]:
    """Puntuación continua para ROC-AUC (probabilidad de clase 1 o decision_function)."""
    try:
        if hasattr(modelo, "predict_proba"):
            proba = modelo.predict_proba(X)
            if proba.ndim == 2 and proba.shape[1] == 2:
                return proba[:, 1]
        if hasattr(modelo, "decision_function"):
            dec = modelo.decision_function(X)
            if np.ndim(dec) == 1:
                return dec
    except Exception:  # pragma: no cover - modelos sin soporte
        return None
    return None


def construir_cv_seguro(y: np.ndarray, semilla: int) -> Optional[Any]:
    """
    [P2-06] cv = StratifiedKFold(min(3, min(np.bincount(y)))) si cada clase tiene
    ≥ 3 muestras; LeaveOneOut si alguna clase tiene 2 (cada fold conserva
    ambas clases); None (sin CV) si alguna clase tiene 1 sola muestra.
    """
    conteos = np.bincount(y)
    min_clase = int(conteos[conteos > 0].min())
    if min_clase >= 3:
        return StratifiedKFold(n_splits=min(3, min_clase), shuffle=True, random_state=semilla)
    if min_clase == 2:
        return LeaveOneOut()
    return None


def semilla_desde_contexto(meta: Dict[str, Any]) -> int:
    """metadatos_contexto['semilla'] controla la reproducibilidad (uso real de los metadatos)."""
    try:
        return int(meta.get("semilla", 42)) % (2**32 - 1)
    except (TypeError, ValueError):
        return 42


def entrenar_y_evaluar_sklearn(fabrica: Callable[[], Any], X: np.ndarray, y: np.ndarray, semilla: int) -> Tuple[Any, Dict[str, Any]]:
    """Evalúa en hold-out y después reentrena con TODOS los datos para el modelo final."""
    X_tr, X_te, y_tr, y_te, conjunto = dividir_holdout(X, y, semilla)
    modelo_eval = fabrica().fit(X_tr, y_tr)
    metricas = calcular_metricas_clasificacion(y_te, modelo_eval.predict(X_te), puntuaciones_modelo(modelo_eval, X_te))
    metricas["conjunto_evaluacion"] = conjunto
    modelo_final = fabrica().fit(X, y) if conjunto.startswith("holdout") else modelo_eval
    return modelo_final, metricas


# =============================================================================
# 7-9. AGENTES LEGACY (v1)
# =============================================================================
class _AgenteLegacyBase:
    nombre_modelo = "legacy"

    def __init__(self) -> None:
        self.ruta = ruta_modelo(self.nombre_modelo)

    def _fabrica(self, semilla: int) -> Callable[[], Any]:  # pragma: no cover - abstracto
        raise NotImplementedError

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1 if isinstance(previo, dict) else 1
            modelo, metricas = entrenar_y_evaluar_sklearn(self._fabrica(semilla), X, y, semilla)
            guardar_modelo_seguro(
                {"modelo": modelo, "version": version, "n_features": int(X.shape[1]),
                 "entrenado_en": ahora_utc().isoformat()},
                self.ruta,
            )
        return {"agente": self.nombre_modelo, "version_modelo": version, "metricas": metricas}


class AgenteRedNeuronalLegacy(_AgenteLegacyBase):
    """MLP clásico de scikit-learn con persistencia joblib+checksum."""

    nombre_modelo = "red_neuronal_legacy"

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        from sklearn.pipeline import make_pipeline

        return lambda: make_pipeline(
            StandardScaler(),
            MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=800, early_stopping=False, random_state=semilla),
        )


class AgenteSVMLegacy(_AgenteLegacyBase):
    """SVM con GridSearch y CV estratificada segura [P2-06]."""

    nombre_modelo = "svm_legacy"

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        self._cv = construir_cv_seguro(y, semilla)
        resultado = super().entrenar(X, y, semilla)
        resultado["estrategia_cv"] = type(self._cv).__name__ if self._cv is not None else "sin_cv (clase con 1 muestra)"
        return resultado

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        from sklearn.pipeline import make_pipeline

        cv = self._cv

        def construir() -> Any:
            base = make_pipeline(StandardScaler(), SVC(kernel="rbf", random_state=semilla))
            if cv is None:
                return base
            return _GridSearchSeguro(base, {"svc__C": [0.1, 1.0, 10.0], "svc__gamma": ["scale", 0.1]}, cv)

        return construir


class _GridSearchSeguro:
    """
    Envoltorio que recalcula la CV segura sobre el subconjunto con el que se
    entrena (el hold-out reduce muestras por clase y la CV global podría fallar).
    """

    def __init__(self, estimador: Any, rejilla: Dict[str, List[Any]], cv: Any) -> None:
        self.estimador, self.rejilla, self.cv = estimador, rejilla, cv
        self.modelo_: Any = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_GridSearchSeguro":
        cv = construir_cv_seguro(y, 42)
        if cv is None:
            self.modelo_ = copy.deepcopy(self.estimador).fit(X, y)
        else:
            gs = GridSearchCV(copy.deepcopy(self.estimador), self.rejilla, cv=cv, scoring="accuracy", n_jobs=1)
            self.modelo_ = gs.fit(X, y).best_estimator_
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.modelo_.predict(X)

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return self.modelo_.decision_function(X)


class AgenteArbolDecisionLegacy(_AgenteLegacyBase):
    """Árbol de decisión interpretable (profundidad acotada para evitar sobreajuste)."""

    nombre_modelo = "arbol_legacy"

    def _fabrica(self, semilla: int) -> Callable[[], Any]:
        return lambda: DecisionTreeClassifier(max_depth=5, min_samples_leaf=2, random_state=semilla)


# =============================================================================
# 10. MOTOR DE STREAMING CONTINUO  [P1-05] [P1-06] [P1-08]
# =============================================================================
class MotorStreamingContinuo:
    """
    Aprendizaje online con SGDClassifier.partial_fit + StandardScaler.partial_fit.
    - Clases dinámicas: universo = np.unique(y) ∪ clases_declaradas.
    - SGD no admite añadir clases tras inicializarse: si llegan clases nuevas se
      responde 409 salvo que se pida reiniciar_modelo=True.
    - Métricas prequential: se evalúa el lote ANTES de aprender de él
      (estimación honesta) y también después ("accuracy_muestra_actual").
    """

    def __init__(self) -> None:
        self.ruta = ruta_modelo("streaming_sgd")

    def ingerir(self, X: np.ndarray, y: np.ndarray, clases_declaradas: Optional[List[int]],
                reiniciar: bool, semilla: int = 42) -> Dict[str, Any]:
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            estado = None if reiniciar else cargar_modelo_seguro(self.ruta)
            clases_lote = set(np.unique(y).tolist()) | set(clases_declaradas or [])
            metricas_previas: Optional[Dict[str, Any]] = None
            if estado is None:
                clases = np.array(sorted(clases_lote), dtype=int)
                if len(clases) < 2:
                    raise ErrorValidacionDatos(
                        "La inicialización del modelo online requiere ≥ 2 clases "
                        "(en el lote o en clases_declaradas)."
                    )
                estado = {
                    "scaler": StandardScaler(),
                    "modelo": SGDClassifier(loss="log_loss", alpha=1e-4, random_state=semilla),
                    "clases": clases,
                    "n_features": int(X.shape[1]),
                    "muestras_vistas": 0,
                    "lotes": 0,
                    "version": 0,
                }
            else:
                if X.shape[1] != estado["n_features"]:
                    raise ErrorValidacionDatos(
                        f"Dimensionalidad incompatible: el modelo online espera {estado['n_features']} "
                        f"características y el lote tiene {X.shape[1]}."
                    )
                nuevas = sorted(clases_lote - set(estado["clases"].tolist()))
                if nuevas:
                    raise ConflictoClasesStreaming(
                        f"Clases nuevas {nuevas} no presentes en el modelo online (clases={estado['clases'].tolist()}). "
                        "Envíe reiniciar_modelo=true con clases_declaradas para reconstruirlo."
                    )
                # Evaluación prequential (antes de aprender del lote)
                Xs_prev = estado["scaler"].transform(X)
                metricas_previas = calcular_metricas_clasificacion(y, estado["modelo"].predict(Xs_prev))

            estado["scaler"].partial_fit(X)
            Xs = estado["scaler"].transform(X)
            estado["modelo"].partial_fit(Xs, y, classes=estado["clases"])
            estado["muestras_vistas"] += int(len(y))
            estado["lotes"] += 1
            estado["version"] += 1
            estado["actualizado_en"] = ahora_utc().isoformat()
            acc_actual = float(accuracy_score(y, estado["modelo"].predict(Xs)))
            guardar_modelo_seguro(estado, self.ruta)

        return {
            "agente": "streaming_sgd",
            "version_modelo": estado["version"],
            "clases_modelo": estado["clases"].tolist(),
            "muestras_vistas_total": estado["muestras_vistas"],
            "lotes_procesados": estado["lotes"],
            "metricas": {
                "accuracy_muestra_actual": round(acc_actual, 6),
                "accuracy_muestra_actual_nota": "Medida sobre el mismo lote tras aprender de él (optimista).",
                "prequential_antes_de_actualizar": metricas_previas,
            },
        }


# =============================================================================
# 11. AGENTE CEREBRAL PROFUNDO (red numpy 2 capas)  [P2-03] [P2-04] [P2-05]
# =============================================================================
class AgenteCerebralProfundo:
    """
    Red densa X → tanh(16) → sigmoide, entrenada con Adam sobre BCE.

    Coherencia matemática (P2-03):
        L    = -mean( y·log(a2+ε) + (1-y)·log(1-a2+ε) )
        dL/dz2 = (a2 - y) / n          ← gradiente exacto de BCE con sigmoide
    Todos los parámetros (w1, b1, w2, b2) se actualizan con Adam (P2-04) y el
    estado de Adam completo se persiste para continuar el entrenamiento en
    llamadas posteriores (warm start). Early stopping real con restauración de
    la instantánea con mejor pérdida de validación (P2-05).
    """

    PARAMS = ("w1", "b1", "w2", "b2")

    def __init__(self, n_ocultas: int = 16, lr: float = 0.01, epocas_max: int = 1500, paciencia: int = 60,
                 l2: float = 1e-4, beta1: float = 0.9, beta2: float = 0.999, eps_adam: float = 1e-8) -> None:
        self.ruta = ruta_modelo("cerebral_profundo")
        self.n_ocultas, self.lr, self.epocas_max, self.paciencia = n_ocultas, lr, epocas_max, paciencia
        self.l2, self.beta1, self.beta2, self.eps_adam = l2, beta1, beta2, eps_adam
        self.eps_log = 1e-12

    # --- Inicialización / estado --------------------------------------------
    def _estado_inicial(self, n_features: int, rng: np.random.Generator) -> Dict[str, Any]:
        h = self.n_ocultas
        estado: Dict[str, Any] = {
            "w1": rng.normal(0, np.sqrt(1.0 / n_features), (n_features, h)),
            "b1": np.zeros((1, h)),
            "w2": rng.normal(0, np.sqrt(1.0 / h), (h, 1)),
            "b2": np.zeros((1, 1)),
            "t": 0,
            "n_features": n_features,
            "n_ocultas": h,
            "version": 0,
        }
        for p in self.PARAMS:  # mw1, vw1, mb1, vb1, mw2, vw2, mb2, vb2
            estado[f"m{p}"] = np.zeros_like(estado[p])
            estado[f"v{p}"] = np.zeros_like(estado[p])
        return estado

    @staticmethod
    def _sigmoide(z: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))

    def _forward(self, X: np.ndarray, e: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        a1 = np.tanh(X @ e["w1"] + e["b1"])
        a2 = self._sigmoide(a1 @ e["w2"] + e["b2"])
        return a1, a2

    def _bce(self, a2: np.ndarray, y: np.ndarray) -> float:
        return float(-np.mean(y * np.log(a2 + self.eps_log) + (1 - y) * np.log(1 - a2 + self.eps_log)))

    def _paso_adam(self, X: np.ndarray, y: np.ndarray, e: Dict[str, Any]) -> None:
        n = len(X)
        a1, a2 = self._forward(X, e)
        dz2 = (a2 - y) / n                                 # BCE + sigmoide
        grads = {
            "w2": a1.T @ dz2 + self.l2 * e["w2"],
            "b2": dz2.sum(axis=0, keepdims=True),
        }
        dz1 = (dz2 @ e["w2"].T) * (1.0 - a1 ** 2)          # derivada de tanh
        grads["w1"] = X.T @ dz1 + self.l2 * e["w1"]
        grads["b1"] = dz1.sum(axis=0, keepdims=True)
        e["t"] += 1
        t = e["t"]
        for p in self.PARAMS:                               # Adam también en biases
            g = grads[p]
            e[f"m{p}"] = self.beta1 * e[f"m{p}"] + (1 - self.beta1) * g
            e[f"v{p}"] = self.beta2 * e[f"v{p}"] + (1 - self.beta2) * (g ** 2)
            m_hat = e[f"m{p}"] / (1 - self.beta1 ** t)
            v_hat = e[f"v{p}"] / (1 - self.beta2 ** t)
            e[p] = e[p] - self.lr * m_hat / (np.sqrt(v_hat) + self.eps_adam)

    def predecir_proba(self, X: np.ndarray, e: Dict[str, Any]) -> np.ndarray:
        Xn = (X - e["media"]) / e["desv"]
        return self._forward(Xn, e)[1].ravel()

    # --- Entrenamiento ------------------------------------------------------
    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        if not set(np.unique(y).tolist()).issubset({0, 1}) or len(np.unique(y)) < 2:
            raise ErrorValidacionDatos("El agente profundo requiere targets binarios con ambas clases presentes.")
        rng = np.random.default_rng(semilla)
        with FileLock(f"{self.ruta}.train.lock", timeout=600):
            previo = cargar_modelo_seguro(self.ruta)
            warm_start = bool(previo is not None and previo.get("n_features") == X.shape[1]
                              and previo.get("n_ocultas") == self.n_ocultas)
            if warm_start:
                e = previo
            else:
                e = self._estado_inicial(X.shape[1], rng)
                e["media"] = X.mean(axis=0)
                desv = X.std(axis=0)
                e["desv"] = np.where(desv > 1e-12, desv, 1.0)

            X_tr, X_te, y_tr, y_te, conjunto = dividir_holdout(X, y, semilla)
            # Validación interna para early stopping (sin tocar el conjunto de test)
            usa_val = len(y_tr) >= 30 and np.bincount(y_tr).min() >= 4
            if usa_val:
                X_fit, X_val, y_fit, y_val = train_test_split(X_tr, y_tr, test_size=0.15, stratify=y_tr, random_state=semilla)
            else:
                X_fit, X_val, y_fit, y_val = X_tr, X_tr, y_tr, y_tr
            norm = lambda A: (A - e["media"]) / e["desv"]  # noqa: E731
            Xf, Xv = norm(X_fit), norm(X_val)
            yf, yv = y_fit.reshape(-1, 1).astype(float), y_val.reshape(-1, 1).astype(float)

            mejor_loss, mejor_epoca, sin_mejora = float("inf"), 0, 0
            mejor_estado: Dict[str, Any] = {}
            epoca = 0
            for epoca in range(1, self.epocas_max + 1):
                self._paso_adam(Xf, yf, e)
                loss_monitor = self._bce(self._forward(Xv, e)[1], yv)
                if loss_monitor < mejor_loss - 1e-6:
                    mejor_loss, mejor_epoca, sin_mejora = loss_monitor, epoca, 0
                    # Instantánea de pesos + estado Adam (best_w1, best_b1, best_w2, best_b2, ...)
                    mejor_estado = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in e.items()}
                else:
                    sin_mejora += 1
                    if sin_mejora >= self.paciencia:
                        break
            detenido_temprano = sin_mejora >= self.paciencia
            e.update(mejor_estado)  # restauración de los mejores pesos
            e["version"] = int(e.get("version", 0)) + 1
            e["entrenado_en"] = ahora_utc().isoformat()

            bce_train = self._bce(self._forward(Xf, e)[1], yf)
            proba_te = self.predecir_proba(X_te, e)
            metricas = calcular_metricas_clasificacion(y_te, (proba_te >= 0.5).astype(int), proba_te)
            metricas.update({
                "bce_loss_final": round(bce_train, 6),
                "bce_loss_monitor_mejor": round(mejor_loss, 6),
                "bce_loss_evaluacion": round(self._bce(proba_te.reshape(-1, 1), y_te.reshape(-1, 1).astype(float)), 6),
                "conjunto_evaluacion": conjunto,
                "monitor_early_stopping": "validacion_interna_15pct" if usa_val else "entrenamiento",
            })
            guardar_modelo_seguro(e, self.ruta)

        return {
            "agente": "cerebral_profundo",
            "version_modelo": e["version"],
            "warm_start_desde_estado_persistido": warm_start,
            "entrenamiento": {"epocas_ejecutadas": epoca, "mejor_epoca": mejor_epoca,
                              "early_stopping_activado": detenido_temprano, "paso_adam_t": int(e["t"])},
            "metricas": metricas,
        }


# =============================================================================
# 12. AGENTE VECTORIAL EVOLUTIVO (SVM + búsqueda evolutiva)
# =============================================================================
class AgenteVectorialEvolutivo:
    """
    Algoritmo evolutivo sobre (log10 C, log10 gamma) de un SVM RBF.
    Fitness = accuracy media de CV segura (reportada como `accuracy_cv`,
    NO como "precision"). Además precision/recall/F1 sobre el conjunto de
    entrenamiento con los mejores parámetros (etiquetado como tal) y métricas
    de hold-out cuando hay datos suficientes.
    """

    def __init__(self, poblacion: int = 8, generaciones: int = 5) -> None:
        self.ruta = ruta_modelo("vectorial_evolutivo")
        self.poblacion, self.generaciones = poblacion, generaciones

    @staticmethod
    def _modelo(genes: np.ndarray, semilla: int) -> Any:
        from sklearn.pipeline import make_pipeline

        return make_pipeline(StandardScaler(), SVC(C=10 ** genes[0], gamma=10 ** genes[1], random_state=semilla))

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42) -> Dict[str, Any]:
        rng = np.random.default_rng(semilla)
        # Muestra acotada para la búsqueda (SVC es O(n²))
        if len(y) > 2000:
            idx, _ = train_test_split(np.arange(len(y)), train_size=2000, stratify=y, random_state=semilla)
            Xb, yb = X[idx], y[idx]
        else:
            Xb, yb = X, y
        cv = construir_cv_seguro(yb, semilla)
        limites = np.array([[-2.0, 3.0], [-4.0, 1.0]])

        def fitness(genes: np.ndarray) -> float:
            modelo = self._modelo(genes, semilla)
            if cv is None:
                return float(accuracy_score(yb, modelo.fit(Xb, yb).predict(Xb)))
            return float(np.mean(cross_val_score(modelo, Xb, yb, cv=cv, scoring="accuracy")))

        pob = rng.uniform(limites[:, 0], limites[:, 1], size=(self.poblacion, 2))
        apt = np.array([fitness(g) for g in pob])
        historial = [round(float(apt.max()), 6)]
        for _ in range(self.generaciones):
            orden = np.argsort(-apt)
            elite = pob[orden[: self.poblacion // 2]]
            hijos = []
            while len(hijos) < self.poblacion - len(elite):
                p1, p2 = elite[rng.integers(len(elite))], elite[rng.integers(len(elite))]
                hijo = (p1 + p2) / 2 + rng.normal(0, 0.3, 2)  # cruce aritmético + mutación gaussiana
                hijos.append(np.clip(hijo, limites[:, 0], limites[:, 1]))
            pob = np.vstack([elite, np.array(hijos)])
            apt = np.array([fitness(g) for g in pob])
            historial.append(round(float(apt.max()), 6))
        mejor = pob[int(np.argmax(apt))]

        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            modelo, metricas_holdout = entrenar_y_evaluar_sklearn(lambda: self._modelo(mejor, semilla), X, y, semilla)
            pred_train = modelo.predict(X)
            m_train = calcular_metricas_clasificacion(y, pred_train, puntuaciones_modelo(modelo, X))
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1
            guardar_modelo_seguro({"modelo": modelo, "genes": mejor, "version": version,
                                   "n_features": int(X.shape[1])}, self.ruta)
        return {
            "agente": "vectorial_evolutivo",
            "version_modelo": version,
            "mejores_hiperparametros": {"C": round(float(10 ** mejor[0]), 6), "gamma": round(float(10 ** mejor[1]), 6)},
            "evolucion_fitness_accuracy_cv": historial,
            "metricas": {
                "accuracy_cv": round(float(apt.max()), 6),
                "estrategia_cv": type(cv).__name__ if cv is not None else "sin_cv (accuracy de entrenamiento)",
                "precision_entrenamiento": m_train["precision"],
                "recall_entrenamiento": m_train["recall"],
                "f1_entrenamiento": m_train["f1_score"],
                "holdout": metricas_holdout,
            },
        }


# =============================================================================
# 13. AGENTE ENSAMBLADO EXPLICABLE (Gradient Boosting)
# =============================================================================
class AgenteEnsambladoExplicable:
    """Gradient Boosting con importancia de características (explicabilidad global)."""

    def __init__(self) -> None:
        self.ruta = ruta_modelo("ensamblado_gb")

    def entrenar(self, X: np.ndarray, y: np.ndarray, semilla: int = 42,
                 nombres: Optional[List[str]] = None) -> Dict[str, Any]:
        fabrica = lambda: GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.1,  # noqa: E731
                                                     subsample=0.9, random_state=semilla)
        with FileLock(f"{self.ruta}.train.lock", timeout=300):
            modelo, metricas = entrenar_y_evaluar_sklearn(fabrica, X, y, semilla)
            previo = cargar_modelo_seguro(self.ruta)
            version = (previo or {}).get("version", 0) + 1
            guardar_modelo_seguro({"modelo": modelo, "version": version, "n_features": int(X.shape[1])}, self.ruta)
        nombres = nombres if nombres and len(nombres) == X.shape[1] else [f"x{i}" for i in range(X.shape[1])]
        importancias = sorted(zip(nombres, modelo.feature_importances_.tolist()), key=lambda t: -t[1])
        return {
            "agente": "ensamblado_explicable_gb",
            "version_modelo": version,
            "metricas": metricas,
            "explicabilidad": {
                "metodo": "feature_importances_ (reducción de impureza)",
                "importancias_top": [{"caracteristica": n, "importancia": round(v, 6)} for n, v in importancias[:15]],
            },
        }



# =============================================================================
# 14. METACOGNICIÓN OPERATIVA  [P3-06]
# =============================================================================
class AgenteAnalistaDatosMetacognitivo:
    """
    Perfila el dataset y DECIDE la ruta (la entropía de Shannon influye de verdad):
      · Entropía normalizada de la distribución de clases H_norm = H / log2(K).
        H_norm < UMBRAL_ENTROPIA_DESBALANCE ⇒ datos desbalanceados ⇒ se prefiere
        Gradient Boosting (robusto al desbalance) sobre SVM.
      · Entropía media normalizada de las características (histograma 10 bins).
        Valor alto (> UMBRAL_ENTROPIA_FEATURES) con n ≥ 100 ⇒ distribución
        compleja/dispersa ⇒ Gradient Boosting.
      · n ≥ 200 y binario ⇒ red profunda; en otro caso ⇒ SVM evolutivo.
    """

    @staticmethod
    def _entropia_bits(probabilidades: np.ndarray) -> float:
        p = probabilidades[probabilidades > 0]
        return float(-(p * np.log2(p)).sum())

    def analizar(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        clases, conteos = np.unique(y, return_counts=True)
        k = len(clases)
        h_clases = self._entropia_bits(conteos / conteos.sum())
        h_norm = h_clases / math.log2(k) if k > 1 else 0.0
        ent_features = []
        for j in range(X.shape[1]):
            col = X[:, j]
            if np.ptp(col) <= 1e-12:
                ent_features.append(0.0)
                continue
            hist, _ = np.histogram(col, bins=10)
            ent_features.append(self._entropia_bits(hist / hist.sum()) / math.log2(10))
        return {
            "n_muestras": int(X.shape[0]),
            "n_caracteristicas": int(X.shape[1]),
            "clases": clases.tolist(),
            "distribucion_clases": {str(c): int(n) for c, n in zip(clases, conteos)},
            "entropia_shannon_bits": round(h_clases, 6),
            "entropia_clases_normalizada": round(h_norm, 6),
            "ratio_desbalance": round(float(conteos.max() / conteos.min()), 4),
            "entropia_media_caracteristicas_normalizada": round(float(np.mean(ent_features)), 6),
            "binario": set(clases.tolist()).issubset({0, 1}) and k == 2,
        }

    def decidir_ruta(self, perfil: Dict[str, Any]) -> Tuple[str, str]:
        if len(perfil["clases"]) < 2:
            raise ErrorValidacionDatos("Se requieren al menos 2 clases para entrenar.")
        if perfil["entropia_clases_normalizada"] < UMBRAL_ENTROPIA_DESBALANCE:
            return "ensamblado_explicable", (
                f"Entropía de clases {perfil['entropia_clases_normalizada']:.3f} < {UMBRAL_ENTROPIA_DESBALANCE} "
                "⇒ desbalance ⇒ Gradient Boosting preferido sobre SVM."
            )
        if perfil["entropia_media_caracteristicas_normalizada"] > UMBRAL_ENTROPIA_FEATURES and perfil["n_muestras"] >= 100:
            return "ensamblado_explicable", (
                f"Entropía media de características {perfil['entropia_media_caracteristicas_normalizada']:.3f} > "
                f"{UMBRAL_ENTROPIA_FEATURES} ⇒ alta complejidad ⇒ Gradient Boosting."
            )
        if perfil["n_muestras"] >= 200 and perfil["binario"]:
            return "cerebral_profundo", "Volumen suficiente (n ≥ 200) y problema binario ⇒ red profunda."
        return "vectorial_evolutivo", "Datos equilibrados y volumen moderado ⇒ SVM con búsqueda evolutiva."


class OrquestadorSemanticoUASI:
    """
    Convierte las categorías detectadas por el guardián en un PLAN con efecto
    operativo real (no meramente informativo):
      DIAGNOSTICO                  → health check detallado
      AUDITORIA                    → integridad del ledger + raíz Merkle del día
      APRENDIZAJE_ONLINE_CONTINUO  → fuerza la ruta de streaming
      EXPLICABILIDAD               → Gradient Boosting con importancias
      OPTIMIZACION_EVOLUTIVA       → SVM evolutivo
      APRENDIZAJE_PROFUNDO         → red profunda
      ANALISIS_METACOGNITIVO_AUTO  → decide el analista metacognitivo
    """

    PRIORIDAD = (
        "DIAGNOSTICO", "AUDITORIA", "APRENDIZAJE_ONLINE_CONTINUO", "EXPLICABILIDAD",
        "OPTIMIZACION_EVOLUTIVA", "APRENDIZAJE_PROFUNDO", "ANALISIS_METACOGNITIVO_AUTO",
    )
    RUTA_POR_PLAN = {
        "APRENDIZAJE_ONLINE_CONTINUO": "streaming",
        "EXPLICABILIDAD": "ensamblado_explicable",
        "OPTIMIZACION_EVOLUTIVA": "vectorial_evolutivo",
        "APRENDIZAJE_PROFUNDO": "cerebral_profundo",
    }

    def __init__(self) -> None:
        self.analista = AgenteAnalistaDatosMetacognitivo()

    def planificar(self, categorias: List[str]) -> str:
        return next(p for p in self.PRIORIDAD if p in categorias)

    def ejecutar(self, sol: SolicitudMaestraV5, categorias: List[str], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Síncrono y CPU-bound: se invoca SIEMPRE vía run_in_threadpool."""
        plan = self.planificar(categorias)
        salida: Dict[str, Any] = {"plan": plan, "categorias_detectadas": categorias}
        if plan == "DIAGNOSTICO":
            salida["ruta_ejecutada"] = "diagnostico"
            salida["resultado"] = estado_salud_detallado()
            return salida
        if plan == "AUDITORIA":
            hoy = ahora_utc().date().isoformat()
            bloques_hoy = ledger.bloques_de_fecha(hoy)
            salida["ruta_ejecutada"] = "auditoria"
            salida["resultado"] = {
                "integridad_ledger": ledger.verificar_integridad(),
                "fecha": hoy,
                "bloques_del_dia": len(bloques_hoy),
                "raiz_merkle_del_dia": MerkleOpenTimestampsManager.calcular_raiz_merkle(bloques_hoy) if bloques_hoy else None,
            }
            return salida

        if sol.inputs is None or sol.targets is None:
            raise ErrorValidacionDatos(f"El plan {plan} requiere inputs y targets.")
        X, informe = guardian.sanitizar_matriz(sol.inputs)
        y = np.asarray(sol.targets, dtype=int)
        semilla = semilla_desde_contexto(meta)
        salida["sanitizacion"] = informe
        salida["hash_datos"] = sha256_hex(X.tobytes() + y.tobytes())

        if plan == "APRENDIZAJE_ONLINE_CONTINUO":
            salida["ruta_ejecutada"] = "streaming"
            salida["resultado"] = MotorStreamingContinuo().ingerir(X, y, sol.clases_declaradas, False, semilla)
            return salida

        perfil = self.analista.analizar(X, y)
        salida["perfil_metacognitivo"] = perfil
        if plan == "ANALISIS_METACOGNITIVO_AUTO":
            ruta, razon = self.analista.decidir_ruta(perfil)
        else:
            ruta, razon = self.RUTA_POR_PLAN[plan], f"Ruta impuesta por el plan semántico {plan}."
        if ruta == "cerebral_profundo" and not perfil["binario"]:
            raise ErrorValidacionDatos("La red profunda requiere targets binarios (0/1) con ambas clases.")
        if len(perfil["clases"]) < 2:
            raise ErrorValidacionDatos("Se requieren al menos 2 clases para entrenar.")
        salida["ruta_ejecutada"], salida["razonamiento"] = ruta, razon
        if ruta == "cerebral_profundo":
            salida["resultado"] = AgenteCerebralProfundo().entrenar(X, y, semilla)
        elif ruta == "ensamblado_explicable":
            nombres = meta.get("nombres_caracteristicas") if isinstance(meta.get("nombres_caracteristicas"), list) else None
            salida["resultado"] = AgenteEnsambladoExplicable().entrenar(X, y, semilla, nombres)
        else:
            salida["resultado"] = AgenteVectorialEvolutivo().entrenar(X, y, semilla)
        return salida


# =============================================================================
# 15. LEDGER CRIPTOGRÁFICO + MERKLE + OPENTIMESTAMPS (Bitcoin L1)  [P3-01..04]
# =============================================================================
class LedgerAuditoriaCriptografica:
    """
    Ledger append-only hash-encadenado en JSONL (una línea = un bloque).
    Se elige JSONL en lugar de un volcado joblib completo porque un registro de
    auditoría debe ser legible por humanos, anexable sin reescribir y auditable
    con herramientas estándar. Escrituras bajo RLock + FileLock (multi-worker).

    hash_bloque = SHA-256( JSON canónico de {indice, timestamp, fecha, evento,
                           actor, datos, hash_datos, hash_previo} )
    """

    HASH_GENESIS = "0" * 64

    def __init__(self, ruta: str = ARCHIVO_LEDGER) -> None:
        self.ruta = ruta
        self._lock = threading.RLock()

    def _filelock(self) -> FileLock:
        return FileLock(f"{self.ruta}.lock", timeout=60)

    @staticmethod
    def _calcular_hash(bloque: Dict[str, Any]) -> str:
        contenido = {k: bloque[k] for k in ("indice", "timestamp", "fecha", "evento", "actor",
                                            "datos", "hash_datos", "hash_previo")}
        return sha256_hex(json_canonico(contenido).encode())

    def _leer_ultimo_bloque(self) -> Optional[Dict[str, Any]]:
        """Lee la última línea desde el final del archivo (O(1) en tamaño del ledger)."""
        if not os.path.exists(self.ruta) or os.path.getsize(self.ruta) == 0:
            return None
        with open(self.ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buffer = b""
            while pos > 0:
                paso = min(4096, pos)
                pos -= paso
                f.seek(pos)
                buffer = f.read(paso) + buffer
                lineas = buffer.rstrip(b"\n").split(b"\n")
                if len(lineas) > 1 or pos == 0:
                    return json.loads(lineas[-1].decode("utf-8"))
        return None

    def inicializar(self) -> None:
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        with self._lock, self._filelock():
            if self._leer_ultimo_bloque() is None:
                self._anexar_sin_lock("GENESIS", {"version": VERSION_UASI}, "sistema")

    def _anexar_sin_lock(self, evento: str, datos: Dict[str, Any], actor: str) -> Dict[str, Any]:
        ultimo = self._leer_ultimo_bloque()
        datos_json = json.loads(json_canonico(datos))  # normaliza a tipos JSON puros
        ts = ahora_utc()
        bloque = {
            "indice": (ultimo["indice"] + 1) if ultimo else 0,
            "timestamp": ts.isoformat(),
            "fecha": ts.date().isoformat(),
            "evento": evento,
            "actor": actor,
            "datos": datos_json,
            "hash_datos": sha256_hex(json_canonico(datos_json).encode()),
            "hash_previo": ultimo["hash_bloque"] if ultimo else self.HASH_GENESIS,
        }
        bloque["hash_bloque"] = self._calcular_hash(bloque)
        with open(self.ruta, "a", encoding="utf-8") as f:
            f.write(json_canonico(bloque) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return bloque

    def registrar(self, evento: str, datos: Dict[str, Any], actor: str = "sistema") -> Dict[str, Any]:
        with self._lock, self._filelock():
            return self._anexar_sin_lock(evento, datos, actor)

    def iterar(self):
        if not os.path.exists(self.ruta):
            return
        with open(self.ruta, "r", encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    yield json.loads(linea)

    def contar(self) -> int:
        return sum(1 for _ in self.iterar())

    def paginar(self, pagina: int, tamano: int) -> Dict[str, Any]:
        inicio, fin = (pagina - 1) * tamano, pagina * tamano
        items, total = [], 0
        for i, b in enumerate(self.iterar()):
            if inicio <= i < fin:
                items.append(b)
            total += 1
        return {"pagina": pagina, "tamano_pagina": tamano, "total_bloques": total,
                "total_paginas": max(1, math.ceil(total / tamano)), "bloques": items}

    def bloques_de_fecha(self, fecha: str) -> List[Dict[str, Any]]:
        return [b for b in self.iterar() if b.get("fecha") == fecha]

    def verificar_integridad(self) -> Dict[str, Any]:
        previo, n = self.HASH_GENESIS, 0
        try:
            for esperado_idx, b in enumerate(self.iterar()):
                n += 1
                if b["indice"] != esperado_idx:
                    return {"integro": False, "bloques": n, "error": f"Índice discontinuo en bloque {esperado_idx}."}
                if b["hash_previo"] != previo:
                    return {"integro": False, "bloques": n, "error": f"Encadenamiento roto en bloque {b['indice']}."}
                if sha256_hex(json_canonico(b["datos"]).encode()) != b["hash_datos"]:
                    return {"integro": False, "bloques": n, "error": f"hash_datos alterado en bloque {b['indice']}."}
                if self._calcular_hash(b) != b["hash_bloque"]:
                    return {"integro": False, "bloques": n, "error": f"hash_bloque alterado en bloque {b['indice']}."}
                previo = b["hash_bloque"]
        except (json.JSONDecodeError, KeyError) as exc:
            return {"integro": False, "bloques": n, "error": f"Ledger corrupto: {type(exc).__name__}."}
        return {"integro": True, "bloques": n, "ultimo_hash": previo}


class MerkleOpenTimestampsManager:
    """
    Raíz de Merkle diaria + anclaje en Bitcoin L1 mediante OpenTimestamps.

    Árbol de Merkle (estilo RFC 6962, con separación de dominio):
        hoja  = SHA256(0x00 || hash_bloque)
        nodo  = SHA256(0x01 || izq || der)
        nodo impar en un nivel ⇒ se promociona sin duplicar (evita CVE-2012-2459)

    Flujo de anclaje:
        1. La raíz (32 bytes) es el "digest" de un DetachedTimestampFile.
        2. Se añade un nonce aleatorio (privacidad) y se aplica SHA256.
        3. El resultado se envía en paralelo (asyncio) a varios calendarios OTS.
        4. Los calendarios agregan miles de compromisos y los publican en una
           transacción Bitcoin con OP_RETURN (≈ cada pocas horas).
        5. Se guarda el .ots (prueba "pendiente"). Tras la confirmación,
           /verify-ots hace "upgrade" y obtiene la atestación de bloque Bitcoin.
    """

    PATRON_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def __init__(self, ledger_ref: LedgerAuditoriaCriptografica, directorio: str = PATH_OTS) -> None:
        self.ledger = ledger_ref
        self.directorio = directorio

    # --- Merkle -------------------------------------------------------------
    @staticmethod
    def calcular_raiz_merkle(bloques: List[Dict[str, Any]]) -> str:
        if not bloques:
            raise ErrorValidacionDatos("No hay bloques para calcular la raíz de Merkle.")
        nivel = [hashlib.sha256(b"\x00" + bytes.fromhex(b["hash_bloque"])).digest() for b in bloques]
        while len(nivel) > 1:
            siguiente = [hashlib.sha256(b"\x01" + nivel[i] + nivel[i + 1]).digest() for i in range(0, len(nivel) - 1, 2)]
            if len(nivel) % 2 == 1:
                siguiente.append(nivel[-1])
            nivel = siguiente
        return nivel[0].hex()

    def _rutas(self, fecha: str) -> Tuple[str, str]:
        if not self.PATRON_FECHA.match(fecha):  # evita path traversal
            raise ErrorValidacionDatos("Fecha inválida; formato esperado YYYY-MM-DD.")
        datetime.strptime(fecha, "%Y-%m-%d")
        return os.path.join(self.directorio, f"{fecha}.ots"), os.path.join(self.directorio, f"{fecha}.json")

    @staticmethod
    def _estado_no_disponible() -> Dict[str, Any]:
        return {"estado": "opentimestamps_no_disponible",
                "detalle": f"Instale 'opentimestamps' para anclar en Bitcoin. Error de importación: {OTS_ERROR_IMPORT}"}

    @staticmethod
    def _url_permitida(url: str) -> bool:
        return url in OTS_CALENDARS or url in DEFAULT_CALENDAR_WHITELIST  # type: ignore[operator]

    @staticmethod
    def _serializar(dtf: Any, ruta: str) -> None:
        tmp = f"{ruta}.tmp-{uuid4().hex}"
        with open(tmp, "wb") as f:
            dtf.serialize(StreamSerializationContext(f))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)

    @staticmethod
    def _deserializar(ruta: str) -> Any:
        with open(ruta, "rb") as f:
            return DetachedTimestampFile.deserialize(StreamDeserializationContext(f))

    # --- Anclaje ------------------------------------------------------------
    async def anclar_en_bitcoin(self, raiz_merkle: str, fecha: str, n_bloques: Optional[int] = None) -> Dict[str, Any]:
        if not OTS_DISPONIBLE:
            return {**self._estado_no_disponible(), "raiz_merkle": raiz_merkle, "fecha": fecha}
        ruta_ots, ruta_meta = self._rutas(fecha)
        os.makedirs(self.directorio, exist_ok=True)

        digest = bytes.fromhex(raiz_merkle)
        dtf = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
        tip = dtf.timestamp.ops.add(OpAppend(os.urandom(16))).ops.add(OpSHA256())

        async def enviar(url: str) -> Tuple[str, Any]:
            calendario = RemoteCalendar(url, user_agent=f"UASI/{VERSION_UASI}")
            return url, await asyncio.to_thread(calendario.submit, tip.msg, OTS_TIMEOUT_SECONDS)

        resultados = await asyncio.gather(*(enviar(u) for u in OTS_CALENDARS), return_exceptions=True)
        aceptados, errores = [], []
        for url, res in zip(OTS_CALENDARS, resultados):
            if isinstance(res, BaseException):
                errores.append({"calendario": url, "error": f"{type(res).__name__}: {str(res)[:200]}"})
            else:
                tip.merge(res[1])
                aceptados.append(url)

        if len(aceptados) < OTS_MIN_CALENDARS:
            logger.error("anclaje_ots_fallido", extra={"fecha": fecha, "errores": errores})
            return {"estado": "error_anclaje", "raiz_merkle": raiz_merkle, "fecha": fecha,
                    "calendarios_aceptados": aceptados, "errores": errores,
                    "detalle": f"Se requieren al menos {OTS_MIN_CALENDARS} calendario(s) OTS."}

        sellado = ahora_utc().isoformat()
        meta = {"fecha": fecha, "raiz_merkle": raiz_merkle, "n_bloques": n_bloques, "sellado_en": sellado,
                "calendarios": aceptados, "red_bitcoin_configurada": BITCOIN_NETWORK, "version": VERSION_UASI}
        def persistir() -> None:
            # Bloqueante (locks + fsync) → se ejecuta en un hilo, nunca en el event loop
            with _LOCK_PROCESO, FileLock(f"{ruta_ots}.lock", timeout=60):
                self._serializar(dtf, ruta_ots)
                # Histórico inmutable de cada anclaje del día (la raíz cambia al crecer el ledger)
                sufijo = sellado.replace(":", "").replace("-", "").split(".")[0].split("+")[0]
                self._serializar(dtf, os.path.join(self.directorio, f"{fecha}_{sufijo}.ots"))
                _escritura_atomica_texto(ruta_meta, json.dumps(meta, indent=2, ensure_ascii=False))

        await asyncio.to_thread(persistir)
        logger.info("anclaje_ots_enviado", extra={"fecha": fecha, "raiz": raiz_merkle, "calendarios": aceptados})
        return {"estado": "pendiente_confirmacion_bitcoin", **meta, "archivo_ots": os.path.basename(ruta_ots),
                "errores_calendarios": errores,
                "nota": "Los calendarios publican la transacción OP_RETURN en Bitcoin en pocas horas; "
                        "consulte /api/v5/verify-ots/{fecha} para obtener la atestación de bloque."}

    # --- Verificación -------------------------------------------------------
    def _upgrade(self, timestamp: Any) -> Tuple[bool, List[Dict[str, Any]]]:
        """Consulta a los calendarios las atestaciones pendientes (bloqueante → threadpool)."""
        def con_atestaciones(ts: Any):
            if ts.attestations:
                yield ts
            for sub in ts.ops.values():
                yield from con_atestaciones(sub)

        cambiado, incidencias = False, []
        for sub in list(con_atestaciones(timestamp)):
            for att in list(sub.attestations):
                if not isinstance(att, PendingAttestation):
                    continue
                if not self._url_permitida(att.uri):
                    incidencias.append({"calendario": att.uri, "estado": "no_en_lista_blanca"})
                    continue
                try:
                    mejorado = RemoteCalendar(att.uri, user_agent=f"UASI/{VERSION_UASI}").get_timestamp(
                        sub.msg, timeout=OTS_TIMEOUT_SECONDS)
                    sub.merge(mejorado)
                    cambiado = True
                except CommitmentNotFoundError:
                    incidencias.append({"calendario": att.uri, "estado": "aun_pendiente"})
                except Exception as exc:  # red, timeout, etc.
                    incidencias.append({"calendario": att.uri, "estado": f"error: {type(exc).__name__}"})
        return cambiado, incidencias

    @staticmethod
    def _contrastar_bloque(altura: int, merkle_root_esperada: str) -> Dict[str, Any]:
        """Compara la merkle root del bloque Bitcoin (vía Esplora) con la de la prueba."""
        if not HTTPX_DISPONIBLE:
            return {"contrastado": False, "detalle": "httpx no disponible"}
        try:
            with httpx.Client(timeout=OTS_TIMEOUT_SECONDS) as cliente:
                r = cliente.get(f"{ESPLORA_API_URL}/block-height/{altura}")
                r.raise_for_status()
                hash_bloque = r.text.strip()
                info = cliente.get(f"{ESPLORA_API_URL}/block/{hash_bloque}")
                info.raise_for_status()
                datos = info.json()
            return {
                "contrastado": True,
                "explorador": ESPLORA_API_URL,
                "hash_bloque": hash_bloque,
                "coincide_merkle_root": datos.get("merkle_root") == merkle_root_esperada,
                "fecha_bloque_utc": datetime.fromtimestamp(int(datos["timestamp"]), timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {"contrastado": False, "detalle": f"{type(exc).__name__}: {str(exc)[:200]}"}

    async def verificar_prueba_ots(self, archivo_ots: str) -> Dict[str, Any]:
        if not OTS_DISPONIBLE:
            return self._estado_no_disponible()
        if not os.path.exists(archivo_ots):
            raise FileNotFoundError(archivo_ots)
        ruta_meta = archivo_ots[: -len(".ots")] + ".json"
        meta: Dict[str, Any] = {}
        if os.path.exists(ruta_meta):
            with open(ruta_meta, encoding="utf-8") as f:
                meta = json.load(f)

        dtf = await asyncio.to_thread(self._deserializar, archivo_ots)
        digest_prueba = dtf.file_digest.hex()

        # 1) Coherencia ledger ↔ prueba: se recalcula la raíz con los mismos n bloques
        coherencia: Dict[str, Any] = {"raiz_en_prueba": digest_prueba}
        if meta.get("fecha"):
            bloques = await run_in_threadpool(self.ledger.bloques_de_fecha, meta["fecha"])
            n = meta.get("n_bloques") or len(bloques)
            if len(bloques) >= n and n > 0:
                raiz_recalculada = self.calcular_raiz_merkle(bloques[:n])
                coherencia.update({"raiz_recalculada_desde_ledger": raiz_recalculada, "n_bloques": n,
                                   "coincide": raiz_recalculada == digest_prueba == meta.get("raiz_merkle")})
            else:
                coherencia.update({"coincide": False, "detalle": "El ledger tiene menos bloques que los anclados."})

        # 2) Upgrade contra calendarios (bloqueante → hilo)
        cambiado, incidencias = await asyncio.to_thread(self._upgrade, dtf.timestamp)
        if cambiado:
            def persistir() -> None:
                with _LOCK_PROCESO, FileLock(f"{archivo_ots}.lock", timeout=60):
                    self._serializar(dtf, archivo_ots)

            await asyncio.to_thread(persistir)

        # 3) Inventario de atestaciones y contraste con la cadena de bloques
        atestaciones: List[Dict[str, Any]] = []
        confirmada = contrastada_ok = False
        for msg, att in dtf.timestamp.all_attestations():
            if isinstance(att, BitcoinBlockHeaderAttestation):
                confirmada = True
                esperado = msg[::-1].hex()  # Esplora muestra la merkle root en orden de visualización
                item = {"tipo": "bitcoin_block_header", "altura_bloque": att.height, "merkle_root_bloque": esperado}
                item["contraste_explorador"] = await asyncio.to_thread(self._contrastar_bloque, att.height, esperado)
                contrastada_ok = contrastada_ok or bool(item["contraste_explorador"].get("coincide_merkle_root"))
                atestaciones.append(item)
            elif isinstance(att, PendingAttestation):
                atestaciones.append({"tipo": "pendiente", "calendario": att.uri})
            else:
                atestaciones.append({"tipo": type(att).__name__})

        if confirmada and contrastada_ok:
            estado = "confirmada_en_bitcoin_y_contrastada"
        elif confirmada:
            estado = "confirmada_en_bitcoin (contraste con explorador no disponible)"
        else:
            estado = "pendiente_confirmacion_bitcoin"
        return {"estado": estado, "archivo_ots": os.path.basename(archivo_ots), "metadatos_anclaje": meta,
                "coherencia_ledger": coherencia, "prueba_actualizada": cambiado,
                "atestaciones": atestaciones, "incidencias_calendarios": incidencias,
                "red_bitcoin_configurada": BITCOIN_NETWORK}


ledger = LedgerAuditoriaCriptografica()
merkle_manager = MerkleOpenTimestampsManager(ledger)
orquestador = OrquestadorSemanticoUASI()

MODELOS_ESPERADOS = ("red_neuronal_legacy", "svm_legacy", "arbol_legacy", "streaming_sgd",
                     "cerebral_profundo", "vectorial_evolutivo", "ensamblado_gb")


def estado_salud_detallado() -> Dict[str, Any]:
    """Health check REAL: workspace escribible, checksums de modelos, integridad del ledger."""
    componentes: Dict[str, Any] = {}
    critico = degradado = False
    try:
        os.makedirs(PATH_WORKSPACE, exist_ok=True)
        prueba = os.path.join(PATH_WORKSPACE, f".healthcheck-{uuid4().hex}")
        with open(prueba, "w") as f:
            f.write("ok")
        os.remove(prueba)
        componentes["workspace"] = {"ruta": PATH_WORKSPACE, "escribible": True}
    except OSError as exc:
        componentes["workspace"] = {"ruta": PATH_WORKSPACE, "escribible": False, "error": str(exc)}
        critico = True

    modelos = {}
    for nombre in MODELOS_ESPERADOS:
        ruta = ruta_modelo(nombre)
        if not os.path.exists(ruta):
            modelos[nombre] = {"estado": "no_entrenado"}
            continue
        ok, motivo = verificar_checksum(ruta)
        modelos[nombre] = {"estado": "ok" if ok else "integridad_comprometida", "checksum": motivo,
                           "tamano_bytes": os.path.getsize(ruta),
                           "modificado_utc": datetime.fromtimestamp(os.path.getmtime(ruta), timezone.utc).isoformat()}
        degradado = degradado or not ok
    componentes["modelos"] = modelos

    integridad = ledger.verificar_integridad()
    componentes["ledger"] = integridad
    critico = critico or not integridad["integro"]

    componentes["autenticacion"] = {"configurada": bool(SECRET_JWT_KEY)}
    degradado = degradado or not SECRET_JWT_KEY
    componentes["opentimestamps"] = {"disponible": OTS_DISPONIBLE, "calendarios": OTS_CALENDARS,
                                     "red_bitcoin": BITCOIN_NETWORK, "error_import": OTS_ERROR_IMPORT}
    estado = "critico" if critico else ("degradado" if degradado else "ok")
    return {"estado": estado, "version": VERSION_UASI, "entorno": UASI_ENV,
            "timestamp": ahora_utc().isoformat(), "componentes": componentes}


# =============================================================================
# 17. LIFESPAN (sustituye a @app.on_event)
# =============================================================================
@asynccontextmanager
async def lifespan(app_: FastAPI):
    for d in (PATH_WORKSPACE, PATH_MODELOS, PATH_LEDGER, PATH_OTS):
        os.makedirs(d, exist_ok=True)
    if not SECRET_JWT_KEY:
        logger.warning("SECRET_JWT_KEY ausente: los endpoints protegidos responderán 503 (fail-closed).")
    elif len(SECRET_JWT_KEY) < 32:
        logger.warning("SECRET_JWT_KEY demasiado corta (< 32 caracteres).")
    if not OTS_DISPONIBLE:
        logger.warning("OpenTimestamps no disponible", extra={"error": OTS_ERROR_IMPORT})
    if BITCOIN_NETWORK == "testnet" and OTS_CALENDARS and "opentimestamps.org" in ",".join(OTS_CALENDARS):
        logger.warning("BITCOIN_NETWORK=testnet pero los calendarios públicos OTS anclan en mainnet; "
                       "configure OTS_CALENDARS con un calendario testnet propio.")
    await run_in_threadpool(ledger.inicializar)
    logger.info("uasi_arranque", extra={"version": VERSION_UASI, "workspace": PATH_WORKSPACE,
                                        "rate_limit_min": RATE_LIMIT_PER_MINUTE, "red_bitcoin": BITCOIN_NETWORK})
    yield
    logger.info("uasi_parada")


# =============================================================================
# 16. APLICACIÓN Y ENDPOINTS REST
# =============================================================================
app = FastAPI(
    title="UASI v5 Enterprise Production",
    version=VERSION_UASI,
    description="Agentes de aprendizaje gobernados, auditables y anclados en Bitcoin vía OpenTimestamps.",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(LimiteTamanoPayloadMiddleware, max_bytes=MAX_PAYLOAD_BYTES)  # el más externo


# --- Traducción de excepciones de dominio a HTTP -----------------------------
from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def _h_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Se omite el campo "input" para no reflejar datos (ni NaN no serializables)
    errores = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errores})


@app.exception_handler(ErrorValidacionDatos)
async def _h_datos(_: Request, exc: ErrorValidacionDatos) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ErrorGobernanza)
async def _h_gobernanza(_: Request, exc: ErrorGobernanza) -> JSONResponse:
    logger.warning("gobernanza_rechazo", extra={"motivo": str(exc)})
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ConflictoClasesStreaming)
async def _h_conflicto(_: Request, exc: ConflictoClasesStreaming) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(IntegridadModeloError)
async def _h_integridad(_: Request, exc: IntegridadModeloError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def _h_generico(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("error_no_controlado", extra={"tipo": type(exc).__name__})
    return JSONResponse(status_code=500, content={"detail": "Error interno. Consulte los logs con el id de correlación."})


AGENTES_V1: Dict[str, Callable[[], _AgenteLegacyBase]] = {
    "red_neuronal": AgenteRedNeuronalLegacy,
    "svm": AgenteSVMLegacy,
    "arbol_decision": AgenteArbolDecisionLegacy,
}


@app.get("/api/v5/token", tags=["auth"])
async def emitir_token_desarrollo(
    sujeto: str = Query("desarrollador", pattern=r"^[A-Za-z0-9_.@\-]{1,64}$"),
    minutos: int = Query(JWT_EXP_MINUTES, ge=1, le=1440),
) -> Dict[str, Any]:
    """Token de PRUEBA. Solo existe si SECRET_JWT_KEY está definida y UASI_ENV != production."""
    if not SECRET_JWT_KEY or UASI_ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    return {"access_token": emitir_token(sujeto, minutos), "token_type": "bearer",
            "expira_en_minutos": minutos, "advertencia": "Solo para desarrollo."}


@app.post("/api/v1/procesar", tags=["v1"])
async def procesar_v1(sol: SolicitudProcesamientoV1, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    intencion = guardian.validar_intencion(sol.intencion, guardian.INTENCIONES_V1)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    X, informe = guardian.sanitizar_matriz(sol.inputs)
    y = np.asarray(sol.targets, dtype=int)
    resultado = await run_in_threadpool(AGENTES_V1[sol.tipo_agente]().entrenar, X, y, semilla_desde_contexto(meta))
    bloque = await run_in_threadpool(ledger.registrar, "PROCESAMIENTO_V1", {
        "id_solicitud": sol.id_solicitud, "intencion": intencion, "agente": resultado["agente"],
        "hash_datos": sha256_hex(X.tobytes() + y.tobytes()), "n_muestras": int(X.shape[0]),
        "n_caracteristicas": int(X.shape[1]), "metricas": resultado["metricas"],
        "metadatos_contexto": meta, "sanitizacion": informe}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            "sanitizacion": informe, **resultado, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}


@app.post("/api/v5/uasi-master-execute", tags=["v5"])
async def uasi_master_execute(sol: SolicitudMaestraV5, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    categorias = guardian.clasificar_prompt_master(sol.prompt_intencion)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    salida = await run_in_threadpool(orquestador.ejecutar, sol, categorias, meta)
    resultado = salida.get("resultado", {})
    bloque = await run_in_threadpool(ledger.registrar, "EJECUCION_MAESTRA_V5", {
        "id_solicitud": sol.id_solicitud, "plan": salida["plan"], "ruta": salida.get("ruta_ejecutada"),
        "hash_datos": salida.get("hash_datos"), "metricas": resultado.get("metricas") if isinstance(resultado, dict) else None,
        "metadatos_contexto": meta}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            **salida, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}


@app.post("/api/v5/streaming-ingest", tags=["v5"])
async def streaming_ingest(sol: SolicitudStreaming, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    guardian.validar_intencion(sol.intencion, guardian.INTENCIONES_STREAMING)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    X, informe = guardian.sanitizar_matriz(sol.inputs)
    y = np.asarray(sol.targets, dtype=int)
    resultado = await run_in_threadpool(MotorStreamingContinuo().ingerir, X, y, sol.clases_declaradas,
                                        sol.reiniciar_modelo, semilla_desde_contexto(meta))
    bloque = await run_in_threadpool(ledger.registrar, "INGESTA_STREAMING", {
        "id_solicitud": sol.id_solicitud, "hash_datos": sha256_hex(X.tobytes() + y.tobytes()),
        "n_muestras": int(X.shape[0]), "version_modelo": resultado["version_modelo"],
        "metricas": resultado["metricas"], "metadatos_contexto": meta, "sanitizacion": informe}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            "sanitizacion": informe, **resultado, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}


@app.get("/api/v5/cryptographic-ledger", tags=["auditoria"])
async def cryptographic_ledger(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    verificar: bool = Query(False, description="Verifica la cadena completa (coste O(n))."),
    claims: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    datos = await run_in_threadpool(ledger.paginar, page, page_size)
    if verificar:
        datos["integridad"] = await run_in_threadpool(ledger.verificar_integridad)
    return datos


@app.get("/api/v5/merkle-anchor", tags=["auditoria"])
async def merkle_anchor(
    fecha: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Por defecto: hoy (UTC)."),
    claims: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    fecha = fecha or ahora_utc().date().isoformat()
    bloques = await run_in_threadpool(ledger.bloques_de_fecha, fecha)
    if not bloques:
        raise HTTPException(status_code=404, detail=f"No hay bloques en el ledger para {fecha}.")
    raiz = MerkleOpenTimestampsManager.calcular_raiz_merkle(bloques)
    resultado = await merkle_manager.anclar_en_bitcoin(raiz, fecha, n_bloques=len(bloques))
    await run_in_threadpool(ledger.registrar, "ANCLAJE_MERKLE_OTS", {
        "fecha": fecha, "raiz_merkle": raiz, "n_bloques": len(bloques), "estado": resultado.get("estado"),
        "calendarios": resultado.get("calendarios") or resultado.get("calendarios_aceptados")}, claims["sub"])
    return {"fecha": fecha, "raiz_merkle": raiz, "n_bloques": len(bloques), "anclaje": resultado}


@app.get("/api/v5/verify-ots/{fecha}", tags=["auditoria"])
async def verify_ots(fecha: str, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    ruta_ots, _ = merkle_manager._rutas(fecha)
    if not OTS_DISPONIBLE:
        return MerkleOpenTimestampsManager._estado_no_disponible()
    if not os.path.exists(ruta_ots):
        raise HTTPException(status_code=404, detail=f"No existe prueba OTS para {fecha}.")
    return await merkle_manager.verificar_prueba_ots(ruta_ots)


@app.get("/api/v5/health", tags=["salud"])
async def health() -> JSONResponse:
    """Sin autenticación. 200 si ok/degradado, 503 si crítico."""
    datos = await run_in_threadpool(estado_salud_detallado)
    return JSONResponse(status_code=503 if datos["estado"] == "critico" else 200, content=datos)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("uasi_v5_enterprise_production:app", host=os.getenv("HOST", "0.0.0.0"),
                port=_env_int("PORT", 8000), workers=_env_int("UVICORN_WORKERS", 1))
