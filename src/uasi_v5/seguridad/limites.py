"""Rate limiting [P1-09] y límite de tamaño de payload [P1-10]."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from slowapi import Limiter
from slowapi.util import get_remote_address

from uasi_v5.core.config import MAX_PAYLOAD_BYTES, RATE_LIMIT_PER_MINUTE, RATE_LIMIT_STORAGE_URI

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{RATE_LIMIT_PER_MINUTE}/minute"],
    storage_uri=RATE_LIMIT_STORAGE_URI,  # usar redis:// en multi-worker para límite global
)


class LimiteTamanoPayloadMiddleware:
    """
    Middleware ASGI puro: lee el cuerpo hasta MAX_PAYLOAD_BYTES y lo re-inyecta.
    Rechaza con 413 tanto por Content-Length declarado como por bytes reales
    recibidos (protege también frente a chunked transfer sin Content-Length).
    """

    def __init__(self, app: Any, max_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _rechazar(self, send: Callable) -> None:
        cuerpo = json.dumps({"detail": f"Payload excede el límite de {self.max_bytes} bytes."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(cuerpo)).encode())]})
        await send({"type": "http.response.body", "body": cuerpo})

    async def __call__(self, scope: Dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        cabeceras = dict(scope.get("headers") or [])
        declarado = cabeceras.get(b"content-length")
        if declarado is not None:
            try:
                if int(declarado) > self.max_bytes:
                    await self._rechazar(send)
                    return
            except ValueError:
                await self._rechazar(send)
                return
        trozos: List[bytes] = []
        total = 0
        while True:
            mensaje = await receive()
            if mensaje["type"] == "http.disconnect":
                return
            cuerpo = mensaje.get("body", b"")
            total += len(cuerpo)
            if total > self.max_bytes:
                await self._rechazar(send)
                return
            trozos.append(cuerpo)
            if not mensaje.get("more_body", False):
                break
        cuerpo_completo = b"".join(trozos)
        entregado = False

        async def receive_reinyectado() -> Dict:
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": cuerpo_completo, "more_body": False}
            return await receive()

        await self.app(scope, receive_reinyectado, send)
