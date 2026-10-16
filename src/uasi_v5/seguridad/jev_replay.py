"""Protección anti-replay e idempotencia del protocolo JEV.

Reglas (solo se aplican a los campos que JEV envía explícitamente en el cuerpo firmado):
- `timestamp`: debe estar dentro de ±`JEV_VENTANA_SEGUNDOS` (por defecto 300) respecto al
  reloj de UASI; fuera de la ventana ⇒ HTTP 401. Un timestamp sin zona horaria se trata como UTC.
- `id_solicitud`: se recuerda durante `JEV_IDEMPOTENCIA_TTL` segundos (por defecto 900, nunca
  menos que la ventana) junto con el hash del cuerpo firmado.
    * mismo id + mismo cuerpo ⇒ se devuelve la respuesta ya firmada (idempotencia, sin re-ejecutar);
    * mismo id + cuerpo distinto ⇒ HTTP 409 (posible replay/manipulación).
Las variables se leen en tiempo de ejecución. Estado en memoria del proceso: con varios
workers/réplicas cada uno lleva su propio registro (documentado en docs/arquitectura.md).
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from uasi_v5.agentes_jev.automatizacion import entero_env
from uasi_v5.core.utilidades import ahora_utc, json_canonico, sha256_hex


def ventana_segundos() -> int:
    return entero_env("JEV_VENTANA_SEGUNDOS", 300, minimo=1)


def verificar_ventana(cuerpo: dict[str, Any], ts: datetime | None) -> None:
    """Lanza 401 si JEV envió `timestamp` y está fuera de la ventana de tolerancia."""
    if "timestamp" not in cuerpo or ts is None:
        return
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    desfase = abs((ahora_utc() - ts).total_seconds())
    if desfase > ventana_segundos():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Solicitud JEV fuera de la ventana temporal ({int(desfase)} s > {ventana_segundos()} s).",
        )


def hash_cuerpo(cuerpo: dict[str, Any]) -> str:
    return sha256_hex(json_canonico(cuerpo).encode())


class RegistroIdempotenciaJEV:
    """id_solicitud → (caducidad, hash del cuerpo, respuesta firmada o None si está en curso)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._datos: dict[str, tuple[float, str, dict[str, Any] | None]] = {}

    @staticmethod
    def ttl() -> int:
        return max(entero_env("JEV_IDEMPOTENCIA_TTL", 900, minimo=1), ventana_segundos())

    def _purgar(self, ahora: float) -> None:
        for k in [k for k, (exp, _, _) in self._datos.items() if exp < ahora]:
            del self._datos[k]

    def reservar(self, id_solicitud: str, huella: str) -> dict[str, Any] | None:
        """Reserva el id. Devuelve la respuesta cacheada si es un reintento idéntico ya resuelto.

        Lanza 409 si el id ya se usó con otro cuerpo o si un reintento idéntico sigue en curso.
        """
        ahora = time.monotonic()
        with self._lock:
            self._purgar(ahora)
            previo = self._datos.get(id_solicitud)
            if previo is None:
                self._datos[id_solicitud] = (ahora + self.ttl(), huella, None)
                return None
            _, huella_previa, respuesta = previo
            if huella_previa != huella:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="id_solicitud ya utilizado con un cuerpo distinto (posible replay).")
            if respuesta is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail="Solicitud idéntica todavía en curso; reintente más tarde.")
            return respuesta

    def completar(self, id_solicitud: str, huella: str, respuesta: dict[str, Any]) -> None:
        with self._lock:
            self._datos[id_solicitud] = (time.monotonic() + self.ttl(), huella, respuesta)

    def liberar(self, id_solicitud: str) -> None:
        """Libera una reserva si el procesamiento falló (permite reintentar)."""
        with self._lock:
            previo = self._datos.get(id_solicitud)
            if previo is not None and previo[2] is None:
                del self._datos[id_solicitud]

    def limpiar(self) -> None:
        with self._lock:
            self._datos.clear()
