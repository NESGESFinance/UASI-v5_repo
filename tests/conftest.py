# -*- coding: utf-8 -*-
"""
Configuración compartida de pytest para UASI v5.

1) Layout `src/`: añade `<repo>/src` a sys.path (redundante con
   `pythonpath = ["src"]` de pyproject.toml, pero permite ejecutar los tests
   aunque se lancen sin esa configuración).

2) Alias de compatibilidad: `tests/test_uasi_v5.py` es una copia íntegra de la
   suite original, que importa el módulo por su nombre histórico
   `uasi_v5_enterprise_production`. Un MetaPathFinder resuelve ese nombre a
   `uasi_v5.main`, forzando una importación NUEVA para que el módulo lea las
   variables de entorno que el fixture `uasi` fija justo antes de importarlo.

3) Fixtures compartidas (`uasi`, `cliente`, `cabeceras`, `dataset_binario`).
   Las de mismo nombre definidas en `test_uasi_v5.py` tienen prioridad sobre
   éstas en ese módulo (regla estándar de pytest); aquí quedan disponibles para
   cualquier archivo de test nuevo.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import sys

import numpy as np
import pytest

RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_SRC = os.path.join(RAIZ_REPO, "src")
if RUTA_SRC not in sys.path:
    sys.path.insert(0, RUTA_SRC)

NOMBRE_HISTORICO = "uasi_v5_enterprise_production"
NOMBRE_REAL = "uasi_v5.main"

SECRETO_TEST = "clave-de-test-suficientemente-larga-0123456789abcdef"


class _AliasModuloHistorico(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resuelve `uasi_v5_enterprise_production` → `uasi_v5.main` (importación fresca)."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if fullname == NOMBRE_HISTORICO:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        sys.modules.pop(NOMBRE_REAL, None)  # reimporta con el entorno actual
        return importlib.import_module(NOMBRE_REAL)

    def exec_module(self, module):  # el módulo ya está ejecutado en create_module
        return None


if not any(isinstance(f, _AliasModuloHistorico) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasModuloHistorico())


def configurar_entorno_test(workspace: str) -> None:
    """Entorno aislado: workspace temporal, secreto de test, rate limit alto."""
    os.environ.update(
        {
            "PATH_WORKSPACE": workspace,
            "SECRET_JWT_KEY": SECRETO_TEST,
            "RATE_LIMIT_PER_MINUTE": "10000",
            "RATE_LIMIT_STORAGE_URI": "memory://",
            "UASI_ENV": "development",
            "LOG_LEVEL": "WARNING",
        }
    )


@pytest.fixture(scope="session")
def uasi(tmp_path_factory):
    """Módulo principal importado con un workspace aislado."""
    configurar_entorno_test(str(tmp_path_factory.mktemp("uasi_ws")))
    sys.modules.pop(NOMBRE_HISTORICO, None)
    return importlib.import_module(NOMBRE_HISTORICO)


@pytest.fixture(scope="session")
def cliente(uasi):
    """TestClient con lifespan activo (crea el bloque GENESIS del ledger)."""
    from fastapi.testclient import TestClient

    with TestClient(uasi.app) as c:
        yield c


@pytest.fixture(scope="session")
def cabeceras(uasi):
    """Cabecera Authorization con un JWT válido."""
    return {"Authorization": f"Bearer {uasi.emitir_token('tester')}"}


@pytest.fixture
def dataset_binario():
    """Fábrica de datasets binarios linealmente separables (reproducibles)."""

    def _fabrica(n: int = 120, d: int = 4, semilla: int = 0):
        rng = np.random.default_rng(semilla)
        X = rng.normal(size=(n, d))
        y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
        return X, y

    return _fabrica
