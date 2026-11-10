"""
Persistencia segura de modelos: joblib + manifiesto JSON firmado con HMAC-SHA256 [P1-03] [P2-02].

Formato en disco (por cada artefacto `<ruta>`):
    <ruta>                  binario joblib (nunca `pickle.dump` directo)
    <ruta>.manifest.json    {"path", "sha256", "hmac", "timestamp", "version", "algoritmo", "formato"}

    hmac = HMAC-SHA256(clave_modelos, sha256_del_binario)

Al cargar se verifica, ANTES de deserializar: existencia del manifiesto, coincidencia del
nombre de archivo, SHA-256 real del binario y firma HMAC (comparación en tiempo constante).
joblib usa pickle internamente, por eso nunca se llama a `joblib.load()` sobre un binario
cuya integridad y autenticidad no estén acreditadas: un archivo creado con `pickle.dump`
(sin manifiesto firmado) o manipulado se rechaza con `IntegridadModeloError`.

Clave: SECRET_KEY (alias UASI_MODEL_HMAC_KEY). En desarrollo, si falta, se deriva de
SECRET_JWT_KEY con separación de dominio; en producción es obligatoria (validar_secretos).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

import joblib
from filelock import FileLock

from uasi_v5.core import config
from uasi_v5.core.excepciones import IntegridadModeloError
from uasi_v5.core.registro import logger
from uasi_v5.core.utilidades import ahora_utc

_LOCK_PROCESO = threading.RLock()  # Exclusión intra-proceso (complementa a FileLock)
SUFIJO_MANIFIESTO = ".manifest.json"
FORMATO_MANIFIESTO = "uasi-modelo-joblib-hmac/1"
_DOMINIO_DERIVACION = b"uasi-v5/modelos/hmac"


def _clave_modelos() -> bytes:
    """Clave HMAC de manifiestos. Falla cerrado si no hay ningún secreto disponible."""
    if config.SECRET_KEY:
        return config.SECRET_KEY.encode()
    if config.SECRET_JWT_KEY and config.UASI_ENV != "production":
        # Derivación con separación de dominio: la clave de modelos nunca es la clave JWT literal.
        return hmac.new(config.SECRET_JWT_KEY.encode(), _DOMINIO_DERIVACION, hashlib.sha256).digest()
    raise IntegridadModeloError("SECRET_KEY no configurada: no se pueden firmar ni verificar modelos.")


def _sha256_archivo(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _firmar(sha256: str) -> str:
    return hmac.new(_clave_modelos(), sha256.encode(), hashlib.sha256).hexdigest()


def ruta_manifiesto(path: str) -> str:
    return f"{path}{SUFIJO_MANIFIESTO}"


def _escritura_atomica_texto(path: str, contenido: str) -> None:
    tmp = f"{path}.tmp-{uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contenido)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def guardar_modelo_seguro(obj: Any, path: str) -> str:
    """
    1) joblib.dump() a un temporal, 2) SHA-256 del binario, 3) manifiesto firmado con HMAC,
    4) os.replace atómico. Todo bajo FileLock (multi-proceso) + RLock (multi-hilo).
    Devuelve el digest en formato "sha256:<hex>".
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _LOCK_PROCESO, FileLock(f"{path}.lock", timeout=60):
        tmp = f"{path}.tmp-{uuid4().hex}"
        try:
            joblib.dump(obj, tmp, compress=3)
            sha256 = _sha256_archivo(tmp)
            manifiesto = {
                "path": os.path.basename(path),
                "sha256": sha256,
                "hmac": _firmar(sha256),
                "timestamp": ahora_utc().isoformat(),
                "version": config.VERSION_UASI,
                "algoritmo": "HMAC-SHA256",
                "formato": FORMATO_MANIFIESTO,
            }
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        _escritura_atomica_texto(ruta_manifiesto(path), json.dumps(manifiesto, sort_keys=True, indent=2) + "\n")
        heredado = f"{path}.sha256"
        if os.path.exists(heredado):  # checksum del formato < v5.3.0: ya no se usa
            os.remove(heredado)
    return f"sha256:{sha256}"


def leer_manifiesto(path: str) -> Optional[Dict[str, Any]]:
    ruta = ruta_manifiesto(path)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def verificar_checksum(path: str) -> Tuple[bool, str]:
    """Verifica manifiesto + SHA-256 + HMAC SIN deserializar. Devuelve (ok, motivo)."""
    if not os.path.exists(path):
        return False, "inexistente"
    manifiesto = leer_manifiesto(path)
    if manifiesto is None:
        return False, "manifiesto_ausente_o_invalido"
    if manifiesto.get("formato") != FORMATO_MANIFIESTO:
        return False, "formato_no_soportado"
    if manifiesto.get("path") != os.path.basename(path):
        return False, "manifiesto_de_otro_artefacto"
    sha256 = _sha256_archivo(path)
    if not hmac.compare_digest(str(manifiesto.get("sha256", "")).encode(), sha256.encode()):
        return False, "checksum_no_coincide"
    try:
        esperado = _firmar(sha256)
    except IntegridadModeloError:
        return False, "clave_hmac_no_configurada"
    if not hmac.compare_digest(str(manifiesto.get("hmac", "")).encode(), esperado.encode()):
        return False, "firma_hmac_invalida"
    return True, "ok"


def cargar_modelo_seguro(path: str) -> Optional[Any]:
    """
    Verifica manifiesto y firma HMAC ANTES de llamar a joblib.load().
    Devuelve None si el modelo no existe; lanza IntegridadModeloError si la verificación falla.
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
    return os.path.join(config.PATH_MODELOS, f"{nombre}.joblib")
