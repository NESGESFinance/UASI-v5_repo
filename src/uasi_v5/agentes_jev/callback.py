"""Entrega automática de la RespuestaJEV firmada a JEV mediante callback HTTP (opcional).

Seguridad (anti-SSRF, deny-by-default):
- Solo se envía si `metadatos.url_callback` apunta a un host incluido en `JEV_CALLBACK_HOSTS`
  (lista separada por comas, comparación exacta sin distinguir mayúsculas). Lista vacía ⇒ callbacks
  deshabilitados.
- Esquema `https` obligatorio; `http` solo para `localhost` / `127.0.0.1` (desarrollo).
- Sin credenciales embebidas en la URL; no se siguen redirecciones.
- Cabecera `X-JEV-Firma` con la firma de la respuesta (igual a `firma_respuesta` del cuerpo).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from uasi_v5.agentes_jev.automatizacion import entero_env

logger = logging.getLogger("uasi_v5.jev.callback")

HOSTS_LOCALES = frozenset({"localhost", "127.0.0.1"})

Transporte = Callable[[str, dict[str, Any], dict[str, str], float], int]


def hosts_permitidos() -> set[str]:
    return {h.strip().lower() for h in os.getenv("JEV_CALLBACK_HOSTS", "").split(",") if h.strip()}


def validar_url_callback(url: Any) -> tuple[bool, str]:
    """(permitida, motivo). Nunca lanza."""
    if not isinstance(url, str) or not url.strip():
        return False, "url_callback vacía o no textual"
    try:
        partes = urlsplit(url.strip())
        host = (partes.hostname or "").lower()
        _ = partes.port  # puerto inválido ⇒ ValueError
    except ValueError:
        return False, "url_callback mal formada"
    if partes.username or partes.password:
        return False, "url_callback no puede incluir credenciales"
    if partes.scheme not in ("https", "http") or (partes.scheme == "http" and host not in HOSTS_LOCALES):
        return False, "solo se permite https (http únicamente para localhost)"
    permitidos = hosts_permitidos()
    if not permitidos:
        return False, "callbacks deshabilitados (JEV_CALLBACK_HOSTS vacío)"
    if host not in permitidos:
        return False, f"host {host!r} no está en JEV_CALLBACK_HOSTS"
    return True, "ok"


def transporte_httpx(url: str, contenido: dict[str, Any], cabeceras: dict[str, str], timeout: float) -> int:
    """POST JSON con httpx (dependencia ya presente). Devuelve el código HTTP."""
    import httpx

    r = httpx.post(url, json=contenido, headers=cabeceras, timeout=timeout, follow_redirects=False)
    return r.status_code


def enviar_callback(
    url: str,
    contenido: dict[str, Any],
    registrar: Callable[[dict[str, Any]], Any] | None = None,
    transporte: Transporte | None = None,
    esperar: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Envía con reintentos y backoff exponencial. Devuelve un informe (nunca lanza)."""
    intentos_max = entero_env("JEV_CALLBACK_REINTENTOS", 3, minimo=1)
    timeout = float(entero_env("JEV_CALLBACK_TIMEOUT", 5, minimo=1))
    enviar = transporte or transporte_httpx
    cabeceras = {"Content-Type": "application/json", "X-JEV-Firma": str(contenido.get("firma_respuesta", "")),
                 "X-UASI-Id-Ejecucion": str(contenido.get("id_ejecucion", ""))}
    informe: dict[str, Any] = {"host": (urlsplit(url).hostname or ""), "entregado": False, "intentos": 0,
                               "codigo_http": None, "error": None, "id_ejecucion": contenido.get("id_ejecucion")}
    for intento in range(1, intentos_max + 1):
        informe["intentos"] = intento
        try:
            codigo = enviar(url, contenido, cabeceras, timeout)
            informe["codigo_http"] = codigo
            if 200 <= codigo < 300:
                informe["entregado"], informe["error"] = True, None
                break
            informe["error"] = f"HTTP {codigo}"
            if 400 <= codigo < 500 and codigo not in (408, 429):
                break  # error del receptor no transitorio: no reintentar
        except Exception as exc:  # red, timeout, DNS...
            informe["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if intento < intentos_max:
            esperar(0.5 * (2 ** (intento - 1)))
    if not informe["entregado"]:
        logger.warning("Callback JEV no entregado a %s: %s", informe["host"], informe["error"])
    if registrar is not None:
        try:
            registrar(informe)
        except Exception:  # el registro nunca debe romper la tarea en segundo plano
            logger.exception("No se pudo registrar el callback JEV en el ledger")
    return informe
