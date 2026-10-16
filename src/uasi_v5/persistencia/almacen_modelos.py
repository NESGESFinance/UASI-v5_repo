"""Persistencia segura: joblib + checksum/HMAC + FileLock [P1-03] [P2-02]."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
from typing import Any, Optional, Tuple
from uuid import uuid4

import joblib
from filelock import FileLock

from uasi_v5.core.config import PATH_MODELOS, UASI_MODEL_HMAC_KEY
from uasi_v5.core.excepciones import IntegridadModeloError
from uasi_v5.core.registro import logger

_LOCK_PROCESO = threading.RLock()  # Exclusión intra-proceso (complementa a FileLock)


def _digest_archivo(path: str) -> str:
    """SHA-256 (o HMAC-SHA256 si hay clave) del contenido del archivo."""
    if UASI_MODEL_HMAC_KEY:
        h = hmac.new(UASI_MODEL_HMAC_KEY.encode(), digestmod=hashlib.sha256)
        prefijo = "hmac-sha256:"
    else:
        h = hashlib.sha256()
        prefijo = "sha256:"
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return prefijo + h.hexdigest()


def _escritura_atomica_texto(path: str, contenido: str) -> None:
    tmp = f"{path}.tmp-{uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contenido)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def guardar_modelo_seguro(obj: Any, path: str) -> str:
    """
    Serializa con joblib de forma atómica y escribe `<path>.sha256` al lado.
    Todo bajo FileLock (multi-proceso) + RLock (multi-hilo).
    Devuelve el digest escrito.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK_PROCESO, FileLock(f"{path}.lock", timeout=60):
        tmp = f"{path}.tmp-{uuid4().hex}"
        joblib.dump(obj, tmp, compress=3)
        digest = _digest_archivo(tmp)
        os.replace(tmp, path)
        _escritura_atomica_texto(f"{path}.sha256", digest + "\n")
    return digest


def verificar_checksum(path: str) -> Tuple[bool, str]:
    """Comprueba el checksum sin deserializar. Devuelve (ok, motivo)."""
    ruta_sha = f"{path}.sha256"
    if not os.path.exists(path):
        return False, "inexistente"
    if not os.path.exists(ruta_sha):
        return False, "checksum_ausente"
    with open(ruta_sha, "r", encoding="utf-8") as f:
        esperado = f.read().strip()
    real = _digest_archivo(path)
    if not hmac.compare_digest(esperado, real):
        return False, "checksum_no_coincide"
    return True, "ok"


def cargar_modelo_seguro(path: str) -> Optional[Any]:
    """
    Verifica el checksum ANTES de deserializar (joblib usa pickle internamente,
    así que nunca se carga un artefacto cuya integridad no esté acreditada).
    Devuelve None si el modelo no existe; lanza IntegridadModeloError si falla.
    """
    if not os.path.exists(path):
        return None
    with _LOCK_PROCESO, FileLock(f"{path}.lock", timeout=60):
        ok, motivo = verificar_checksum(path)
        if not ok:
            logger.error("integridad_modelo_fallida", extra={"ruta": path, "motivo": motivo})
            raise IntegridadModeloError(f"Integridad del artefacto '{os.path.basename(path)}' comprometida: {motivo}.")
        return joblib.load(path)


def ruta_modelo(nombre: str) -> str:
    return os.path.join(PATH_MODELOS, f"{nombre}.joblib")
