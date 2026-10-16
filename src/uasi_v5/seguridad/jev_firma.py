"""Firma HMAC-SHA256 del protocolo JEV ↔ UASI (fail-closed).

- La firma se calcula sobre `json_canonico(cuerpo)` (claves ordenadas, sin espacios, UTF-8)
  **excluyendo** los campos de firma (`firma_hmac` en la solicitud, `firma_respuesta` en la respuesta).
- `JEV_HMAC_KEY` se lee con `os.getenv` en cada llamada: permite rotar la clave sin reiniciar
  y que los tests la fijen con monkeypatch.
- Sin clave configurada → HTTP 503. Firma ausente o inválida → HTTP 401.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import HTTPException, status

from uasi_v5.core.utilidades import json_canonico

CAMPOS_FIRMA = ("firma_hmac", "firma_respuesta")


def jev_configurado() -> bool:
    """True si JEV_HMAC_KEY está definida (no vacía)."""
    return bool(os.getenv("JEV_HMAC_KEY", "").strip())


def _clave() -> bytes:
    clave = os.getenv("JEV_HMAC_KEY", "").strip()
    if not clave:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integración JEV no configurada (JEV_HMAC_KEY ausente).",
        )
    return clave.encode("utf-8")


def cuerpo_firmable(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Copia del cuerpo sin los campos de firma."""
    return {k: v for k, v in cuerpo.items() if k not in CAMPOS_FIRMA}


def firmar_jev(cuerpo: dict[str, Any]) -> str:
    """HMAC-SHA256 (hex, 64 caracteres) del JSON canónico del cuerpo sin los campos de firma."""
    mensaje = json_canonico(cuerpo_firmable(cuerpo)).encode("utf-8")
    return hmac.new(_clave(), mensaje, hashlib.sha256).hexdigest()


def verificar_firma_jev(cuerpo: dict[str, Any], firma: str | None = None) -> None:
    """Verifica la firma en tiempo constante. Lanza 503 sin clave y 401 si no coincide.

    Si `firma` es None se toma `cuerpo["firma_hmac"]`.
    """
    esperada = firmar_jev(cuerpo)  # primero: sin clave → 503 antes de mirar la firma
    recibida = cuerpo.get("firma_hmac") if firma is None else firma
    valida = False
    if isinstance(recibida, str):
        try:
            valida = hmac.compare_digest(esperada, recibida.strip().lower())
        except TypeError:  # caracteres no ASCII
            valida = False
    if not valida:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Firma JEV inválida.")
