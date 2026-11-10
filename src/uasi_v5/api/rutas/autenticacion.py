"""Rutas de autenticación: login por usuario/contraseña (producción) y token de desarrollo."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.config import JWT_EXP_MINUTES, SECRET_JWT_KEY, UASI_ENV
from uasi_v5.core.registro import logger
from uasi_v5.seguridad.autenticacion import emitir_token, verificar_credenciales

router = APIRouter(tags=["auth"])


class SolicitudLogin(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


@router.post("/api/v5/token")
async def login(sol: SolicitudLogin) -> Dict[str, Any]:
    """
    Login real por usuario y contraseña. Funciona en TODOS los entornos (también producción).
    Devuelve un JWT firmado con los scopes del usuario. 401 genérico si las credenciales
    son inválidas o la cuenta está desactivada (no se distingue el motivo).
    """
    if not SECRET_JWT_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Autenticación no configurada en el servidor (SECRET_JWT_KEY ausente).")
    creds = await run_in_threadpool(verificar_credenciales, sol.username, sol.password)
    if creds is None:
        raise HTTPException(status_code=401, detail="Credenciales inválidas.",
                            headers={"WWW-Authenticate": "Bearer"})
    token = emitir_token(creds["sub"], alcance=creds["scope"])
    logger.info("login_exitoso", extra={"sujeto": creds["sub"]})
    return {"access_token": token, "token_type": "bearer", "expires_in": JWT_EXP_MINUTES * 60,
            "scope": creds["scope"]}


@router.get("/api/v5/token")
async def emitir_token_desarrollo(
    sujeto: str = Query("desarrollador", pattern=r"^[A-Za-z0-9_.@\-]{1,64}$"),
    minutos: int = Query(JWT_EXP_MINUTES, ge=1, le=1440),
    alcance: str = Query("uasi:full", pattern=r"^uasi:full( admin)?$",
                         description="'uasi:full' o 'uasi:full admin' (endpoints /api/v5/admin/*)."),
) -> Dict[str, Any]:
    """Token de PRUEBA. Solo existe si SECRET_JWT_KEY está definida y UASI_ENV != production."""
    if not SECRET_JWT_KEY or UASI_ENV == "production":
        raise HTTPException(status_code=404, detail="Not Found")
    return {"access_token": emitir_token(sujeto, minutos, alcance=alcance), "token_type": "bearer", "scope": alcance,
            "expira_en_minutos": minutos, "advertencia": "Solo para desarrollo."}
