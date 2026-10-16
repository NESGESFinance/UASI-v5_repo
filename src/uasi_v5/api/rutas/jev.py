"""Capa JEV: JEV → UASI (dispatcher multiagente por lenguaje) → JEV, con firma HMAC-SHA256.

Endpoints:
- POST /api/v5/jev-dispatch        → una solicitud firmada → una respuesta firmada.
- POST /api/v5/jev-dispatch/lote   → varias solicitudes (cada una con su firma) → respuestas firmadas.
- GET  /api/v5/jev/agentes         → lenguajes, tipos de tarea y configuración.
- GET  /api/v5/jev/metricas        → contadores por lenguaje / tarea / rechazos / caché / callbacks.

Pipeline por solicitud: firma (503/401) → ventana temporal (401) → gobernanza de metadatos (403)
→ idempotencia por id_solicitud (respuesta cacheada o 409) → dispatcher (caché determinista)
→ ledger DISPATCH_JEV → respuesta firmada → callback opcional en segundo plano.
"""

from __future__ import annotations

import time
import unicodedata
from typing import Any, get_args

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from uasi_v5.agentes_jev.automatizacion import TAREAS_NO_CACHEABLES, entero_env
from uasi_v5.agentes_jev.callback import enviar_callback, hosts_permitidos, validar_url_callback
from uasi_v5.core.config import VERSION_UASI
from uasi_v5.core.excepciones import ErrorGobernanza
from uasi_v5.core.utilidades import json_canonico, sha256_hex
from uasi_v5.esquemas.jev import LenguajeDestino, LoteJEV, RespuestaJEV, SolicitudJEV, TipoTareaJEV
from uasi_v5.seguridad.autenticacion import require_auth
from uasi_v5.seguridad.gobernanza import guardian
from uasi_v5.seguridad.jev_firma import firmar_jev, jev_configurado, verificar_firma_jev
from uasi_v5.seguridad.jev_replay import hash_cuerpo, ventana_segundos, verificar_ventana
from uasi_v5.servicios import cache_jev, dispatcher_jev, idempotencia_jev, ledger, metricas_jev

router = APIRouter(tags=["jev"])

def lote_max() -> int:
    return entero_env("JEV_LOTE_MAX", 20, minimo=1)


def _verificar_seguridad(cuerpo: dict[str, Any], sol: SolicitudJEV) -> dict[str, Any]:
    """Firma + ventana + gobernanza. Registra el motivo del rechazo en las métricas."""
    try:
        verificar_firma_jev(cuerpo)
    except HTTPException as exc:
        metricas_jev.registrar_rechazo("sin_configurar" if exc.status_code == 503 else "firma_invalida")
        raise
    try:
        verificar_ventana(cuerpo, sol.timestamp)
    except HTTPException:
        metricas_jev.registrar_rechazo("fuera_de_ventana")
        raise
    try:
        return guardian.validar_metadatos(sol.metadatos)
    except ErrorGobernanza:
        metricas_jev.registrar_rechazo("gobernanza")
        raise


async def _procesar(cuerpo: dict[str, Any], sol: SolicitudJEV, actor: str) -> tuple[dict[str, Any], bool]:
    """Procesa una solicitud ya validada por Pydantic. Devuelve (respuesta_firmada, idempotente)."""
    inicio = time.perf_counter()
    meta = _verificar_seguridad(cuerpo, sol)

    # Idempotencia solo si JEV envió su propio id_solicitud (el generado por UASI es siempre nuevo).
    id_idem = sol.id_solicitud if "id_solicitud" in cuerpo else None
    huella = hash_cuerpo({k: v for k, v in cuerpo.items() if k != "firma_hmac"})
    if id_idem is not None:
        try:
            previa = idempotencia_jev.reservar(id_idem, huella)
        except HTTPException:
            metricas_jev.registrar_rechazo("replay_conflicto")
            raise
        if previa is not None:
            metricas_jev.registrar_idempotente()
            return previa, True

    try:
        despacho = await run_in_threadpool(
            dispatcher_jev.despachar, sol.tipo_tarea, sol.lenguajes_destino, sol.datos, meta
        )
        resultados = despacho["resultados_por_lenguaje"]
        bloque = await run_in_threadpool(ledger.registrar, "DISPATCH_JEV", {
            "id_jev": sol.id_jev, "id_solicitud": sol.id_solicitud, "version_jev": sol.version_jev,
            "tipo_tarea": sol.tipo_tarea, "lenguajes": list(resultados), "estado": despacho["estado"],
            "hash_datos": sha256_hex(json_canonico(sol.datos).encode()),
            "hashes_resultado": {lang: r.hash_resultado for lang, r in resultados.items()},
            "desde_cache": sorted(lang for lang, r in resultados.items() if r.desde_cache),
            "metadatos": meta}, actor)
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
    except BaseException:
        if id_idem is not None:
            idempotencia_jev.liberar(id_idem)
        raise
    metricas_jev.registrar_despacho(sol.tipo_tarea, despacho["estado"], resultados.values())
    if id_idem is not None:
        idempotencia_jev.completar(id_idem, huella, contenido)
    return contenido, False


def _registrar_callback_ledger(actor: str) -> Any:
    def registrar(informe: dict[str, Any]) -> None:
        metricas_jev.registrar_callback("enviados" if informe["entregado"] else "fallidos")
        ledger.registrar("CALLBACK_JEV", informe, actor)
    return registrar


def _programar_callback(meta: dict[str, Any], contenido: dict[str, Any], tareas: BackgroundTasks,
                        actor: str) -> str | None:
    """Programa el callback si `metadatos.url_callback` existe. Devuelve el estado para la cabecera."""
    if "url_callback" not in meta:
        return None
    permitida, motivo = validar_url_callback(meta["url_callback"])
    if not permitida:
        metricas_jev.registrar_callback("bloqueados")
        # Las cabeceras HTTP deben ser latin-1/ASCII: se eliminan tildes del motivo.
        ascii_motivo = unicodedata.normalize("NFKD", motivo).encode("ascii", "ignore").decode()
        return f"rechazado: {ascii_motivo}"
    tareas.add_task(enviar_callback, str(meta["url_callback"]).strip(), contenido,
                    registrar=_registrar_callback_ledger(actor))
    return "programado"


@router.post("/api/v5/jev-dispatch", response_model=RespuestaJEV)
async def jev_dispatch(
    sol: SolicitudJEV, request: Request, tareas: BackgroundTasks, claims: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    # La firma se verifica sobre el JSON EXACTO emitido por JEV (no sobre el modelo con
    # valores por defecto rellenados por UASI, como id_solicitud o timestamp).
    cuerpo = await request.json()
    if not isinstance(cuerpo, dict):
        raise HTTPException(status_code=422, detail="El cuerpo debe ser un objeto JSON.")
    contenido, idempotente = await _procesar(cuerpo, sol, claims["sub"])
    cabeceras = {"X-JEV-Idempotente": "true" if idempotente else "false"}
    if not idempotente:  # un reintento idéntico no vuelve a disparar el callback
        estado_cb = _programar_callback(sol.metadatos, contenido, tareas, claims["sub"])
        if estado_cb:
            cabeceras["X-JEV-Callback"] = estado_cb
    return JSONResponse(content=contenido, headers=cabeceras)


@router.post("/api/v5/jev-dispatch/lote")
async def jev_dispatch_lote(
    lote: LoteJEV, tareas: BackgroundTasks, claims: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    """Procesa hasta JEV_LOTE_MAX solicitudes; cada una se verifica, ejecuta y registra por separado.

    Un elemento rechazado no detiene el resto: su entrada lleva `codigo_http` y `error`.
    """
    if len(lote.solicitudes) > lote_max():
        raise HTTPException(status_code=422, detail=f"El lote excede JEV_LOTE_MAX={lote_max()} solicitudes.")
    if not jev_configurado():  # fail-closed global antes de procesar nada
        metricas_jev.registrar_rechazo("sin_configurar")
        raise HTTPException(status_code=503, detail="Integración JEV no configurada (JEV_HMAC_KEY ausente).")
    inicio = time.perf_counter()
    entradas: list[dict[str, Any]] = []
    for indice, cuerpo in enumerate(lote.solicitudes):
        entrada: dict[str, Any] = {"indice": indice, "id_jev": cuerpo.get("id_jev")}
        try:
            sol = SolicitudJEV.model_validate(cuerpo)
            contenido, idempotente = await _procesar(cuerpo, sol, claims["sub"])
            entrada.update(codigo_http=200, idempotente=idempotente, respuesta=contenido)
            if not idempotente:
                estado_cb = _programar_callback(sol.metadatos, contenido, tareas, claims["sub"])
                if estado_cb:
                    entrada["callback"] = estado_cb
        except ValidationError as exc:
            metricas_jev.registrar_rechazo("validacion")
            entrada.update(codigo_http=422, error=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()])
        except ErrorGobernanza as exc:
            entrada.update(codigo_http=403, error=str(exc))
        except HTTPException as exc:
            entrada.update(codigo_http=exc.status_code, error=exc.detail)
        entradas.append(entrada)
    exitosas = sum(1 for e in entradas if e["codigo_http"] == 200)
    resumen: dict[str, Any] = {
        "total": len(entradas), "exitosas": exitosas, "fallidas": len(entradas) - exitosas,
        "duracion_total_ms": round((time.perf_counter() - inicio) * 1000, 3),
        "version_uasi": VERSION_UASI, "resultados": entradas,
    }
    resumen["firma_respuesta"] = firmar_jev(resumen)
    return JSONResponse(content=resumen)


@router.get("/api/v5/jev/agentes")
async def jev_agentes(claims: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    return {
        "lenguajes": dispatcher_jev.lenguajes,
        "lenguajes_protocolo": list(get_args(LenguajeDestino)),
        "tipos_tarea": list(get_args(TipoTareaJEV)),
        "tareas_no_cacheables": sorted(TAREAS_NO_CACHEABLES),
        "max_workers": dispatcher_jev.max_workers,
        "firma": "HMAC-SHA256 hex sobre JSON canónico sin firma_hmac/firma_respuesta",
        "jev_configurado": jev_configurado(),
        "ventana_segundos": ventana_segundos(),
        "lote_max": lote_max(),
        "callbacks_habilitados": bool(hosts_permitidos()),
    }


@router.get("/api/v5/jev/metricas")
async def jev_metricas(claims: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    return {**metricas_jev.instantanea(), "cache": cache_jev.estado()}
