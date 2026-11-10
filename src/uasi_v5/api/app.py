"""Fábrica de la aplicación FastAPI: lifespan, middlewares, manejadores y routers."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from slowapi.middleware import SlowAPIMiddleware
from starlette.concurrency import run_in_threadpool

from uasi_v5.api.manejadores import registrar_manejadores
from uasi_v5.api.rutas import admin, auditoria, autenticacion, jev, orquestacion, salud, usuarios, v1, v5
from uasi_v5.auditoria.merkle_ots import OTS_DISPONIBLE, OTS_ERROR_IMPORT
from uasi_v5.core.config import (
    BITCOIN_NETWORK,
    MAX_PAYLOAD_BYTES,
    OTS_CALENDARS,
    PATH_LEDGER,
    PATH_MODELOS,
    PATH_OTS,
    PATH_WORKSPACE,
    RATE_LIMIT_ADMIN_PER_MINUTE,
    RATE_LIMIT_JEV_PER_MINUTE,
    RATE_LIMIT_PER_MINUTE,
    SECRET_JWT_KEY,
    UASI_ENV,
    UASI_WS_INTERVALO_SEGUNDOS,
    VERSION_UASI,
    validar_secretos,
)
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.db.sesion import inicializar_bd
from uasi_v5.seguridad.autenticacion import decodificar_token, token_revocado
from uasi_v5.seguridad.limites import (
    LimiteTamanoPayloadMiddleware,
    LimiteTasaDistribuidoMiddleware,
    MetricasHTTPMiddleware,
    limiter,
)
from uasi_v5.servicios import estado_agentes, ledger, limitador_distribuido, metricas_http


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
    await run_in_threadpool(inicializar_bd)
    await run_in_threadpool(ledger.inicializar)
    logger.info("uasi_arranque", extra={"version": VERSION_UASI, "workspace": PATH_WORKSPACE,
                                        "rate_limit_min": RATE_LIMIT_PER_MINUTE, "red_bitcoin": BITCOIN_NETWORK})
    yield
    logger.info("uasi_parada")


async def ws_agentes(websocket: WebSocket) -> None:
    """
    WebSocket /ws/agents?token=<JWT>: emite el estado de los agentes cada
    UASI_WS_INTERVALO_SEGUNDOS (5 s por defecto). Cierra con 1008 si el token falta,
    es inválido, está revocado o expira durante la conexión.
    """
    token = websocket.query_params.get("token", "")
    try:
        claims = decodificar_token(token) if token else None
        if claims is not None and await run_in_threadpool(token_revocado, claims.get("jti")):
            claims = None
    except jwt.PyJWTError:
        claims = None
    if claims is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        while True:
            if int(claims["exp"]) <= int(time.time()):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token expirado.")
                return
            agentes = await run_in_threadpool(estado_agentes)
            await websocket.send_json({
                "tipo": "estado_agentes",
                "timestamp": ahora_utc().isoformat(),
                "agentes": [{k: a[k] for k in ("id", "nombre", "estado", "ultima_actividad")} for a in agentes],
            })
            await asyncio.sleep(UASI_WS_INTERVALO_SEGUNDOS)
    except WebSocketDisconnect:
        return


def crear_app() -> FastAPI:
    """Construye la aplicación (contrato REST v5.1.0 + admin, WebSocket y BD de v5.3.0)."""
    for advertencia in validar_secretos(UASI_ENV):  # RuntimeError si algún secreto es débil
        logger.warning("secreto_ausente", extra={"detalle": advertencia})
    app = FastAPI(
        title="UASI v5 Enterprise Production",
        version=VERSION_UASI,
        description="Agentes de aprendizaje gobernados, auditables y anclados en Bitcoin vía OpenTimestamps.",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    registrar_manejadores(app)
    app.add_middleware(SlowAPIMiddleware)
    # Límites distribuidos (Redis o memoria) por grupo de rutas: JEV 30/min, admin 100/min por IP.
    app.add_middleware(LimiteTasaDistribuidoMiddleware, limitador=limitador_distribuido, reglas=[
        ("/api/v5/jev", RATE_LIMIT_JEV_PER_MINUTE, "jev"),
        ("/api/v5/admin", RATE_LIMIT_ADMIN_PER_MINUTE, "admin"),
    ])
    app.add_middleware(LimiteTamanoPayloadMiddleware, max_bytes=MAX_PAYLOAD_BYTES)
    app.add_middleware(MetricasHTTPMiddleware, metricas=metricas_http)  # el más externo
    for modulo in (autenticacion, v1, v5, jev, orquestacion, auditoria, salud, admin, usuarios):
        app.include_router(modulo.router)
    app.add_api_websocket_route("/ws/agents", ws_agentes)
    return app
