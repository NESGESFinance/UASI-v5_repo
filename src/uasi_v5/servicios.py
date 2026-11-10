"""
Contenedor de servicios: instancias únicas (singletons de proceso) compartidas por
la API y el orquestador, y el health check detallado.

Es el único punto donde se "cablean" las dependencias entre capas:
    ledger ──► merkle_manager
    ledger + guardian + estado_salud_detallado ──► orquestador
    ledger.verificar_integridad + estado_salud_detallado + cache_jev ──► dispatcher_jev (capa JEV)
    metricas_jev, idempotencia_jev ──► api/rutas/jev.py
    RATE_LIMIT_STORAGE_URI ──► crear_backend_jev (Redis compartido o memoria)
    RATE_LIMIT_STORAGE_URI ──► limitador_distribuido (rate limiting por grupo de rutas)
    UASI_LEDGER_BACKEND ──► ledger (LedgerInmutableSQL por defecto; JSONL heredado)
    agent_metrics (BD) + REGISTRO_AGENTES + modelos ML ──► estado_agentes (admin y /ws/agents)
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Union
from uuid import uuid4

from sqlalchemy import func, select

from uasi_v5.agentes_jev.automatizacion_redis import conectar_redis, crear_backend_jev
from uasi_v5.agentes_jev.dispatcher import REGISTRO_AGENTES, DispatcherMultiagenteJEV
from uasi_v5.auditoria.ledger import LedgerAuditoriaCriptografica, LedgerInmutableSQL
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
    UASI_LEDGER_BACKEND,
    VERSION_UASI,
)
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.db.modelos import AgentMetric
from uasi_v5.db.sesion import comprobar_bd, sesion_bd
from uasi_v5.esquemas.jev import ResultadoAgenteJEV
from uasi_v5.orquestacion.orquestador import OrquestadorSemanticoUASI
from uasi_v5.persistencia.almacen_modelos import ruta_modelo, verificar_checksum
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.seguridad.limites import LimitadorDistribuido, MetricasHTTP

ledger: Union[LedgerInmutableSQL, LedgerAuditoriaCriptografica] = (
    LedgerInmutableSQL() if UASI_LEDGER_BACKEND == "sql" else LedgerAuditoriaCriptografica()
)
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


# Rate limiting distribuido (Redis si RATE_LIMIT_STORAGE_URI=redis://…; si no, memoria por worker).
limitador_distribuido = LimitadorDistribuido(RATE_LIMIT_STORAGE_URI)
metricas_http = MetricasHTTP()


def registrar_metricas_agentes(tipo_tarea: str, resultados: Iterable[ResultadoAgenteJEV]) -> None:
    """Persiste una fila de agent_metrics por agente ejecutado (no interrumpe la petición si falla)."""
    try:
        with sesion_bd() as s:
            for r in resultados:
                s.add(AgentMetric(agente=r.lenguaje, tipo="jev", tarea=tipo_tarea, estado=r.estado,
                                  duracion_ms=float(r.duracion_ms), desde_cache=int(bool(r.desde_cache)),
                                  error=(r.mensaje_error or None)))
    except Exception:  # noqa: BLE001 - la métrica es best-effort; el despacho ya está en el ledger
        logger.exception("No se pudieron persistir las métricas de agentes JEV")


def resumen_agentes_jev(horas: int = 24) -> Dict[str, Dict[str, Any]]:
    """Por agente JEV: ejecuciones, latencia media (sin aciertos de caché), errores y última actividad."""
    desde = ahora_utc() - timedelta(hours=horas)
    resumen: Dict[str, Dict[str, Any]] = {}
    with sesion_bd() as s:
        filas = s.execute(
            select(AgentMetric.agente, AgentMetric.estado, AgentMetric.desde_cache,
                   func.count(), func.avg(AgentMetric.duracion_ms), func.max(AgentMetric.creado))
            .where(AgentMetric.tipo == "jev", AgentMetric.creado >= desde)
            .group_by(AgentMetric.agente, AgentMetric.estado, AgentMetric.desde_cache)
        ).all()
        ultimas = dict(s.execute(select(AgentMetric.agente, func.max(AgentMetric.creado))
                                 .where(AgentMetric.tipo == "jev").group_by(AgentMetric.agente)).all())
    for agente, estado, cache, n, media, _ultima in filas:
        r = resumen.setdefault(agente, {"ejecuciones_24h": 0, "errores_24h": 0, "_suma_ms": 0.0, "_reales": 0})
        r["ejecuciones_24h"] += int(n)
        if estado != "exito":
            r["errores_24h"] += int(n)
        if not cache:
            r["_suma_ms"] += float(media or 0.0) * int(n)
            r["_reales"] += int(n)
    for r in resumen.values():
        r["latencia_media_ms"] = round(r.pop("_suma_ms") / r["_reales"], 3) if r["_reales"] else 0.0
        r.pop("_reales")
    for agente, ultima in ultimas.items():
        resumen.setdefault(agente, {"ejecuciones_24h": 0, "errores_24h": 0, "latencia_media_ms": 0.0})
        resumen[agente]["ultima_actividad"] = _iso(ultima)
    return resumen


def _iso(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return (valor if valor.tzinfo else valor.replace(tzinfo=UTC)).isoformat()
    return str(valor)


def estado_agentes() -> List[Dict[str, Any]]:
    """
    Estado de todos los sub-agentes: los 7 agentes JEV por lenguaje y los 7 modelos ML.
    Estados: activo | inactivo | degradado (JEV, por tasa de error 24h) y
    entrenado | no_entrenado | integridad_comprometida (ML).
    """
    resumen = resumen_agentes_jev()
    agentes: List[Dict[str, Any]] = []
    for lenguaje, clase in REGISTRO_AGENTES.items():
        r = resumen.get(lenguaje, {})
        ejec, err = int(r.get("ejecuciones_24h", 0)), int(r.get("errores_24h", 0))
        estado = "inactivo" if ejec == 0 else ("degradado" if err / ejec > 0.5 else "activo")
        agentes.append({"id": f"jev:{lenguaje}", "nombre": clase.__name__, "tipo": "jev", "estado": estado,
                        "ultima_actividad": r.get("ultima_actividad"), "ejecuciones_24h": ejec,
                        "errores_24h": err, "latencia_media_ms": r.get("latencia_media_ms", 0.0)})
    for nombre in MODELOS_ESPERADOS:
        ruta = ruta_modelo(nombre)
        if not os.path.exists(ruta):
            estado, ultima = "no_entrenado", None
        else:
            ok, _ = verificar_checksum(ruta)
            estado = "entrenado" if ok else "integridad_comprometida"
            ultima = datetime.fromtimestamp(os.path.getmtime(ruta), UTC).isoformat()
        agentes.append({"id": f"ml:{nombre}", "nombre": nombre, "tipo": "ml", "estado": estado,
                        "ultima_actividad": ultima, "ejecuciones_24h": None, "errores_24h": None,
                        "latencia_media_ms": None})
    return agentes


def estado_redis() -> Dict[str, Any]:
    """Estado del backend Redis compartido (o indicación de que se usa memoria)."""
    if not RATE_LIMIT_STORAGE_URI.startswith(("redis://", "rediss://")):
        return {"estado": "no_configurado", "backend": "memoria",
                "detalle": "RATE_LIMIT_STORAGE_URI no es redis://; estado por worker."}
    inicio = time.perf_counter()
    cliente = conectar_redis(RATE_LIMIT_STORAGE_URI)
    if cliente is None:
        return {"estado": "error", "backend": "memoria (fallback)"}
    return {"estado": "ok", "backend": "redis", "latencia_ms": round((time.perf_counter() - inicio) * 1000, 3)}


def salud_dependencias() -> Dict[str, Any]:
    """Health check detallado para administradores: base + Redis + BD + agentes JEV."""
    base = estado_salud_detallado()
    redis_estado = estado_redis()
    bd = comprobar_bd()
    agentes = [a for a in estado_agentes() if a["tipo"] == "jev"]
    componentes = {**base["componentes"], "redis": redis_estado, "base_datos": bd,
                   "agentes_jev": {"registrados": len(agentes),
                                   "degradados": [a["id"] for a in agentes if a["estado"] == "degradado"],
                                   "agentes": agentes},
                   "rate_limiting": {"backend": limitador_distribuido.backend}}
    estado = base["estado"]
    if bd["estado"] != "ok":
        estado = "critico"
    elif redis_estado["estado"] == "error" and estado == "ok":
        estado = "degradado"
    return {**base, "estado": estado, "componentes": componentes}
