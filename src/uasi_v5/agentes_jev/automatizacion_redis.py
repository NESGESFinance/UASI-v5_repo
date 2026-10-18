"""Backend Redis compartido para la capa JEV (caché, métricas e idempotencia).

Con varios workers de Gunicorn (o varias réplicas) las implementaciones en memoria de
`automatizacion.py` y `jev_replay.py` no se comparten: un reintento puede re-ejecutarse si cae
en otro worker y las métricas salen parciales. Estas clases mantienen exactamente la misma
interfaz pública, pero guardan el estado en Redis:

- `CacheJEVRedis`: `uasi:jev:cache:{clave}` con TTL (EX) y JSON; LRU con el sorted set
  `uasi:jev:cache:lru` (score = último acceso) y expulsión cuando se supera `max_entradas`.
- `MetricasJEVRedis`: incrementos atómicos (HINCRBY/HINCRBYFLOAT/INCR) bajo `uasi:jev:metricas:*`.
- `RegistroIdempotenciaJEVRedis`: `SET NX EX` sobre `uasi:jev:idem:{id_solicitud}`; la reserva es
  atómica entre workers (sin condición de carrera).

`crear_backend_jev(uri)` elige el backend: Redis si la URI es `redis://`/`rediss://` y responde
a PING; en cualquier otro caso (o si falla la conexión) usa las clases en memoria y lo registra.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, status

from uasi_v5.agentes_jev.automatizacion import CacheResultadosJEV, MetricasJEV
from uasi_v5.core.registro import logger
from uasi_v5.esquemas.jev import ResultadoAgenteJEV
from uasi_v5.seguridad.jev_replay import RegistroIdempotenciaJEV

PREFIJO_CACHE = "uasi:jev:cache"
PREFIJO_METRICAS = "uasi:jev:metricas"
PREFIJO_IDEM = "uasi:jev:idem"
ESQUEMAS_REDIS = ("redis://", "rediss://")


def _borrar_patron(cliente: Any, patron: str) -> None:
    """Borra todas las claves que coinciden con `patron` usando SCAN (no bloquea Redis como KEYS)."""
    lote: list[str] = []
    for clave in cliente.scan_iter(match=patron, count=500):
        lote.append(clave)
        if len(lote) >= 500:
            cliente.delete(*lote)
            lote = []
    if lote:
        cliente.delete(*lote)


class CacheJEVRedis:
    """Caché determinista compartida entre workers. Misma interfaz que `CacheResultadosJEV`."""

    def __init__(self, cliente: Any, max_entradas: int = 512, ttl_segundos: float = 600.0) -> None:
        self._r = cliente
        self.max_entradas = max(0, int(max_entradas))
        self.ttl_segundos = max(0.0, float(ttl_segundos))
        self._clave_lru = f"{PREFIJO_CACHE}:lru"
        self._clave_aciertos = f"{PREFIJO_CACHE}:aciertos"
        self._clave_fallos = f"{PREFIJO_CACHE}:fallos"

    @property
    def activa(self) -> bool:
        return self.max_entradas > 0 and self.ttl_segundos > 0

    @property
    def aciertos(self) -> int:
        return int(self._r.get(self._clave_aciertos) or 0)

    @property
    def fallos(self) -> int:
        return int(self._r.get(self._clave_fallos) or 0)

    def _clave(self, clave: str) -> str:
        return f"{PREFIJO_CACHE}:{clave}"

    def _purgar_lru(self) -> None:
        # Una entrada sin accesos desde hace más de un TTL ya ha caducado en Redis (el TTL se fija al
        # escribir y el score se actualiza en cada acceso, así que score ≥ instante de escritura).
        self._r.zremrangebyscore(self._clave_lru, "-inf", time.time() - self.ttl_segundos)

    def obtener(self, clave: str) -> ResultadoAgenteJEV | None:
        if not self.activa:
            return None
        bruto = self._r.get(self._clave(clave))
        if bruto is None:
            pipe = self._r.pipeline()
            pipe.incr(self._clave_fallos)
            pipe.zrem(self._clave_lru, clave)
            pipe.execute()
            return None
        pipe = self._r.pipeline()
        pipe.zadd(self._clave_lru, {clave: time.time()})
        pipe.incr(self._clave_aciertos)
        pipe.execute()
        resultado = ResultadoAgenteJEV.model_validate(json.loads(bruto))
        return resultado.model_copy(update={"desde_cache": True, "duracion_ms": 0.0})

    def guardar(self, clave: str, resultado: ResultadoAgenteJEV) -> None:
        if not self.activa or resultado.estado != "exito":
            return  # los errores no se cachean
        pipe = self._r.pipeline()
        pipe.set(self._clave(clave), json.dumps(resultado.model_dump(mode="json")),
                 px=max(1, int(self.ttl_segundos * 1000)))
        pipe.zadd(self._clave_lru, {clave: time.time()})
        pipe.execute()
        self._purgar_lru()
        sobrantes = int(self._r.zcard(self._clave_lru)) - self.max_entradas
        if sobrantes > 0:
            antiguas = self._r.zrange(self._clave_lru, 0, sobrantes - 1)
            if antiguas:
                pipe = self._r.pipeline()
                pipe.delete(*[self._clave(c) for c in antiguas])
                pipe.zrem(self._clave_lru, *antiguas)
                pipe.execute()

    def limpiar(self) -> None:
        _borrar_patron(self._r, f"{PREFIJO_CACHE}:*")

    def estado(self) -> dict[str, Any]:
        self._purgar_lru()
        return {"activa": self.activa, "entradas": int(self._r.zcard(self._clave_lru)),
                "max_entradas": self.max_entradas, "ttl_segundos": self.ttl_segundos,
                "aciertos": self.aciertos, "fallos": self.fallos, "backend": "redis"}


class MetricasJEVRedis:
    """Contadores compartidos entre workers. Misma interfaz que `MetricasJEV`."""

    TIPOS_RECHAZO = MetricasJEV.TIPOS_RECHAZO

    def __init__(self, cliente: Any) -> None:
        self._r = cliente
        self._desde = f"{PREFIJO_METRICAS}:desde"
        self._lenguajes = f"{PREFIJO_METRICAS}:lenguajes"
        self._tareas = f"{PREFIJO_METRICAS}:tareas"
        self._rechazos = f"{PREFIJO_METRICAS}:rechazos"
        self._idempotentes = f"{PREFIJO_METRICAS}:idempotentes"
        self._callbacks = f"{PREFIJO_METRICAS}:callbacks"
        # Solo el primer worker fija el instante inicial; el resto lo reutiliza.
        self._r.set(self._desde, repr(time.time()), nx=True)

    def _clave_lenguaje(self, lenguaje: str) -> str:
        return f"{PREFIJO_METRICAS}:lenguaje:{lenguaje}"

    def _clave_tarea(self, tarea: str) -> str:
        return f"{PREFIJO_METRICAS}:tarea:{tarea}"

    def reiniciar(self) -> None:
        _borrar_patron(self._r, f"{PREFIJO_METRICAS}:*")
        self._r.set(self._desde, repr(time.time()))

    def registrar_despacho(self, tipo_tarea: str, estado: str, resultados: Iterable[ResultadoAgenteJEV]) -> None:
        pipe = self._r.pipeline()
        pipe.sadd(self._tareas, tipo_tarea)
        pipe.hincrby(self._clave_tarea(tipo_tarea), "despachos", 1)
        pipe.hincrby(self._clave_tarea(tipo_tarea), estado, 1)
        for r in resultados:
            clave = self._clave_lenguaje(r.lenguaje)
            pipe.sadd(self._lenguajes, r.lenguaje)
            pipe.hincrby(clave, "ejecuciones", 1)
            pipe.hincrby(clave, "exitos" if r.estado == "exito" else "errores", 1)
            if r.desde_cache:
                pipe.hincrby(clave, "aciertos_cache", 1)
            pipe.hincrbyfloat(clave, "duracion_total_ms", float(r.duracion_ms))
        pipe.execute()

    def registrar_rechazo(self, motivo: str) -> None:
        self._r.hincrby(self._rechazos, motivo, 1)

    def registrar_idempotente(self) -> None:
        self._r.incr(self._idempotentes)

    def registrar_callback(self, resultado: str) -> None:
        self._r.hincrby(self._callbacks, resultado, 1)

    def instantanea(self) -> dict[str, Any]:
        lenguajes: dict[str, dict[str, Any]] = {}
        for lang in sorted(self._r.smembers(self._lenguajes)):
            c = {k: float(v) for k, v in self._r.hgetall(self._clave_lenguaje(lang)).items()}
            ejecuciones, aciertos = c.get("ejecuciones", 0.0), c.get("aciertos_cache", 0.0)
            ejecutadas = ejecuciones - aciertos
            lenguajes[lang] = {
                "ejecuciones": int(ejecuciones), "exitos": int(c.get("exitos", 0)),
                "errores": int(c.get("errores", 0)), "aciertos_cache": int(aciertos),
                # media sobre ejecuciones reales (los aciertos de caché cuestan ~0 ms)
                "duracion_media_ms": round(c.get("duracion_total_ms", 0.0) / ejecutadas, 3) if ejecutadas else 0.0,
            }
        tareas: dict[str, dict[str, int]] = {}
        for tarea in sorted(self._r.smembers(self._tareas)):
            base = {"despachos": 0, "exito": 0, "error_parcial": 0, "error": 0}
            base.update({k: int(v) for k, v in self._r.hgetall(self._clave_tarea(tarea)).items()})
            tareas[tarea] = base
        rechazos = dict.fromkeys(self.TIPOS_RECHAZO, 0)
        rechazos.update({k: int(v) for k, v in self._r.hgetall(self._rechazos).items()})
        callbacks = {"enviados": 0, "fallidos": 0, "bloqueados": 0}
        callbacks.update({k: int(v) for k, v in self._r.hgetall(self._callbacks).items()})
        return {"desde_epoch": float(self._r.get(self._desde) or time.time()), "por_lenguaje": lenguajes,
                "por_tipo_tarea": tareas, "rechazos": rechazos,
                "respuestas_idempotentes": int(self._r.get(self._idempotentes) or 0), "callbacks": callbacks}


class RegistroIdempotenciaJEVRedis:
    """Registro anti-replay compartido entre workers. Misma interfaz que `RegistroIdempotenciaJEV`."""

    ttl = staticmethod(RegistroIdempotenciaJEV.ttl)

    def __init__(self, cliente: Any) -> None:
        self._r = cliente

    def _clave(self, id_solicitud: str) -> str:
        return f"{PREFIJO_IDEM}:{id_solicitud}"

    def reservar(self, id_solicitud: str, huella: str) -> dict[str, Any] | None:
        """Reserva el id de forma atómica (SET NX EX). Devuelve la respuesta si es un reintento ya resuelto.

        Lanza 409 si el id ya se usó con otro cuerpo o si un reintento idéntico sigue en curso.
        """
        clave = self._clave(id_solicitud)
        pendiente = json.dumps({"huella": huella, "respuesta": None})
        for _ in range(3):  # la entrada previa puede caducar entre el SET NX y el GET
            if self._r.set(clave, pendiente, nx=True, ex=self.ttl()):
                return None
            bruto = self._r.get(clave)
            if bruto is None:
                continue
            previo = json.loads(bruto)
            if previo.get("huella") != huella:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="id_solicitud ya utilizado con un cuerpo distinto (posible replay).")
            respuesta: dict[str, Any] | None = previo.get("respuesta")
            if respuesta is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Solicitud idéntica todavía en curso; reintente más tarde.")
            return respuesta
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="No se pudo reservar id_solicitud por concurrencia; reintente más tarde.")

    def completar(self, id_solicitud: str, huella: str, respuesta: dict[str, Any]) -> None:
        self._r.set(self._clave(id_solicitud), json.dumps({"huella": huella, "respuesta": respuesta}), ex=self.ttl())

    def liberar(self, id_solicitud: str) -> None:
        """Libera una reserva pendiente si el procesamiento falló (permite reintentar).

        WATCH/MULTI garantiza que no se borra una respuesta completada por otro worker entretanto.
        """
        clave = self._clave(id_solicitud)
        with self._r.pipeline() as pipe:
            try:
                pipe.watch(clave)
                bruto = pipe.get(clave)
                if bruto is None or json.loads(bruto).get("respuesta") is not None:
                    pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(clave)
                pipe.execute()
            except Exception as exc:  # noqa: BLE001 - WatchError u otro fallo: la reserva caducará sola por TTL
                logger.warning("No se pudo liberar la reserva JEV %s en Redis: %s", id_solicitud, exc)

    def limpiar(self) -> None:
        _borrar_patron(self._r, f"{PREFIJO_IDEM}:*")


BackendJEV = tuple[
    CacheJEVRedis | CacheResultadosJEV,
    MetricasJEVRedis | MetricasJEV,
    RegistroIdempotenciaJEVRedis | RegistroIdempotenciaJEV,
]


def _backend_memoria(max_entradas: int, ttl_segundos: float) -> BackendJEV:
    return (CacheResultadosJEV(max_entradas=max_entradas, ttl_segundos=ttl_segundos), MetricasJEV(),
            RegistroIdempotenciaJEV())


def crear_backend_jev(uri: str | None, max_entradas: int | None = None,
                      ttl_segundos: float | None = None) -> BackendJEV:
    """Devuelve (caché, métricas, idempotencia): Redis si `uri` es redis:// y responde; si no, en memoria."""
    if max_entradas is None or ttl_segundos is None:
        from uasi_v5.core.config import JEV_CACHE_MAX, JEV_CACHE_TTL

        max_entradas = JEV_CACHE_MAX if max_entradas is None else max_entradas
        ttl_segundos = JEV_CACHE_TTL if ttl_segundos is None else ttl_segundos

    if not uri or not uri.startswith(ESQUEMAS_REDIS):
        logger.warning("Backend JEV en memoria (RATE_LIMIT_STORAGE_URI no es redis://): con varios workers "
                       "la caché, la idempotencia y las métricas JEV no se comparten.")
        return _backend_memoria(max_entradas, ttl_segundos)

    try:
        import redis

        cliente = redis.Redis.from_url(uri, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        cliente.ping()
    except Exception as exc:  # noqa: BLE001 - ImportError, ConnectionError, URI inválida…: degradar a memoria
        logger.warning("Redis no disponible para JEV (%s); se usa el backend en memoria. Con varios workers "
                       "la caché, la idempotencia y las métricas JEV no se comparten.", type(exc).__name__)
        return _backend_memoria(max_entradas, ttl_segundos)

    logger.info("Backend JEV en Redis: caché, idempotencia y métricas compartidas entre workers.")
    return (CacheJEVRedis(cliente, max_entradas=max_entradas, ttl_segundos=ttl_segundos),
            MetricasJEVRedis(cliente), RegistroIdempotenciaJEVRedis(cliente))
