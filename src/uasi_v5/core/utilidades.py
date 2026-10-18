"""Utilidades transversales: reloj UTC, JSON canónico y SHA-256."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any


def ahora_utc() -> datetime:
    """[P1-07] Única fuente de tiempo del sistema: timezone-aware en UTC."""
    return datetime.now(UTC)


def json_canonico(obj: Any) -> str:
    """Serialización JSON determinista (claves ordenadas, sin espacios)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
