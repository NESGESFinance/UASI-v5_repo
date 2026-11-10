"""Hash y verificación de contraseñas (bcrypt con pre-hash SHA-256)."""

from __future__ import annotations

import base64
import hashlib

import bcrypt

# bcrypt trunca silenciosamente las contraseñas a 72 bytes. Para admitir contraseñas
# de cualquier longitud sin truncamiento, se pre-hashea con SHA-256 y se codifica en
# base64 (44 bytes, siempre < 72) antes de pasarla a bcrypt.
_RONDAS = 12


def _preparar(contrasena: str) -> bytes:
    """SHA-256 de la contraseña en base64: longitud fija y segura para bcrypt."""
    return base64.b64encode(hashlib.sha256(contrasena.encode("utf-8")).digest())


def hash_password(contrasena: str) -> str:
    """Devuelve el hash bcrypt (con sal aleatoria) de la contraseña, listo para almacenar."""
    return bcrypt.hashpw(_preparar(contrasena), bcrypt.gensalt(rounds=_RONDAS)).decode("ascii")


def verificar_password(contrasena: str, hash_almacenado: str) -> bool:
    """Compara la contraseña con el hash almacenado (tiempo constante vía bcrypt)."""
    if not hash_almacenado:
        return False
    try:
        return bcrypt.checkpw(_preparar(contrasena), hash_almacenado.encode("ascii"))
    except (ValueError, TypeError):
        return False
