"""Logging estructurado JSON (logger `uasi_v5`)."""

from __future__ import annotations

import logging
import os
import sys

# --- Logging estructurado JSON (python-json-logger >= 3 usa pythonjsonlogger.json)
try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover - versiones antiguas
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore

# --- Logging -----------------------------------------------------------------
logger = logging.getLogger("uasi_v5")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "nivel"},
        )
    )
    logger.addHandler(_handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
