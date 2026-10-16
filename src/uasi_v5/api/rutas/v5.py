"""API v5: ejecución maestra orquestada e ingesta en streaming."""

from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.utilidades import ahora_utc, sha256_hex
from uasi_v5.esquemas.solicitudes import SolicitudMaestraV5, SolicitudStreaming
from uasi_v5.ml.agentes.streaming import MotorStreamingContinuo
from uasi_v5.ml.evaluacion import semilla_desde_contexto
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.servicios import ledger, orquestador

router = APIRouter(tags=["v5"])


@router.post("/api/v5/uasi-master-execute")
async def uasi_master_execute(sol: SolicitudMaestraV5, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    categorias = guardian.clasificar_prompt_master(sol.prompt_intencion)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    salida = await run_in_threadpool(orquestador.ejecutar, sol, categorias, meta)
    resultado = salida.get("resultado", {})
    bloque = await run_in_threadpool(ledger.registrar, "EJECUCION_MAESTRA_V5", {
        "id_solicitud": sol.id_solicitud, "plan": salida["plan"], "ruta": salida.get("ruta_ejecutada"),
        "hash_datos": salida.get("hash_datos"), "metricas": resultado.get("metricas") if isinstance(resultado, dict) else None,
        "metadatos_contexto": meta}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            **salida, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}


@router.post("/api/v5/streaming-ingest")
async def streaming_ingest(sol: SolicitudStreaming, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    guardian.validar_intencion(sol.intencion, guardian.INTENCIONES_STREAMING)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    X, informe = guardian.sanitizar_matriz(sol.inputs)
    y = np.asarray(sol.targets, dtype=int)
    resultado = await run_in_threadpool(MotorStreamingContinuo().ingerir, X, y, sol.clases_declaradas,
                                        sol.reiniciar_modelo, semilla_desde_contexto(meta))
    bloque = await run_in_threadpool(ledger.registrar, "INGESTA_STREAMING", {
        "id_solicitud": sol.id_solicitud, "hash_datos": sha256_hex(X.tobytes() + y.tobytes()),
        "n_muestras": int(X.shape[0]), "version_modelo": resultado["version_modelo"],
        "metricas": resultado["metricas"], "metadatos_contexto": meta, "sanitizacion": informe}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            "sanitizacion": informe, **resultado, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}
