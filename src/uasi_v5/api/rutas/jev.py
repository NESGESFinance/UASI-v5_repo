"""Capa JEV: JEV → UASI (dispatcher multiagente por lenguaje) → JEV, con firma HMAC-SHA256."""

from __future__ import annotations

import time
from typing import Any, get_args

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.config import VERSION_UASI
from uasi_v5.core.utilidades import json_canonico, sha256_hex
from uasi_v5.esquemas.jev import LenguajeDestino, RespuestaJEV, SolicitudJEV, TipoTareaJEV
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.seguridad.jev_firma import firmar_jev, jev_configurado, verificar_firma_jev
from uasi_v5.servicios import dispatcher_jev, ledger

router = APIRouter(tags=["jev"])


@router.post("/api/v5/jev-dispatch", response_model=RespuestaJEV)
async def jev_dispatch(
    sol: SolicitudJEV, request: Request, claims: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    inicio = time.perf_counter()
    # La firma se verifica sobre el JSON EXACTO emitido por JEV (no sobre el modelo con
    # valores por defecto rellenados por UASI, como id_solicitud o timestamp).
    cuerpo = await request.json()
    if not isinstance(cuerpo, dict):
        raise HTTPException(status_code=422, detail="El cuerpo debe ser un objeto JSON.")
    verificar_firma_jev(cuerpo)
    meta = guardian.validar_metadatos(sol.metadatos)

    despacho = await run_in_threadpool(
        dispatcher_jev.despachar, sol.tipo_tarea, sol.lenguajes_destino, sol.datos, meta
    )
    resultados = despacho["resultados_por_lenguaje"]
    bloque = await run_in_threadpool(ledger.registrar, "DISPATCH_JEV", {
        "id_jev": sol.id_jev, "id_solicitud": sol.id_solicitud, "version_jev": sol.version_jev,
        "tipo_tarea": sol.tipo_tarea, "lenguajes": list(resultados), "estado": despacho["estado"],
        "hash_datos": sha256_hex(json_canonico(sol.datos).encode()),
        "hashes_resultado": {lang: r.hash_resultado for lang, r in resultados.items()},
        "metadatos": meta}, claims["sub"])

    respuesta = RespuestaJEV(
        id_jev=sol.id_jev,
        estado=despacho["estado"],
        tipo_tarea=sol.tipo_tarea,
        lenguajes_ejecutados=despacho["lenguajes_ejecutados"],
        resultados_por_lenguaje=resultados,
        bloque_ledger={"indice": bloque["indice"], "hash_bloque": bloque["hash_bloque"]},
        duracion_total_ms=round((time.perf_counter() - inicio) * 1000, 3),
        version_uasi=VERSION_UASI,
        errores_globales=despacho["errores_globales"],
    )
    contenido = respuesta.model_dump(mode="json")
    contenido["firma_respuesta"] = firmar_jev(contenido)  # firma sobre el cuerpo sin firma_respuesta
    return JSONResponse(content=contenido)


@router.get("/api/v5/jev/agentes")
async def jev_agentes(claims: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    return {
        "lenguajes": dispatcher_jev.lenguajes,
        "lenguajes_protocolo": list(get_args(LenguajeDestino)),
        "tipos_tarea": list(get_args(TipoTareaJEV)),
        "max_workers": dispatcher_jev.max_workers,
        "firma": "HMAC-SHA256 hex sobre JSON canónico sin firma_hmac/firma_respuesta",
        "jev_configurado": jev_configurado(),
    }
