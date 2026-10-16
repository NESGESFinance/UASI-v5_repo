"""Rutas de auditoría: ledger criptográfico, anclaje Merkle y verificación OpenTimestamps."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from uasi_v5.auditoria.merkle_ots import OTS_DISPONIBLE, MerkleOpenTimestampsManager
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.servicios import ledger, merkle_manager

router = APIRouter(tags=["auditoria"])


@router.get("/api/v5/cryptographic-ledger")
async def cryptographic_ledger(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    verificar: bool = Query(False, description="Verifica la cadena completa (coste O(n))."),
    claims: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    datos = await run_in_threadpool(ledger.paginar, page, page_size)
    if verificar:
        datos["integridad"] = await run_in_threadpool(ledger.verificar_integridad)
    return datos


@router.get("/api/v5/merkle-anchor")
async def merkle_anchor(
    fecha: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Por defecto: hoy (UTC)."),
    claims: Dict[str, Any] = Depends(require_auth),
) -> Dict[str, Any]:
    fecha = fecha or ahora_utc().date().isoformat()
    bloques = await run_in_threadpool(ledger.bloques_de_fecha, fecha)
    if not bloques:
        raise HTTPException(status_code=404, detail=f"No hay bloques en el ledger para {fecha}.")
    raiz = MerkleOpenTimestampsManager.calcular_raiz_merkle(bloques)
    resultado = await merkle_manager.anclar_en_bitcoin(raiz, fecha, n_bloques=len(bloques))
    await run_in_threadpool(ledger.registrar, "ANCLAJE_MERKLE_OTS", {
        "fecha": fecha, "raiz_merkle": raiz, "n_bloques": len(bloques), "estado": resultado.get("estado"),
        "calendarios": resultado.get("calendarios") or resultado.get("calendarios_aceptados")}, claims["sub"])
    return {"fecha": fecha, "raiz_merkle": raiz, "n_bloques": len(bloques), "anclaje": resultado}


@router.get("/api/v5/verify-ots/{fecha}")
async def verify_ots(fecha: str, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    ruta_ots, _ = merkle_manager._rutas(fecha)
    if not OTS_DISPONIBLE:
        return MerkleOpenTimestampsManager._estado_no_disponible()
    if not os.path.exists(ruta_ots):
        raise HTTPException(status_code=404, detail=f"No existe prueba OTS para {fecha}.")
    return await merkle_manager.verificar_prueba_ots(ruta_ots)
