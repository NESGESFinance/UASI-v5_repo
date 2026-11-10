"""API v5: integración de observación con NESGESFinanceTrust.

Expone, de forma protegida (JWT), una vista de solo lectura del estado on-chain del
ecosistema NESGESFinance (runa NGF•BTC•AM, activos RWA con cumplimiento MiCA). Todas
las rutas degradan con elegancia: si la plataforma externa no responde, se devuelve
``conectado: false`` con el detalle del error, nunca un 5xx.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from uasi_v5.integraciones.nesgesfinancetrust import (
    ClienteNESGESFinanceTrust,
    obtener_cliente_trust,
)
from uasi_v5.seguridad.autenticacion import require_auth

router = APIRouter(prefix="/api/v5/trust", tags=["nesgesfinancetrust"])


@router.get("/estado")
async def estado(
    claims: Dict[str, Any] = Depends(require_auth),
    cliente: ClienteNESGESFinanceTrust = Depends(obtener_cliente_trust),
) -> Dict[str, Any]:
    """Resumen agregado del ecosistema on-chain (salud + runa principal + RWA/MiCA)."""
    return await cliente.resumen()


@router.get("/runes")
async def runes(
    limite: int = Query(20, ge=1, le=100),
    claims: Dict[str, Any] = Depends(require_auth),
    cliente: ClienteNESGESFinanceTrust = Depends(obtener_cliente_trust),
) -> Dict[str, Any]:
    """Listado de runas indexadas por NESGESFinanceTrust."""
    return await cliente.runes(limite)


@router.get("/rwa")
async def rwa(
    limite: int = Query(20, ge=1, le=100),
    claims: Dict[str, Any] = Depends(require_auth),
    cliente: ClienteNESGESFinanceTrust = Depends(obtener_cliente_trust),
) -> Dict[str, Any]:
    """Activos del mundo real (RWA) tokenizados y su clasificación."""
    return await cliente.activos_rwa(limite)
