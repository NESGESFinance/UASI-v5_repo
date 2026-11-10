"""
API de administración (/api/v5/admin/*). Todas las rutas exigen un JWT con scope `admin`
(dependencia `require_admin`, que además registra la sesión en admin_sessions).

- GET  /api/v5/admin/agents            sub-agentes con estado, latencia media y errores 24h
- GET  /api/v5/admin/metrics           métricas HTTP, JEV, ledger y agentes
- GET  /api/v5/admin/ledger            entradas paginadas + verificar_integridad()
- POST /api/v5/admin/anchor            anclaje Merkle → Bitcoin (OpenTimestamps)
- GET  /api/v5/admin/health/detailed   Redis, base de datos, agentes JEV y componentes base
- POST /api/v5/admin/tokens/revoke     revoca un JWT por jti (tabla revoked_tokens)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from uasi_v5.api.rutas.auditoria import ejecutar_anclaje
from uasi_v5.core.config import RATE_LIMIT_ADMIN_PER_MINUTE, RATE_LIMIT_JEV_PER_MINUTE, VERSION_UASI
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.db.modelos import AdminSession, AgentMetric, RevokedToken
from uasi_v5.db.sesion import sesion_bd
from uasi_v5.seguridad.autenticacion import require_admin, revocar_token
from uasi_v5.servicios import (
    estado_agentes,
    ledger,
    limitador_distribuido,
    metricas_http,
    metricas_jev,
    salud_dependencias,
)

router = APIRouter(prefix="/api/v5/admin", tags=["admin"])


class SolicitudAnclaje(BaseModel):
    fecha: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Por defecto: hoy (UTC).")


class SolicitudRevocacion(BaseModel):
    jti: str = Field(..., min_length=1, max_length=64)
    sujeto: str = Field("", max_length=128)
    motivo: str = Field("", max_length=256)


@router.get("/agents")
async def listar_agentes(claims: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    agentes = await run_in_threadpool(estado_agentes)
    return {"timestamp": ahora_utc().isoformat(), "total": len(agentes), "agentes": agentes}


def _conteos_bd() -> Dict[str, int]:
    with sesion_bd() as s:
        return {
            "agent_metrics": int(s.execute(select(func.count()).select_from(AgentMetric)).scalar_one()),
            "revoked_tokens": int(s.execute(select(func.count()).select_from(RevokedToken)).scalar_one()),
            "admin_sessions": int(s.execute(select(func.count()).select_from(AdminSession)).scalar_one()),
        }


@router.get("/metrics")
async def metricas_sistema(claims: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    agentes = await run_in_threadpool(estado_agentes)
    jev = [a for a in agentes if a["tipo"] == "jev"]
    return {
        "timestamp": ahora_utc().isoformat(),
        "version": VERSION_UASI,
        "http": metricas_http.instantanea(),
        "jev": metricas_jev.instantanea(),
        "agentes_jev_24h": {
            "ejecuciones": sum(a["ejecuciones_24h"] for a in jev),
            "errores": sum(a["errores_24h"] for a in jev),
        },
        "ledger": {"bloques": await run_in_threadpool(ledger.contar)},
        "base_datos": await run_in_threadpool(_conteos_bd),
        "rate_limiting": {"backend": limitador_distribuido.backend, "jev_por_minuto": RATE_LIMIT_JEV_PER_MINUTE,
                          "admin_por_minuto": RATE_LIMIT_ADMIN_PER_MINUTE},
    }


@router.get("/ledger")
async def ledger_admin(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    claims: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    datos = await run_in_threadpool(ledger.paginar, page, limit)
    datos["integridad"] = await run_in_threadpool(ledger.verificar_integridad)
    return datos


@router.post("/anchor")
async def anclar_admin(
    sol: Optional[SolicitudAnclaje] = None, claims: Dict[str, Any] = Depends(require_admin)
) -> Dict[str, Any]:
    return await ejecutar_anclaje(sol.fecha if sol else None, claims["sub"])


@router.get("/health/detailed")
async def salud_detallada(claims: Dict[str, Any] = Depends(require_admin)) -> JSONResponse:
    datos = await run_in_threadpool(salud_dependencias)
    return JSONResponse(status_code=503 if datos["estado"] == "critico" else 200, content=datos)


@router.post("/tokens/revoke")
async def revocar(sol: SolicitudRevocacion, claims: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    nuevo = await run_in_threadpool(revocar_token, sol.jti, sol.sujeto, sol.motivo, str(claims.get("sub", "")))
    if nuevo:
        await run_in_threadpool(ledger.registrar, "REVOCACION_TOKEN",
                                {"jti": sol.jti, "sujeto": sol.sujeto, "motivo": sol.motivo}, claims["sub"])
    return {"jti": sol.jti, "revocado": True, "ya_estaba_revocado": not nuevo}
