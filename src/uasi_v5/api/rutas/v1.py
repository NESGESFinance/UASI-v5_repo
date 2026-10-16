"""API v1 (compatibilidad): entrenamiento con agentes legacy."""

from __future__ import annotations

from typing import Any, Callable, Dict
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.utilidades import ahora_utc, sha256_hex
from uasi_v5.esquemas.solicitudes import SolicitudProcesamientoV1
from uasi_v5.ml.agentes.legacy import (
    AgenteArbolDecisionLegacy,
    AgenteRedNeuronalLegacy,
    AgenteSVMLegacy,
    _AgenteLegacyBase,
)
from uasi_v5.ml.evaluacion import semilla_desde_contexto
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.servicios import ledger

router = APIRouter(tags=["v1"])

AGENTES_V1: Dict[str, Callable[[], _AgenteLegacyBase]] = {
    "red_neuronal": AgenteRedNeuronalLegacy,
    "svm": AgenteSVMLegacy,
    "arbol_decision": AgenteArbolDecisionLegacy,
}


@router.post("/api/v1/procesar")
async def procesar_v1(sol: SolicitudProcesamientoV1, claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    intencion = guardian.validar_intencion(sol.intencion, guardian.INTENCIONES_V1)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    X, informe = guardian.sanitizar_matriz(sol.inputs)
    y = np.asarray(sol.targets, dtype=int)
    resultado = await run_in_threadpool(AGENTES_V1[sol.tipo_agente]().entrenar, X, y, semilla_desde_contexto(meta))
    bloque = await run_in_threadpool(ledger.registrar, "PROCESAMIENTO_V1", {
        "id_solicitud": sol.id_solicitud, "intencion": intencion, "agente": resultado["agente"],
        "hash_datos": sha256_hex(X.tobytes() + y.tobytes()), "n_muestras": int(X.shape[0]),
        "n_caracteristicas": int(X.shape[1]), "metricas": resultado["metricas"],
        "metadatos_contexto": meta, "sanitizacion": informe}, claims["sub"])
    return {"id_ejecucion": str(uuid4()), "id_solicitud": sol.id_solicitud, "timestamp": ahora_utc().isoformat(),
            "sanitizacion": informe, **resultado, "bloque_ledger": bloque["indice"], "hash_bloque": bloque["hash_bloque"]}
