"""Orquestador semántico: categorías del guardián → plan con efecto operativo."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import numpy as np

from uasi_v5.auditoria.ledger import LedgerAuditoriaCriptografica
from uasi_v5.auditoria.merkle_ots import MerkleOpenTimestampsManager
from uasi_v5.core.excepciones import ErrorValidacionDatos
from uasi_v5.core.utilidades import ahora_utc, sha256_hex
from uasi_v5.esquemas.solicitudes import SolicitudMaestraV5
from uasi_v5.ml.agentes.ensamblado import AgenteEnsambladoExplicable
from uasi_v5.ml.agentes.evolutivo import AgenteVectorialEvolutivo
from uasi_v5.ml.agentes.metacognitivo import AgenteAnalistaDatosMetacognitivo
from uasi_v5.ml.agentes.profundo import AgenteCerebralProfundo
from uasi_v5.ml.agentes.streaming import MotorStreamingContinuo
from uasi_v5.ml.evaluacion import semilla_desde_contexto
from uasi_v5.seguridad.gobernanza import GuardiánGobernanzaSeguridad


class OrquestadorSemanticoUASI:
    """
    Convierte las categorías detectadas por el guardián en un PLAN con efecto
    operativo real (no meramente informativo):
      DIAGNOSTICO                  → health check detallado
      AUDITORIA                    → integridad del ledger + raíz Merkle del día
      APRENDIZAJE_ONLINE_CONTINUO  → fuerza la ruta de streaming
      EXPLICABILIDAD               → Gradient Boosting con importancias
      OPTIMIZACION_EVOLUTIVA       → SVM evolutivo
      APRENDIZAJE_PROFUNDO         → red profunda
      ANALISIS_METACOGNITIVO_AUTO  → decide el analista metacognitivo
    """

    PRIORIDAD = (
        "DIAGNOSTICO", "AUDITORIA", "APRENDIZAJE_ONLINE_CONTINUO", "EXPLICABILIDAD",
        "OPTIMIZACION_EVOLUTIVA", "APRENDIZAJE_PROFUNDO", "ANALISIS_METACOGNITIVO_AUTO",
    )
    RUTA_POR_PLAN = {
        "APRENDIZAJE_ONLINE_CONTINUO": "streaming",
        "EXPLICABILIDAD": "ensamblado_explicable",
        "OPTIMIZACION_EVOLUTIVA": "vectorial_evolutivo",
        "APRENDIZAJE_PROFUNDO": "cerebral_profundo",
    }

    def __init__(
        self,
        ledger_ref: Optional[LedgerAuditoriaCriptografica] = None,
        guardian_ref: Optional[GuardiánGobernanzaSeguridad] = None,
        salud: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        # Dependencias inyectadas (evita el import circular con `uasi_v5.servicios`).
        # Si no se pasan, se resuelven perezosamente contra los singletons del servicio.
        self._ledger, self._guardian, self._salud = ledger_ref, guardian_ref, salud
        self.analista = AgenteAnalistaDatosMetacognitivo()

    @property
    def ledger(self) -> LedgerAuditoriaCriptografica:
        if self._ledger is None:
            from uasi_v5 import servicios

            self._ledger = servicios.ledger
        return self._ledger

    @property
    def guardian(self) -> GuardiánGobernanzaSeguridad:
        if self._guardian is None:
            from uasi_v5.seguridad.gobernanza import guardian

            self._guardian = guardian
        return self._guardian

    def estado_salud(self) -> Dict[str, Any]:
        if self._salud is None:
            from uasi_v5 import servicios

            self._salud = servicios.estado_salud_detallado
        return self._salud()

    def planificar(self, categorias: List[str]) -> str:
        return next(p for p in self.PRIORIDAD if p in categorias)

    def ejecutar(self, sol: SolicitudMaestraV5, categorias: List[str], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Síncrono y CPU-bound: se invoca SIEMPRE vía run_in_threadpool."""
        plan = self.planificar(categorias)
        salida: Dict[str, Any] = {"plan": plan, "categorias_detectadas": categorias}
        if plan == "DIAGNOSTICO":
            salida["ruta_ejecutada"] = "diagnostico"
            salida["resultado"] = self.estado_salud()
            return salida
        if plan == "AUDITORIA":
            hoy = ahora_utc().date().isoformat()
            bloques_hoy = self.ledger.bloques_de_fecha(hoy)
            salida["ruta_ejecutada"] = "auditoria"
            salida["resultado"] = {
                "integridad_ledger": self.ledger.verificar_integridad(),
                "fecha": hoy,
                "bloques_del_dia": len(bloques_hoy),
                "raiz_merkle_del_dia": MerkleOpenTimestampsManager.calcular_raiz_merkle(bloques_hoy) if bloques_hoy else None,
            }
            return salida

        if sol.inputs is None or sol.targets is None:
            raise ErrorValidacionDatos(f"El plan {plan} requiere inputs y targets.")
        X, informe = self.guardian.sanitizar_matriz(sol.inputs)
        y = np.asarray(sol.targets, dtype=int)
        semilla = semilla_desde_contexto(meta)
        salida["sanitizacion"] = informe
        salida["hash_datos"] = sha256_hex(X.tobytes() + y.tobytes())

        if plan == "APRENDIZAJE_ONLINE_CONTINUO":
            salida["ruta_ejecutada"] = "streaming"
            salida["resultado"] = MotorStreamingContinuo().ingerir(X, y, sol.clases_declaradas, False, semilla)
            return salida

        perfil = self.analista.analizar(X, y)
        salida["perfil_metacognitivo"] = perfil
        if plan == "ANALISIS_METACOGNITIVO_AUTO":
            ruta, razon = self.analista.decidir_ruta(perfil)
        else:
            ruta, razon = self.RUTA_POR_PLAN[plan], f"Ruta impuesta por el plan semántico {plan}."
        if ruta == "cerebral_profundo" and not perfil["binario"]:
            raise ErrorValidacionDatos("La red profunda requiere targets binarios (0/1) con ambas clases.")
        if len(perfil["clases"]) < 2:
            raise ErrorValidacionDatos("Se requieren al menos 2 clases para entrenar.")
        salida["ruta_ejecutada"], salida["razonamiento"] = ruta, razon
        if ruta == "cerebral_profundo":
            salida["resultado"] = AgenteCerebralProfundo().entrenar(X, y, semilla)
        elif ruta == "ensamblado_explicable":
            nombres = meta.get("nombres_caracteristicas") if isinstance(meta.get("nombres_caracteristicas"), list) else None
            salida["resultado"] = AgenteEnsambladoExplicable().entrenar(X, y, semilla, nombres)
        else:
            salida["resultado"] = AgenteVectorialEvolutivo().entrenar(X, y, semilla)
        return salida
