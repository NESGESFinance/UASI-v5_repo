"""Configuración centralizada: todas las constantes se leen de variables de entorno al importar."""

from __future__ import annotations

import os


def _env_int(nombre: str, defecto: int) -> int:
    try:
        return int(os.getenv(nombre, str(defecto)))
    except ValueError:
        return defecto


def _env_float(nombre: str, defecto: float) -> float:
    try:
        return float(os.getenv(nombre, str(defecto)))
    except ValueError:
        return defecto


# --- Constantes de configuración (todas sobreescribibles por entorno) ---------
VERSION_UASI = "5.2.0-enterprise"
UASI_ENV = os.getenv("UASI_ENV", "development").lower()
PATH_WORKSPACE = os.path.abspath(os.getenv("PATH_WORKSPACE", "./uasi_workspace"))
PATH_MODELOS = os.path.join(PATH_WORKSPACE, "modelos")
PATH_LEDGER = os.path.join(PATH_WORKSPACE, "ledger")
PATH_OTS = os.path.join(PATH_WORKSPACE, "ots_proofs")
ARCHIVO_LEDGER = os.path.join(PATH_LEDGER, "ledger_auditoria.jsonl")

SECRET_JWT_KEY = os.getenv("SECRET_JWT_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("JWT_ISSUER", "uasi-v5")
JWT_EXP_MINUTES = _env_int("JWT_EXP_MINUTES", 60)

RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 30)
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
MAX_PAYLOAD_BYTES = _env_int("MAX_PAYLOAD_BYTES", 10 * 1024 * 1024)  # 10 MB
MAX_MUESTRAS = _env_int("UASI_MAX_MUESTRAS", 50_000)
MAX_FEATURES = _env_int("UASI_MAX_FEATURES", 512)

UASI_MODEL_HMAC_KEY = os.getenv("UASI_MODEL_HMAC_KEY", "")

# Capa JEV (JEV → UASI multiagente → JEV)
JEV_MAX_WORKERS = _env_int("JEV_MAX_WORKERS", 4)
# Solo informativa: la firma lee JEV_HMAC_KEY con os.getenv en cada petición (rotación sin reinicio).
JEV_HMAC_KEY = os.getenv("JEV_HMAC_KEY", "")
# Caché determinista de resultados de agentes (0 desactiva). Leídas al arrancar.
JEV_CACHE_MAX = _env_int("JEV_CACHE_MAX", 512)
JEV_CACHE_TTL = _env_int("JEV_CACHE_TTL", 600)
# Informativas: se leen con os.getenv en cada petición (ver seguridad/jev_replay.py,
# agentes_jev/callback.py y api/rutas/jev.py).
JEV_VENTANA_SEGUNDOS = _env_int("JEV_VENTANA_SEGUNDOS", 300)
JEV_LOTE_MAX = _env_int("JEV_LOTE_MAX", 20)
JEV_CALLBACK_HOSTS = os.getenv("JEV_CALLBACK_HOSTS", "")

BITCOIN_NETWORK = os.getenv("BITCOIN_NETWORK", "mainnet").lower()
if BITCOIN_NETWORK not in ("mainnet", "testnet"):
    BITCOIN_NETWORK = "mainnet"
_CALENDARIOS_DEFECTO = (
    "https://a.pool.opentimestamps.org,"
    "https://b.pool.opentimestamps.org,"
    "https://a.pool.eternitywall.com"
)
OTS_CALENDARS = [u.strip() for u in os.getenv("OTS_CALENDARS", _CALENDARIOS_DEFECTO).split(",") if u.strip()]
OTS_MIN_CALENDARS = _env_int("OTS_MIN_CALENDARS", 1)
OTS_TIMEOUT_SECONDS = _env_int("OTS_TIMEOUT_SECONDS", 10)
ESPLORA_API_URL = os.getenv(
    "ESPLORA_API_URL",
    "https://blockstream.info/testnet/api" if BITCOIN_NETWORK == "testnet" else "https://blockstream.info/api",
)

# Umbrales metacognitivos
UMBRAL_ENTROPIA_DESBALANCE = _env_float("UASI_UMBRAL_ENTROPIA_DESBALANCE", 0.85)
UMBRAL_ENTROPIA_FEATURES = _env_float("UASI_UMBRAL_ENTROPIA_FEATURES", 0.90)
