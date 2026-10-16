"""Health check público (sin autenticación)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from uasi_v5.servicios import estado_salud_detallado

router = APIRouter(tags=["salud"])


@router.get("/api/v5/health")
async def health() -> JSONResponse:
    """Sin autenticación. 200 si ok/degradado, 503 si crítico."""
    datos = await run_in_threadpool(estado_salud_detallado)
    return JSONResponse(status_code=503 if datos["estado"] == "critico" else 200, content=datos)
