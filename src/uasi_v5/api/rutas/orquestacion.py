"""API v5: capa de orquestación conversacional de UASI.

Este endpoint es la puerta única de interacción agentica: recibe una intención en
lenguaje natural, deja que el GUÁRDIAN la clasifique (allow-list semántica), que el
ORQUESTADOR decida el plan con efecto operativo y, cuando el plan no requiere datos
de ML (diagnóstico / auditoría), lo ejecuta y responde en lenguaje legible. Toda
interacción queda anclada en el ledger inmutable.
"""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.esquemas.solicitudes import SolicitudMaestraV5, SolicitudOrquestacion
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.servicios import ledger, orquestador

router = APIRouter(tags=["orquestacion"])

# Planes que el orquestador puede ejecutar sin datos de ML (solo lectura operativa).
PLANES_SIN_DATOS = frozenset({"DIAGNOSTICO", "AUDITORIA"})

# Descripción legible de cada plan para la respuesta conversacional.
DESCRIPCION_PLAN: Dict[str, str] = {
    "DIAGNOSTICO": "diagnóstico de salud del sistema y los modelos",
    "AUDITORIA": "auditoría de integridad del ledger y raíz Merkle del día",
    "APRENDIZAJE_ONLINE_CONTINUO": "aprendizaje online continuo (streaming)",
    "EXPLICABILIDAD": "entrenamiento explicable con importancias de características",
    "OPTIMIZACION_EVOLUTIVA": "optimización evolutiva de hiperparámetros (SVM)",
    "APRENDIZAJE_PROFUNDO": "aprendizaje profundo (red neuronal)",
    "ANALISIS_METACOGNITIVO_AUTO": "análisis metacognitivo con selección automática de ruta",
}


def _mensaje_diagnostico(resultado: Dict[str, Any]) -> str:
    estado = resultado.get("estado", "operativo")
    componentes = resultado.get("componentes")
    partes = [f"He ejecutado un diagnóstico del sistema. Estado general: {estado}."]
    if isinstance(componentes, dict):
        modelos = componentes.get("modelos")
        if isinstance(modelos, dict):
            entrenados = sum(1 for m in modelos.values() if isinstance(m, dict) and m.get("estado") == "ok")
            partes.append(f"Modelos operativos: {entrenados}/{len(modelos)}.")
        ledger_comp = componentes.get("ledger")
        if isinstance(ledger_comp, dict) and "bloques" in ledger_comp:
            partes.append(f"Bloques en el ledger: {ledger_comp['bloques']}.")
    return " ".join(partes)


def _mensaje_auditoria(resultado: Dict[str, Any]) -> str:
    integridad = resultado.get("integridad_ledger")
    ok = bool(integridad.get("integro")) if isinstance(integridad, dict) else bool(integridad)
    bloques = resultado.get("bloques_del_dia", 0)
    raiz = resultado.get("raiz_merkle_del_dia")
    estado = "íntegro ✓" if ok else "con inconsistencias ⚠"
    partes = [
        f"He auditado el ledger inmutable. Integridad de la cadena: {estado}.",
        f"Bloques registrados hoy: {bloques}.",
    ]
    if raiz:
        partes.append(f"Raíz Merkle del día: {str(raiz)[:16]}…")
    return " ".join(partes)


def _mensaje_requiere_datos(plan: str) -> str:
    desc = DESCRIPCION_PLAN.get(plan, plan)
    return (
        f"He interpretado tu intención como un plan de {desc}. "
        "Este plan entrena o ejecuta modelos de aprendizaje automático, por lo que "
        "necesita un conjunto de datos (inputs y targets). Envíalos al endpoint "
        "maestro POST /api/v5/uasi-master-execute con la misma intención y la matriz "
        "de datos, y el orquestador ejecutará la ruta completa."
    )


@router.post("/api/v5/orquestar")
async def orquestar(
    sol: SolicitudOrquestacion, claims: Dict[str, Any] = Depends(require_auth)
) -> Dict[str, Any]:
    """Interpreta una intención en lenguaje natural, decide el plan y responde.

    - Clasifica la intención con la allow-list semántica del guárdian (403 si no
      corresponde a ninguna intención permitida o contiene patrones denegados).
    - Si el plan no requiere datos (diagnóstico / auditoría), lo ejecuta y devuelve
      un mensaje legible con el resultado operativo real.
    - Si el plan requiere datos de ML, devuelve estado ``requiere_datos`` con la guía
      para completarlo (nunca falla por falta de datos).
    - Registra cada interacción en el ledger inmutable.
    """
    categorias: List[str] = guardian.clasificar_prompt_master(sol.intencion)
    meta = guardian.validar_metadatos(sol.metadatos_contexto)
    plan = orquestador.planificar(categorias)

    resultado: Any = None
    ruta_ejecutada = None
    if plan in PLANES_SIN_DATOS:
        solm = SolicitudMaestraV5(prompt_intencion=sol.intencion, metadatos_contexto=meta)
        salida = await run_in_threadpool(orquestador.ejecutar, solm, categorias, meta)
        resultado = salida.get("resultado")
        ruta_ejecutada = salida.get("ruta_ejecutada")
        estado = "completado"
        if plan == "DIAGNOSTICO":
            mensaje = _mensaje_diagnostico(resultado if isinstance(resultado, dict) else {})
        else:
            mensaje = _mensaje_auditoria(resultado if isinstance(resultado, dict) else {})
    else:
        estado = "requiere_datos"
        mensaje = _mensaje_requiere_datos(plan)

    bloque = await run_in_threadpool(
        ledger.registrar,
        "ORQUESTACION_INTERACCION",
        {
            "id_sesion": sol.id_sesion,
            "intencion": sol.intencion,
            "plan": plan,
            "categorias_detectadas": categorias,
            "estado": estado,
            "ruta_ejecutada": ruta_ejecutada,
            "metadatos_contexto": meta,
        },
        claims["sub"],
    )
    return {
        "id_interaccion": str(uuid4()),
        "id_sesion": sol.id_sesion,
        "timestamp": ahora_utc().isoformat(),
        "intencion": sol.intencion,
        "plan": plan,
        "plan_descripcion": DESCRIPCION_PLAN.get(plan, plan),
        "categorias_detectadas": categorias,
        "ruta_ejecutada": ruta_ejecutada,
        "estado": estado,
        "mensaje": mensaje,
        "resultado": resultado,
        "bloque_ledger": bloque["indice"],
        "hash_bloque": bloque["hash_bloque"],
    }
