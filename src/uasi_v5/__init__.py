"""
UASI v5 Enterprise Production.

Sistema Unificado de Agentes de Aprendizaje con Supervisión Inteligente:
API FastAPI con agentes de ML gobernados, ledger de auditoría hash-encadenado
y anclaje diario de la raíz de Merkle en Bitcoin L1 vía OpenTimestamps.

El módulo principal (`uasi_v5.main`) lee su configuración de las variables de
entorno EN EL MOMENTO DE IMPORTARSE. Por eso los exports públicos se resuelven
de forma perezosa (PEP 562): `import uasi_v5` no fija la configuración; solo el
primer acceso a `uasi_v5.app` (u otro export) importa `uasi_v5.main`.

Uso:
    gunicorn uasi_v5.main:app -k uvicorn.workers.UvicornWorker -w 4
    from uasi_v5 import app, emitir_token
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "5.2.0-enterprise"

__all__ = [
    "__version__",
    # Aplicación y seguridad
    "app",
    "emitir_token",
    "require_auth",
    # Gobernanza
    "GuardiánGobernanzaSeguridad",
    "guardian",
    # Agentes
    "AgenteRedNeuronalLegacy",
    "AgenteSVMLegacy",
    "AgenteArbolDecisionLegacy",
    "MotorStreamingContinuo",
    "AgenteCerebralProfundo",
    "AgenteVectorialEvolutivo",
    "AgenteEnsambladoExplicable",
    "AgenteAnalistaDatosMetacognitivo",
    "OrquestadorSemanticoUASI",
    # Auditoría y Bitcoin
    "LedgerAuditoriaCriptografica",
    "MerkleOpenTimestampsManager",
    "ledger",
    "merkle_manager",
    # Persistencia segura
    "guardar_modelo_seguro",
    "cargar_modelo_seguro",
    "verificar_checksum",
    # Excepciones de dominio
    "ErrorValidacionDatos",
    "ErrorGobernanza",
    "IntegridadModeloError",
    "ConflictoClasesStreaming",
]


def __getattr__(nombre: str) -> Any:
    if nombre in __all__:
        return getattr(importlib.import_module("uasi_v5.main"), nombre)
    raise AttributeError(f"module 'uasi_v5' has no attribute {nombre!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
