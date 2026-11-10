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
VERSION_UASI = "5.4.0-enterprise"
UASI_ENV = os.getenv("UASI_ENV", "development").lower()
PATH_WORKSPACE = os.path.abspath(os.getenv("PATH_WORKSPACE", "./uasi_workspace"))
PATH_MODELOS = os.path.join(PATH_WORKSPACE, "modelos")
PATH_LEDGER = os.path.join(PATH_WORKSPACE, "ledger")
PATH_OTS = os.path.join(PATH_WORKSPACE, "ots_proofs")
ARCHIVO_LEDGER = os.path.join(PATH_LEDGER, "ledger_auditoria.jsonl")

# JWT_SECRET se acepta como alias de SECRET_JWT_KEY (prioridad: SECRET_JWT_KEY).
SECRET_JWT_KEY = os.getenv("SECRET_JWT_KEY", "") or os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("JWT_ISSUER", "uasi-v5")
JWT_EXP_MINUTES = _env_int("JWT_EXP_MINUTES", 60)

RATE_LIMIT_PER_MINUTE = _env_int("RATE_LIMIT_PER_MINUTE", 30)
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
MAX_PAYLOAD_BYTES = _env_int("MAX_PAYLOAD_BYTES", 10 * 1024 * 1024)  # 10 MB
MAX_MUESTRAS = _env_int("UASI_MAX_MUESTRAS", 50_000)
MAX_FEATURES = _env_int("UASI_MAX_FEATURES", 512)

# SECRET_KEY: secreto general del servidor (firma HMAC de manifiestos de modelos).
# UASI_MODEL_HMAC_KEY se mantiene como alias heredado (prioridad: SECRET_KEY).
SECRET_KEY = os.getenv("SECRET_KEY", "") or os.getenv("UASI_MODEL_HMAC_KEY", "")
UASI_MODEL_HMAC_KEY = SECRET_KEY

# Base de datos (SQLAlchemy): SQLite en desarrollo, PostgreSQL en producción.
DATABASE_URL = os.getenv("DATABASE_URL", "") or f"sqlite:///{os.path.join(PATH_WORKSPACE, 'uasi.db')}"
# Backend del ledger de auditoría: "sql" (tabla ledger_entries, por defecto) o "jsonl" (heredado).
UASI_LEDGER_BACKEND = os.getenv("UASI_LEDGER_BACKEND", "sql").lower()
if UASI_LEDGER_BACKEND not in ("sql", "jsonl"):
    UASI_LEDGER_BACKEND = "sql"

# Rate limiting distribuido por grupo de rutas (peticiones/minuto/IP).
RATE_LIMIT_JEV_PER_MINUTE = _env_int("RATE_LIMIT_JEV_PER_MINUTE", 30)
RATE_LIMIT_ADMIN_PER_MINUTE = _env_int("RATE_LIMIT_ADMIN_PER_MINUTE", 100)

# Privacidad diferencial (mecanismo gaussiano) para las métricas expuestas por la API.
UASI_DP_ACTIVA = os.getenv("UASI_DP_ACTIVA", "1").strip().lower() not in ("0", "false", "no")
UASI_DP_EPSILON = _env_float("UASI_DP_EPSILON", 1.0)
UASI_DP_DELTA = _env_float("UASI_DP_DELTA", 1e-5)

# WebSocket /ws/agents: periodo de emisión del estado de agentes (segundos).
UASI_WS_INTERVALO_SEGUNDOS = _env_float("UASI_WS_INTERVALO_SEGUNDOS", 5.0)

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


# --- Validación estricta de secretos al arranque ------------------------------
LONGITUD_MINIMA_SECRETO = 32
# Valores de ejemplo conocidos (documentación, .env.example, tutoriales): nunca válidos.
SECRETOS_POR_DEFECTO = frozenset({
    "cambie-esto-por-un-secreto-aleatorio-de-al-menos-32-caracteres",
    "cambie-esto-por-otro-secreto-aleatorio-distinto",
    "changeme", "change-me", "secret", "secreto", "default", "password", "uasi", "uasi-v5",
    "your-secret-key", "supersecret", "dev-secret",
})


def _problema_secreto(nombre: str, valor: str) -> str | None:
    """Devuelve la descripción del problema del secreto o None si es aceptable."""
    if valor.strip().lower() in SECRETOS_POR_DEFECTO:
        return f"{nombre} usa un valor por defecto/de ejemplo; genere uno con ./scripts/generar_secretos.sh"
    if len(valor) < LONGITUD_MINIMA_SECRETO:
        return f"{nombre} debe tener al menos {LONGITUD_MINIMA_SECRETO} caracteres (tiene {len(valor)})"
    if len(set(valor)) < 8:
        return f"{nombre} tiene entropía insuficiente (menos de 8 caracteres distintos)"
    return None


def validar_secretos(entorno: str | None = None, secret_key: str | None = None,
                     jwt_secret: str | None = None) -> list[str]:
    """
    Valida SECRET_KEY y SECRET_JWT_KEY (alias JWT_SECRET) al arrancar la aplicación.

    - Un secreto DEFINIDO pero débil (< 32 caracteres, valor por defecto o baja entropía)
      provoca RuntimeError en cualquier entorno: nunca se arranca con secretos débiles.
    - SECRET_KEY y el secreto JWT deben ser distintos (separación de claves).
    - En producción ambos son obligatorios (RuntimeError si faltan).
    - En desarrollo un secreto AUSENTE solo genera advertencias: los endpoints protegidos
      responden 503 (fail-closed) y los manifiestos de modelos usan una clave derivada.

    Devuelve la lista de advertencias (no fatales).
    """
    entorno = (entorno if entorno is not None else UASI_ENV).lower()
    secretos = {
        "SECRET_KEY": SECRET_KEY if secret_key is None else secret_key,
        "SECRET_JWT_KEY": SECRET_JWT_KEY if jwt_secret is None else jwt_secret,
    }
    errores: list[str] = []
    advertencias: list[str] = []
    for nombre, valor in secretos.items():
        if not valor:
            (errores if entorno == "production" else advertencias).append(f"{nombre} no está definida")
            continue
        problema = _problema_secreto(nombre, valor)
        if problema:
            errores.append(problema)
    if secretos["SECRET_KEY"] and secretos["SECRET_KEY"] == secretos["SECRET_JWT_KEY"]:
        errores.append("SECRET_KEY y SECRET_JWT_KEY deben ser distintas (separación de claves)")
    if errores:
        raise RuntimeError("Configuración de secretos insegura; UASI no arrancará: " + "; ".join(errores))
    return advertencias
