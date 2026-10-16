"""Modelos Pydantic para el protocolo JEV ↔ UASI (JSON con firma HMAC-SHA256).

Flujo:
  JEV emite una SolicitudJEV firmada con HMAC-SHA256(cuerpo canónico, JEV_HMAC_KEY).
  UASI valida la firma, despacha agentes especializados por lenguaje y devuelve
  una RespuestaJEV firmada con el mismo secreto compartido.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from uasi_v5.core.utilidades import ahora_utc

# ---------------------------------------------------------------------------
# Tipos de tarea reconocidos por el dispatcher JEV
# ---------------------------------------------------------------------------
TipoTareaJEV = Literal[
    "INFERENCIA_MULTILENGUAJE",   # Ejecutar el modelo y formatear salidas por lenguaje
    "ANALISIS_DATOS",             # Análisis de datos con resultados formateados por ecosistema
    "EVALUACION_MODELO",          # Métricas del modelo representadas en cada lenguaje objetivo
    "AUDITORIA_LEDGER",           # Auditoría del ledger con salida estructurada por lenguaje
    "SINCRONIZACION_RWA",         # Sincronización de Real World Assets (Bitcoin / RWA / DeFi)
    "VALIDACION_CRUZADA",         # Validación cruzada con salida en cada SDK objetivo
    "GENERACION_BINDINGS",        # Generar fragmentos de código / bindings para cada lenguaje
]

LenguajeDestino = Literal[
    "python", "typescript", "javascript", "solidity", "rust", "go", "java"
]


# ---------------------------------------------------------------------------
# Submodelos de resultado por agente
# ---------------------------------------------------------------------------
class ResultadoAgenteJEV(BaseModel):
    """Resultado producido por un agente especializado en un lenguaje concreto."""

    model_config = ConfigDict(extra="ignore")

    lenguaje: str
    estado: Literal["exito", "error", "omitido"]
    datos_resultado: Optional[Any] = None
    mensaje_error: Optional[str] = None
    duracion_ms: float = 0.0
    hash_resultado: str = ""       # SHA-256 del JSON canónico de datos_resultado


# ---------------------------------------------------------------------------
# Solicitud JEV → UASI
# ---------------------------------------------------------------------------
class SolicitudJEV(BaseModel):
    """Cuerpo firmado que JEV envía al endpoint /api/v5/jev-dispatch.

    La firma HMAC-SHA256 se calcula sobre el JSON canónico del cuerpo
    **sin** el campo `firma_hmac` (el campo se excluye antes de firmar).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id_jev: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Identificador único generado por el sistema JEV (UUID recomendado).",
    )
    version_jev: str = Field(
        default="1.0",
        max_length=16,
        description="Versión del protocolo JEV (p. ej. '1.0').",
    )
    firma_hmac: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="HMAC-SHA256 en hexadecimal del cuerpo canónico (sin este campo).",
    )
    tipo_tarea: TipoTareaJEV = Field(
        ...,
        description="Tipo de operación que los agentes especializados deben ejecutar.",
    )
    lenguajes_destino: List[LenguajeDestino] = Field(
        default=["python"],
        min_length=1,
        description="Lenguajes para los que se solicita output (al menos uno).",
    )
    datos: Dict[str, Any] = Field(
        default_factory=dict,
        description="Payload de la tarea (inputs, parámetros, referencias de modelo, etc.).",
    )
    metadatos: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metadatos de contexto (origen, correlación, entorno).",
    )
    id_solicitud: str = Field(
        default_factory=lambda: str(uuid4()),
        description="UUID de correlación asignado por UASI si JEV no lo provee.",
    )
    timestamp: datetime = Field(default_factory=ahora_utc)

    @field_validator("lenguajes_destino")
    @classmethod
    def sin_duplicados(cls, v: List[str]) -> List[str]:
        vistos: list[str] = []
        for lang in v:
            if lang not in vistos:
                vistos.append(lang)
        return vistos


# ---------------------------------------------------------------------------
# Respuesta UASI → JEV
# ---------------------------------------------------------------------------
class RespuestaJEV(BaseModel):
    """Respuesta firmada que UASI devuelve a JEV tras ejecutar los agentes."""

    model_config = ConfigDict(extra="ignore")

    id_jev: str                               # eco del id_jev de la solicitud
    id_ejecucion: str = Field(default_factory=lambda: str(uuid4()))
    timestamp_respuesta: datetime = Field(default_factory=ahora_utc)
    firma_respuesta: str = ""                 # HMAC-SHA256 del cuerpo canónico de la respuesta
    estado: Literal["exito", "error_parcial", "error"]
    tipo_tarea: TipoTareaJEV
    lenguajes_ejecutados: List[str] = Field(default_factory=list)
    resultados_por_lenguaje: Dict[str, ResultadoAgenteJEV] = Field(default_factory=dict)
    bloque_ledger: Dict[str, Any] = Field(
        default_factory=dict,
        description="Referencia al bloque del ledger donde se registró esta ejecución.",
    )
    duracion_total_ms: float = 0.0
    version_uasi: str = ""
    errores_globales: List[str] = Field(default_factory=list)
