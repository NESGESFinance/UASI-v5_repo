"""Ruta de emisión de tokens de desarrollo."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from uasi_v5.core.config import JWT_EXP_MINUTES, SECRET_JWT_KEY, UASI_ENV
from uasi_v5.seguridad.autenticacion import emitir_token

router = APIRouter(tags=["auth"])


@router.get("/api/v5/token")
async def emitir_token_desarrollo(
    sujeto: str = Query("desarrollador", pattern=r"^[A-Za-z0-9_.@\-]{1,64}$"),
    minutos: int = Query(JWT_EXP_MINUTES, ge=1, le=1440),
) -> Dict[str, Any]:
    """Token de PRUEBA. Solo existe si SECRET_JWT_KEY está definida y UASI_ENV != production."""
    if not SECRET_JWT_KEY or UASI_ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    return {"access_token": emitir_token(sujeto, minutos), "token_type": "bearer",
            "expira_en_minutos": minutos, "advertencia": "Solo para desarrollo."}
