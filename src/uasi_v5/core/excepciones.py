"""Excepciones de dominio (se traducen a códigos HTTP en `uasi_v5.api.manejadores`)."""

from __future__ import annotations


class ErrorValidacionDatos(ValueError):
    """Datos de entrada incoherentes → HTTP 422."""


class ErrorGobernanza(PermissionError):
    """Petición rechazada por la política de gobernanza → HTTP 403."""


class IntegridadModeloError(RuntimeError):
    """Checksum de un artefacto no coincide → HTTP 500 (posible manipulación)."""


class ConflictoClasesStreaming(RuntimeError):
    """Clases nuevas incompatibles con el modelo online persistido → HTTP 409."""
