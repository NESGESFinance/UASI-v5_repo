"""Optimización y observabilidad de la capa JEV: caché determinista de resultados y métricas.

- `CacheResultadosJEV`: LRU thread-safe con TTL. Como los agentes son deterministas
  (mismo tipo de tarea + mismos datos ⇒ mismo resultado), un resultado ya calculado puede
  reutilizarse sin volver a ejecutar el agente. Las tareas que dependen del estado vivo de
  UASI (`TAREAS_NO_CACHEABLES`) nunca se cachean.
- `MetricasJEV`: contadores thread-safe por lenguaje, por tipo de tarea y de rechazos.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any, Protocol

from uasi_v5.core.utilidades import json_canonico, sha256_hex
from uasi_v5.esquemas.jev import ResultadoAgenteJEV

# Dependen del ledger / health check en vivo: su resultado cambia aunque los datos no cambien.
TAREAS_NO_CACHEABLES = frozenset({"AUDITORIA_LEDGER", "EVALUACION_MODELO"})


def entero_env(nombre: str, defecto: int, minimo: int = 0) -> int:
    """Lee un entero del entorno en tiempo de ejecución (valor inválido ⇒ defecto)."""
    try:
        return max(minimo, int(os.getenv(nombre, str(defecto)).strip()))
    except ValueError:
        return defecto


def clave_cache(identidad_agente: str, tipo_tarea: str, datos: dict[str, Any]) -> str:
    """Clave = SHA-256 del JSON canónico de (agente, tarea, datos)."""
    return sha256_hex(json_canonico({"agente": identidad_agente, "tarea": tipo_tarea, "datos": datos}).encode())


class CacheJEV(Protocol):
    """Interfaz mínima de caché que usa el dispatcher (en memoria o Redis)."""

    @property
    def activa(self) -> bool: ...

    def obtener(self, clave: str) -> ResultadoAgenteJEV | None: ...

    def guardar(self, clave: str, resultado: ResultadoAgenteJEV) -> None: ...


class CacheResultadosJEV:
    """LRU con caducidad. `max_entradas=0` desactiva la caché."""

    def __init__(self, max_entradas: int = 512, ttl_segundos: float = 600.0) -> None:
        self.max_entradas = max(0, int(max_entradas))
        self.ttl_segundos = max(0.0, float(ttl_segundos))
        self._datos: OrderedDict[str, tuple[float, ResultadoAgenteJEV]] = OrderedDict()
        self._lock = threading.Lock()
        self.aciertos = 0
        self.fallos = 0

    @property
    def activa(self) -> bool:
        return self.max_entradas > 0 and self.ttl_segundos > 0

    def obtener(self, clave: str) -> ResultadoAgenteJEV | None:
        if not self.activa:
            return None
        with self._lock:
            entrada = self._datos.get(clave)
            if entrada is None or entrada[0] < time.monotonic():
                if entrada is not None:
                    del self._datos[clave]
                self.fallos += 1
                return None
            self._datos.move_to_end(clave)
            self.aciertos += 1
            return entrada[1].model_copy(deep=True, update={"desde_cache": True, "duracion_ms": 0.0})

    def guardar(self, clave: str, resultado: ResultadoAgenteJEV) -> None:
        if not self.activa or resultado.estado != "exito":
            return  # los errores no se cachean
        with self._lock:
            self._datos[clave] = (time.monotonic() + self.ttl_segundos, resultado.model_copy(deep=True))
            self._datos.move_to_end(clave)
            while len(self._datos) > self.max_entradas:
                self._datos.popitem(last=False)

    def limpiar(self) -> None:
        with self._lock:
            self._datos.clear()
            self.aciertos = self.fallos = 0

    def estado(self) -> dict[str, Any]:
        with self._lock:
            return {"activa": self.activa, "entradas": len(self._datos), "max_entradas": self.max_entradas,
                    "ttl_segundos": self.ttl_segundos, "aciertos": self.aciertos, "fallos": self.fallos,
                    "backend": "memoria"}


def _contador_lenguaje() -> dict[str, float]:
    return {"ejecuciones": 0, "exitos": 0, "errores": 0, "aciertos_cache": 0, "duracion_total_ms": 0.0}


class MetricasJEV:
    """Contadores en memoria del proceso (se reinician al reiniciar el servicio)."""

    TIPOS_RECHAZO = ("firma_invalida", "sin_configurar", "fuera_de_ventana", "replay_conflicto",
                     "validacion", "gobernanza")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reiniciar()

    def reiniciar(self) -> None:
        with self._lock:
            self._lenguajes: dict[str, dict[str, float]] = {}
            self._tareas: dict[str, dict[str, int]] = {}
            self._rechazos: dict[str, int] = dict.fromkeys(self.TIPOS_RECHAZO, 0)
            self._idempotentes = 0
            self._callbacks = {"enviados": 0, "fallidos": 0, "bloqueados": 0}
            self._desde = time.time()

    def registrar_despacho(self, tipo_tarea: str, estado: str, resultados: Iterable[ResultadoAgenteJEV]) -> None:
        with self._lock:
            tarea = self._tareas.setdefault(tipo_tarea, {"despachos": 0, "exito": 0, "error_parcial": 0, "error": 0})
            tarea["despachos"] += 1
            tarea[estado] = tarea.get(estado, 0) + 1
            for r in resultados:
                c = self._lenguajes.setdefault(r.lenguaje, _contador_lenguaje())
                c["ejecuciones"] += 1
                c["exitos" if r.estado == "exito" else "errores"] += 1
                c["aciertos_cache"] += 1 if r.desde_cache else 0
                c["duracion_total_ms"] += r.duracion_ms

    def registrar_rechazo(self, motivo: str) -> None:
        with self._lock:
            self._rechazos[motivo] = self._rechazos.get(motivo, 0) + 1

    def registrar_idempotente(self) -> None:
        with self._lock:
            self._idempotentes += 1

    def registrar_callback(self, resultado: str) -> None:
        with self._lock:
            self._callbacks[resultado] = self._callbacks.get(resultado, 0) + 1

    def instantanea(self) -> dict[str, Any]:
        with self._lock:
            lenguajes = {}
            for lang, c in self._lenguajes.items():
                ejecutadas = c["ejecuciones"] - c["aciertos_cache"]
                lenguajes[lang] = {
                    "ejecuciones": int(c["ejecuciones"]), "exitos": int(c["exitos"]), "errores": int(c["errores"]),
                    "aciertos_cache": int(c["aciertos_cache"]),
                    # media sobre ejecuciones reales (los aciertos de caché cuestan ~0 ms)
                    "duracion_media_ms": round(c["duracion_total_ms"] / ejecutadas, 3) if ejecutadas else 0.0,
                }
            return {"desde_epoch": self._desde, "por_lenguaje": lenguajes,
                    "por_tipo_tarea": {k: dict(v) for k, v in self._tareas.items()},
                    "rechazos": dict(self._rechazos), "respuestas_idempotentes": self._idempotentes,
                    "callbacks": dict(self._callbacks)}
