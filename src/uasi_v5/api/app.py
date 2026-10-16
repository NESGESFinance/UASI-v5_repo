"""Fábrica de la aplicación FastAPI: lifespan, middlewares, manejadores y routers."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi.middleware import SlowAPIMiddleware
from starlette.concurrency import run_in_threadpool

from uasi_v5.api.manejadores import registrar_manejadores
from uasi_v5.api.rutas import auditoria, autenticacion, jev, salud, v1, v5
from uasi_v5.auditoria.merkle_ots import OTS_DISPONIBLE, OTS_ERROR_IMPORT
from uasi_v5.core.config import (
    BITCOIN_NETWORK,
    MAX_PAYLOAD_BYTES,
    OTS_CALENDARS,
    PATH_LEDGER,
    PATH_MODELOS,
    PATH_OTS,
    PATH_WORKSPACE,
    RATE_LIMIT_PER_MINUTE,
    SECRET_JWT_KEY,
    VERSION_UASI,
)
from uasi_v5.core.registro import logger
from uasi_v5.seguridad.limites import LimiteTamanoPayloadMiddleware, limiter
from uasi_v5.servicios import ledger


@asynccontextmanager
async def lifespan(app_: FastAPI):
    for d in (PATH_WORKSPACE, PATH_MODELOS, PATH_LEDGER, PATH_OTS):
        os.makedirs(d, exist_ok=True)
    if not SECRET_JWT_KEY:
        logger.warning("SECRET_JWT_KEY ausente: los endpoints protegidos responderán 503 (fail-closed).")
    elif len(SECRET_JWT_KEY) < 32:
        logger.warning("SECRET_JWT_KEY demasiado corta (< 32 caracteres).")
    if not os.getenv("JEV_HMAC_KEY", "").strip():
        logger.warning("JEV_HMAC_KEY ausente: /api/v5/jev-dispatch responderá 503 (fail-closed).")
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


def crear_app() -> FastAPI:
    """Construye la aplicación con el mismo contrato REST que v5.1.0."""
    app = FastAPI(
        title="UASI v5 Enterprise Production",
        version=VERSION_UASI,
        description="Agentes de aprendizaje gobernados, auditables y anclados en Bitcoin vía OpenTimestamps.",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    registrar_manejadores(app)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(LimiteTamanoPayloadMiddleware, max_bytes=MAX_PAYLOAD_BYTES)  # el más externo
    for modulo in (autenticacion, v1, v5, jev, auditoria, salud):
        app.include_router(modulo.router)
    return app
