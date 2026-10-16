"""
UASI v5 Enterprise Production — punto de entrada ASGI y fachada de compatibilidad.

Desde v5.2.0 el código vive en un paquete modular por capas (ver docs/arquitectura.md):

    core/          configuración, logging, utilidades, excepciones
    seguridad/     JWT, rate limiting + límite de payload, gobernanza
    esquemas/      modelos Pydantic de entrada
    persistencia/  almacén seguro de modelos (checksum/HMAC + FileLock)
    ml/            evaluación honesta y agentes de aprendizaje
    orquestacion/  orquestador semántico de la ejecución maestra
    auditoria/     ledger hash-encadenado, Merkle y OpenTimestamps (Bitcoin L1)
    servicios.py   singletons (ledger, merkle_manager, orquestador) y health check
    api/           fábrica FastAPI, manejadores de error y routers REST

Este módulo crea la aplicación (`uasi_v5.main:app`, usado por gunicorn/uvicorn y
Docker) y reexporta los nombres públicos históricos del monolito v5.1.0, de modo
que `from uasi_v5.main import X` sigue funcionando sin cambios.

Uso:
    gunicorn uasi_v5.main:app -k uvicorn.workers.UvicornWorker -w 4
    python -m uasi_v5.main
"""

from __future__ import annotations

import os

from uasi_v5.agentes_jev import REGISTRO_AGENTES, AgenteJEVBase, DispatcherMultiagenteJEV
from uasi_v5.agentes_jev.automatizacion import TAREAS_NO_CACHEABLES, CacheResultadosJEV, MetricasJEV
from uasi_v5.agentes_jev.callback import enviar_callback, validar_url_callback
from uasi_v5.api.app import crear_app, lifespan
from uasi_v5.api.rutas.v1 import AGENTES_V1
from uasi_v5.auditoria.ledger import LedgerAuditoriaCriptografica
from uasi_v5.auditoria.merkle_ots import HTTPX_DISPONIBLE, OTS_DISPONIBLE, OTS_ERROR_IMPORT, MerkleOpenTimestampsManager
from uasi_v5.core.config import (
    ARCHIVO_LEDGER,
    BITCOIN_NETWORK,
    ESPLORA_API_URL,
    JEV_HMAC_KEY,
    JEV_MAX_WORKERS,
    JWT_ALGORITHM,
    JWT_EXP_MINUTES,
    JWT_ISSUER,
    MAX_FEATURES,
    MAX_MUESTRAS,
    MAX_PAYLOAD_BYTES,
    OTS_CALENDARS,
    OTS_MIN_CALENDARS,
    OTS_TIMEOUT_SECONDS,
    PATH_LEDGER,
    PATH_MODELOS,
    PATH_OTS,
    PATH_WORKSPACE,
    RATE_LIMIT_PER_MINUTE,
    RATE_LIMIT_STORAGE_URI,
    SECRET_JWT_KEY,
    UASI_ENV,
    UASI_MODEL_HMAC_KEY,
    UMBRAL_ENTROPIA_DESBALANCE,
    UMBRAL_ENTROPIA_FEATURES,
    VERSION_UASI,
    _env_float,
    _env_int,
)
from uasi_v5.core.excepciones import (
    ConflictoClasesStreaming,
    ErrorGobernanza,
    ErrorValidacionDatos,
    IntegridadModeloError,
)
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc, json_canonico, sha256_hex
from uasi_v5.esquemas.jev import LenguajeDestino, LoteJEV, RespuestaJEV, ResultadoAgenteJEV, SolicitudJEV, TipoTareaJEV
from uasi_v5.esquemas.solicitudes import (
    FilaNumerica,
    SolicitudMaestraV5,
    SolicitudProcesamientoV1,
    SolicitudStreaming,
)
from uasi_v5.ml.agentes.ensamblado import AgenteEnsambladoExplicable
from uasi_v5.ml.agentes.evolutivo import AgenteVectorialEvolutivo
from uasi_v5.ml.agentes.legacy import (
    AgenteArbolDecisionLegacy,
    AgenteRedNeuronalLegacy,
    AgenteSVMLegacy,
)
from uasi_v5.ml.agentes.metacognitivo import AgenteAnalistaDatosMetacognitivo
from uasi_v5.ml.agentes.profundo import AgenteCerebralProfundo
from uasi_v5.ml.agentes.streaming import MotorStreamingContinuo
from uasi_v5.ml.evaluacion import (
    calcular_metricas_clasificacion,
    construir_cv_seguro,
    dividir_holdout,
    entrenar_y_evaluar_sklearn,
    puntuaciones_modelo,
    semilla_desde_contexto,
)
from uasi_v5.orquestacion.orquestador import OrquestadorSemanticoUASI
from uasi_v5.persistencia.almacen_modelos import (
    cargar_modelo_seguro,
    guardar_modelo_seguro,
    ruta_modelo,
    verificar_checksum,
)
from uasi_v5.seguridad.autenticacion import api_key_header, emitir_token, require_auth
from uasi_v5.seguridad.gobernanza import GuardiánGobernanzaSeguridad, guardian
from uasi_v5.seguridad.jev_firma import firmar_jev, jev_configurado, verificar_firma_jev
from uasi_v5.seguridad.jev_replay import RegistroIdempotenciaJEV, verificar_ventana
from uasi_v5.seguridad.limites import LimiteTamanoPayloadMiddleware, limiter
from uasi_v5.servicios import (
    MODELOS_ESPERADOS,
    cache_jev,
    dispatcher_jev,
    estado_salud_detallado,
    idempotencia_jev,
    ledger,
    merkle_manager,
    metricas_jev,
    orquestador,
)

app = crear_app()

__all__ = [
    # Aplicación
    "app", "crear_app", "lifespan", "AGENTES_V1",
    # Configuración
    "VERSION_UASI", "UASI_ENV", "PATH_WORKSPACE", "PATH_MODELOS", "PATH_LEDGER", "PATH_OTS", "ARCHIVO_LEDGER",
    "SECRET_JWT_KEY", "JWT_ALGORITHM", "JWT_ISSUER", "JWT_EXP_MINUTES", "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_STORAGE_URI", "MAX_PAYLOAD_BYTES", "MAX_MUESTRAS", "MAX_FEATURES", "UASI_MODEL_HMAC_KEY",
    "BITCOIN_NETWORK", "OTS_CALENDARS", "OTS_MIN_CALENDARS", "OTS_TIMEOUT_SECONDS", "ESPLORA_API_URL",
    "UMBRAL_ENTROPIA_DESBALANCE", "UMBRAL_ENTROPIA_FEATURES", "OTS_DISPONIBLE", "OTS_ERROR_IMPORT",
    "HTTPX_DISPONIBLE", "_env_int", "_env_float",
    # Núcleo
    "logger", "ahora_utc", "json_canonico", "sha256_hex",
    "ErrorValidacionDatos", "ErrorGobernanza", "IntegridadModeloError", "ConflictoClasesStreaming",
    # Seguridad
    "api_key_header", "emitir_token", "require_auth", "limiter", "LimiteTamanoPayloadMiddleware",
    "GuardiánGobernanzaSeguridad", "guardian",
    # Esquemas
    "FilaNumerica", "SolicitudProcesamientoV1", "SolicitudMaestraV5", "SolicitudStreaming",
    # Persistencia y evaluación
    "guardar_modelo_seguro", "cargar_modelo_seguro", "verificar_checksum", "ruta_modelo",
    "calcular_metricas_clasificacion", "dividir_holdout", "puntuaciones_modelo", "construir_cv_seguro",
    "semilla_desde_contexto", "entrenar_y_evaluar_sklearn",
    # Agentes y orquestación
    "AgenteRedNeuronalLegacy", "AgenteSVMLegacy", "AgenteArbolDecisionLegacy", "MotorStreamingContinuo",
    "AgenteCerebralProfundo", "AgenteVectorialEvolutivo", "AgenteEnsambladoExplicable",
    "AgenteAnalistaDatosMetacognitivo", "OrquestadorSemanticoUASI", "orquestador",
    # Auditoría
    "LedgerAuditoriaCriptografica", "MerkleOpenTimestampsManager", "ledger", "merkle_manager",
    "MODELOS_ESPERADOS", "estado_salud_detallado",
    # JEV (JEV → UASI multiagente → JEV)
    "JEV_MAX_WORKERS", "JEV_HMAC_KEY", "TipoTareaJEV", "LenguajeDestino", "SolicitudJEV", "RespuestaJEV",
    "ResultadoAgenteJEV", "AgenteJEVBase", "DispatcherMultiagenteJEV", "REGISTRO_AGENTES", "dispatcher_jev",
    "firmar_jev", "verificar_firma_jev", "jev_configurado",
    # JEV: automatización y optimización
    "LoteJEV", "CacheResultadosJEV", "MetricasJEV", "TAREAS_NO_CACHEABLES", "RegistroIdempotenciaJEV",
    "verificar_ventana", "enviar_callback", "validar_url_callback", "cache_jev", "metricas_jev", "idempotencia_jev",
]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("uasi_v5.main:app", host=os.getenv("HOST", "0.0.0.0"),
                port=_env_int("PORT", 8000), workers=_env_int("UVICORN_WORKERS", 1))
