"""Traducción de excepciones de dominio a respuestas HTTP."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from uasi_v5.core.excepciones import (
    ConflictoClasesStreaming,
    ErrorGobernanza,
    ErrorValidacionDatos,
    IntegridadModeloError,
)
from uasi_v5.core.registro import logger


async def _h_validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Se omite el campo "input" para no reflejar datos (ni NaN no serializables)
    errores = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errores})


async def _h_datos(_: Request, exc: ErrorValidacionDatos) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


async def _h_gobernanza(_: Request, exc: ErrorGobernanza) -> JSONResponse:
    logger.warning("gobernanza_rechazo", extra={"motivo": str(exc)})
    return JSONResponse(status_code=403, content={"detail": str(exc)})


async def _h_conflicto(_: Request, exc: ConflictoClasesStreaming) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


async def _h_integridad(_: Request, exc: IntegridadModeloError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


async def _h_generico(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("error_no_controlado", extra={"tipo": type(exc).__name__})
    return JSONResponse(status_code=500, content={"detail": "Error interno. Consulte los logs con el id de correlación."})


def registrar_manejadores(app: FastAPI) -> None:
    """Registra todos los manejadores de excepción en la aplicación."""
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _h_validacion)  # type: ignore[arg-type]
    app.add_exception_handler(ErrorValidacionDatos, _h_datos)  # type: ignore[arg-type]
    app.add_exception_handler(ErrorGobernanza, _h_gobernanza)  # type: ignore[arg-type]
    app.add_exception_handler(ConflictoClasesStreaming, _h_conflicto)  # type: ignore[arg-type]
    app.add_exception_handler(IntegridadModeloError, _h_integridad)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _h_generico)
