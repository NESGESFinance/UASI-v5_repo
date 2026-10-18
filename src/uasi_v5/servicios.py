"""
Contenedor de servicios: instancias únicas (singletons de proceso) compartidas por
la API y el orquestador, y el health check detallado.

Es el único punto donde se "cablean" las dependencias entre capas:
    ledger ──► merkle_manager
    ledger + guardian + estado_salud_detallado ──► orquestador
    ledger.verificar_integridad + estado_salud_detallado + cache_jev ──► dispatcher_jev (capa JEV)
    metricas_jev, idempotencia_jev ──► api/rutas/jev.py
    RATE_LIMIT_STORAGE_URI ──► crear_backend_jev (Redis compartido o memoria)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Dict
from uuid import uuid4

from uasi_v5.agentes_jev.automatizacion_redis import crear_backend_jev
from uasi_v5.agentes_jev.dispatcher import DispatcherMultiagenteJEV
from uasi_v5.auditoria.ledger import LedgerAuditoriaCriptografica
from uasi_v5.auditoria.merkle_ots import OTS_DISPONIBLE, OTS_ERROR_IMPORT, MerkleOpenTimestampsManager
from uasi_v5.core.config import (
    BITCOIN_NETWORK,
    JEV_CACHE_MAX,
    JEV_CACHE_TTL,
    JEV_MAX_WORKERS,
    OTS_CALENDARS,
    PATH_WORKSPACE,
    RATE_LIMIT_STORAGE_URI,
    SECRET_JWT_KEY,
    UASI_ENV,
    VERSION_UASI,
)
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.orquestacion.orquestador import OrquestadorSemanticoUASI
from uasi_v5.persistencia.almacen_modelos import ruta_modelo, verificar_checksum
from uasi_v5.seguridad.gobernanza import guardian

ledger = LedgerAuditoriaCriptografica()
merkle_manager = MerkleOpenTimestampsManager(ledger)

MODELOS_ESPERADOS = ("red_neuronal_legacy", "svm_legacy", "arbol_legacy", "streaming_sgd",
                     "cerebral_profundo", "vectorial_evolutivo", "ensamblado_gb")


def estado_salud_detallado() -> Dict[str, Any]:
    """Health check REAL: workspace escribible, checksums de modelos, integridad del ledger."""
    componentes: Dict[str, Any] = {}
    critico = degradado = False
    try:
        os.makedirs(PATH_WORKSPACE, exist_ok=True)
        prueba = os.path.join(PATH_WORKSPACE, f".healthcheck-{uuid4().hex}")
        with open(prueba, "w") as f:
            f.write("ok")
        os.remove(prueba)
        componentes["workspace"] = {"ruta": PATH_WORKSPACE, "escribible": True}
    except OSError as exc:
        componentes["workspace"] = {"ruta": PATH_WORKSPACE, "escribible": False, "error": str(exc)}
        critico = True

    modelos = {}
    for nombre in MODELOS_ESPERADOS:
        ruta = ruta_modelo(nombre)
        if not os.path.exists(ruta):
            modelos[nombre] = {"estado": "no_entrenado"}
            continue
        ok, motivo = verificar_checksum(ruta)
        modelos[nombre] = {"estado": "ok" if ok else "integridad_comprometida", "checksum": motivo,
                           "tamano_bytes": os.path.getsize(ruta),
                           "modificado_utc": datetime.fromtimestamp(os.path.getmtime(ruta), UTC).isoformat()}
        degradado = degradado or not ok
    componentes["modelos"] = modelos

    integridad = ledger.verificar_integridad()
    componentes["ledger"] = integridad
    critico = critico or not integridad["integro"]

    componentes["autenticacion"] = {"configurada": bool(SECRET_JWT_KEY)}
    degradado = degradado or not SECRET_JWT_KEY
    componentes["opentimestamps"] = {"disponible": OTS_DISPONIBLE, "calendarios": OTS_CALENDARS,
                                     "red_bitcoin": BITCOIN_NETWORK, "error_import": OTS_ERROR_IMPORT}
    estado = "critico" if critico else ("degradado" if degradado else "ok")
    return {"estado": estado, "version": VERSION_UASI, "entorno": UASI_ENV,
            "timestamp": ahora_utc().isoformat(), "componentes": componentes}


orquestador = OrquestadorSemanticoUASI(ledger_ref=ledger, guardian_ref=guardian, salud=estado_salud_detallado)

# Con RATE_LIMIT_STORAGE_URI=redis://... el estado JEV se comparte entre workers/réplicas;
# si Redis no está configurado o no responde, se usan las implementaciones en memoria.
cache_jev, metricas_jev, idempotencia_jev = crear_backend_jev(
    RATE_LIMIT_STORAGE_URI, max_entradas=JEV_CACHE_MAX, ttl_segundos=JEV_CACHE_TTL
)

dispatcher_jev = DispatcherMultiagenteJEV(
    max_workers=JEV_MAX_WORKERS, verificar_ledger=ledger.verificar_integridad, salud=estado_salud_detallado,
    cache=cache_jev,
)
