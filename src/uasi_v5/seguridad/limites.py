"""
Rate limiting [P1-09] y límite de tamaño de payload [P1-10].

- `limiter` (slowapi): límite global por IP (RATE_LIMIT_PER_MINUTE).
- `LimitadorDistribuido` + `LimiteTasaDistribuidoMiddleware` (v5.3.0): límites por grupo de
  rutas compartidos entre workers/réplicas mediante Redis (ventana fija por minuto con
  INCR + EXPIRE atómicos en pipeline). Si Redis no está configurado o deja de responder,
  degrada a contadores en memoria por worker (mismo fallback que el backend JEV de PR#4).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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


PREFIJO_RATE_LIMIT = "uasi:rl"


class LimitadorDistribuido:
    """Contador de ventana fija por (clave, minuto). Redis si está disponible; si no, memoria."""

    def __init__(self, uri: Optional[str] = None, cliente: Any = None) -> None:
        if cliente is None and uri:
            from uasi_v5.agentes_jev.automatizacion_redis import conectar_redis

            cliente = conectar_redis(uri)
        self._r = cliente
        self._memoria: Dict[str, Tuple[int, float]] = {}
        self._lock = threading.Lock()

    @property
    def backend(self) -> str:
        return "redis" if self._r is not None else "memoria"

    def _consumir_memoria(self, clave: str, ventana: int, ahora: float) -> int:
        with self._lock:
            if len(self._memoria) > 10_000:  # purga de ventanas caducadas
                self._memoria = {k: v for k, v in self._memoria.items() if v[1] > ahora}
            cuenta, expira = self._memoria.get(clave, (0, ahora + ventana))
            if expira <= ahora:
                cuenta, expira = 0, ahora + ventana
            cuenta += 1
            self._memoria[clave] = (cuenta, expira)
            return cuenta

    def consumir(self, identificador: str, limite: int, ventana: int = 60) -> Tuple[bool, int, int]:
        """Registra una petición. Devuelve (permitida, restantes, segundos_hasta_reinicio)."""
        ahora = time.time()
        inicio_ventana = int(ahora // ventana) * ventana
        reinicio = max(1, int(inicio_ventana + ventana - ahora))
        clave = f"{PREFIJO_RATE_LIMIT}:{identificador}:{inicio_ventana}"
        cuenta: int
        if self._r is not None:
            try:
                pipe = self._r.pipeline()
                pipe.incr(clave)
                pipe.expire(clave, ventana + 1)
                cuenta = int(pipe.execute()[0])
            except Exception as exc:  # noqa: BLE001 - Redis caído en caliente: degradar a memoria
                from uasi_v5.core.registro import logger

                logger.warning("Rate limiting: Redis no responde (%s); se usa memoria por worker.", type(exc).__name__)
                self._r = None
                cuenta = self._consumir_memoria(clave, ventana, ahora)
        else:
            cuenta = self._consumir_memoria(clave, ventana, ahora)
        return cuenta <= limite, max(0, limite - cuenta), reinicio


class LimiteTasaDistribuidoMiddleware:
    """
    Middleware ASGI: aplica el límite del primer prefijo de ruta que coincida, por IP.
    `reglas` = [(prefijo, limite_por_minuto, nombre_grupo), ...]. Responde 429 con
    Retry-After y cabeceras X-RateLimit-*. Las rutas sin regla pasan sin coste.
    """

    def __init__(self, app: Any, limitador: LimitadorDistribuido, reglas: Sequence[Tuple[str, int, str]]) -> None:
        self.app = app
        self.limitador = limitador
        self.reglas = list(reglas)

    async def __call__(self, scope: Dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ruta = scope.get("path", "")
        regla = next((r for r in self.reglas if ruta.startswith(r[0])), None)
        if regla is None:
            await self.app(scope, receive, send)
            return
        _, limite, grupo = regla
        ip = (scope.get("client") or ("desconocida", 0))[0]
        permitida, restantes, reinicio = self.limitador.consumir(f"{grupo}:{ip}", limite)
        cabeceras = [(b"x-ratelimit-limit", str(limite).encode()),
                     (b"x-ratelimit-remaining", str(restantes).encode()),
                     (b"x-ratelimit-reset", str(reinicio).encode())]
        if not permitida:
            cuerpo = json.dumps({"detail": f"Límite de {limite} peticiones/minuto excedido ({grupo})."}).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(cuerpo)).encode()),
                                    (b"retry-after", str(reinicio).encode()), *cabeceras]})
            await send({"type": "http.response.body", "body": cuerpo})
            return

        async def send_con_cabeceras(mensaje: Dict) -> None:
            if mensaje["type"] == "http.response.start":
                mensaje = {**mensaje, "headers": [*mensaje.get("headers", []), *cabeceras]}
            await send(mensaje)

        await self.app(scope, receive, send_con_cabeceras)


class MetricasHTTP:
    """Métricas HTTP en proceso (por worker): peticiones, errores y latencias recientes."""

    def __init__(self, ventana: int = 2000) -> None:
        from collections import deque

        self.desde = time.time()
        self.peticiones = 0
        self.errores_4xx = 0
        self.errores_5xx = 0
        self._latencias: Any = deque(maxlen=ventana)
        self._instantes: Any = deque(maxlen=ventana)
        self._lock = threading.Lock()

    def registrar(self, estado: int, duracion_ms: float) -> None:
        with self._lock:
            self.peticiones += 1
            if 400 <= estado < 500:
                self.errores_4xx += 1
            elif estado >= 500:
                self.errores_5xx += 1
            self._latencias.append(duracion_ms)
            self._instantes.append(time.time())

    def instantanea(self) -> Dict[str, Any]:
        with self._lock:
            lat = sorted(self._latencias)
            ahora = time.time()
            ultimo_minuto = sum(1 for t in self._instantes if t >= ahora - 60)

            def percentil(p: float) -> float:
                return round(lat[min(len(lat) - 1, int(p * len(lat)))], 3) if lat else 0.0

            return {"ambito": "worker", "uptime_segundos": round(ahora - self.desde, 1),
                    "peticiones_totales": self.peticiones, "errores_4xx": self.errores_4xx,
                    "errores_5xx": self.errores_5xx,
                    "tasa_error_5xx": round(self.errores_5xx / self.peticiones, 6) if self.peticiones else 0.0,
                    "throughput_rpm_ultimo_minuto": ultimo_minuto,
                    "latencia_ms": {"media": round(sum(lat) / len(lat), 3) if lat else 0.0,
                                    "p50": percentil(0.50), "p95": percentil(0.95), "p99": percentil(0.99),
                                    "muestras": len(lat)}}


class MetricasHTTPMiddleware:
    """Middleware ASGI que alimenta `MetricasHTTP` (código de estado y latencia por petición)."""

    def __init__(self, app: Any, metricas: MetricasHTTP) -> None:
        self.app = app
        self.metricas = metricas

    async def __call__(self, scope: Dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()
        estado = {"codigo": 500}

        async def send_capturando(mensaje: Dict) -> None:
            if mensaje["type"] == "http.response.start":
                estado["codigo"] = int(mensaje["status"])
            await send(mensaje)

        try:
            await self.app(scope, receive, send_capturando)
        finally:
            self.metricas.registrar(estado["codigo"], (time.perf_counter() - inicio) * 1000)
