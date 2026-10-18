"""Modelos Pydantic de entrada [P1-06] [P1-07]."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uasi_v5.core.config import MAX_FEATURES, MAX_MUESTRAS
from uasi_v5.core.utilidades import ahora_utc

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
    def validar_coherencia(self) -> SolicitudProcesamientoV1:
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
    def validar_coherencia(self) -> SolicitudMaestraV5:
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
    def validar_coherencia(self) -> SolicitudStreaming:
        if len(self.inputs) != len(self.targets):
            raise ValueError(f"len(inputs)={len(self.inputs)} != len(targets)={len(self.targets)}.")
        if not self.targets or any(t < 0 for t in self.targets):
            raise ValueError("targets debe contener enteros no negativos y no estar vacío.")
        if self.clases_declaradas is not None and any(c < 0 for c in self.clases_declaradas):
            raise ValueError("clases_declaradas debe contener enteros no negativos.")
        return self
