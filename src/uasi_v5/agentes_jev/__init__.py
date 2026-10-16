"""Capa multiagente JEV: agentes deterministas por lenguaje y dispatcher paralelo.

Flujo: JEV → (SolicitudJEV firmada) → UASI → DispatcherMultiagenteJEV → agentes
python / typescript / javascript / solidity / rust / go / java → (RespuestaJEV firmada) → JEV.
Ningún agente ejecuta código: sólo infieren tipos y generan bindings como texto.
"""

from uasi_v5.agentes_jev.base import AgenteJEVBase, ejecutar_agente, inferir_campos, inferir_tipo
from uasi_v5.agentes_jev.dispatcher import REGISTRO_AGENTES, DispatcherMultiagenteJEV

__all__ = [
    "REGISTRO_AGENTES",
    "AgenteJEVBase",
    "DispatcherMultiagenteJEV",
    "ejecutar_agente",
    "inferir_campos",
    "inferir_tipo",
]
