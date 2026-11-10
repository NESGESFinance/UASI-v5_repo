"""Persistencia segura de modelos: joblib + manifiesto JSON firmado con HMAC-SHA256 (sin pickle)."""

from __future__ import annotations

import hashlib
import json
import pickle
import sys

import numpy as np
import pytest


@pytest.fixture
def almacen(uasi):
    return sys.modules["uasi_v5.persistencia.almacen_modelos"]


def test_joblib_hmac_ida_y_vuelta(almacen, tmp_path):
    ruta = str(tmp_path / "modelo.joblib")
    digest = almacen.guardar_modelo_seguro({"w": np.arange(4)}, ruta)
    manifiesto = json.loads((tmp_path / "modelo.joblib.manifest.json").read_text())
    assert set(manifiesto) >= {"path", "sha256", "hmac", "timestamp", "version"}
    assert manifiesto["path"] == "modelo.joblib"
    assert digest == f"sha256:{manifiesto['sha256']}"
    assert manifiesto["sha256"] == hashlib.sha256((tmp_path / "modelo.joblib").read_bytes()).hexdigest()
    assert almacen.verificar_checksum(ruta) == (True, "ok")
    assert almacen.cargar_modelo_seguro(ruta)["w"].tolist() == [0, 1, 2, 3]


def test_modelo_pickle_sin_manifiesto_es_rechazado(almacen, uasi, tmp_path):
    ruta = tmp_path / "malicioso.joblib"
    ruta.write_bytes(pickle.dumps({"payload": "no se debe deserializar"}))
    with pytest.raises(uasi.IntegridadModeloError, match="manifiesto"):
        almacen.cargar_modelo_seguro(str(ruta))


def test_pickle_con_manifiesto_falsificado_es_rechazado(almacen, uasi, tmp_path):
    """Un atacante con escritura en disco puede recalcular el SHA-256, pero no el HMAC."""
    ruta = tmp_path / "falso.joblib"
    ruta.write_bytes(pickle.dumps({"x": 1}))
    sha = hashlib.sha256(ruta.read_bytes()).hexdigest()
    (tmp_path / "falso.joblib.manifest.json").write_text(json.dumps({
        "path": "falso.joblib", "sha256": sha, "hmac": "0" * 64, "timestamp": "2026-01-01T00:00:00+00:00",
        "version": "5.3.0-enterprise", "formato": almacen.FORMATO_MANIFIESTO}))
    assert almacen.verificar_checksum(str(ruta)) == (False, "firma_hmac_invalida")
    with pytest.raises(uasi.IntegridadModeloError):
        almacen.cargar_modelo_seguro(str(ruta))


def test_binario_manipulado_y_manifiesto_ajeno(almacen, uasi, tmp_path):
    a, b = str(tmp_path / "a.joblib"), str(tmp_path / "b.joblib")
    almacen.guardar_modelo_seguro([1, 2, 3], a)
    almacen.guardar_modelo_seguro([4, 5, 6], b)
    # Manifiesto válido de OTRO artefacto
    (tmp_path / "b.joblib.manifest.json").write_text((tmp_path / "a.joblib.manifest.json").read_text())
    assert almacen.verificar_checksum(b)[1] == "manifiesto_de_otro_artefacto"
    with open(a, "ab") as f:
        f.write(b"tamper")
    assert almacen.verificar_checksum(a)[1] == "checksum_no_coincide"
    with pytest.raises(uasi.IntegridadModeloError):
        almacen.cargar_modelo_seguro(a)


def test_cambio_de_clave_invalida_la_firma(almacen, tmp_path, monkeypatch):
    ruta = str(tmp_path / "m.joblib")
    almacen.guardar_modelo_seguro({"k": 1}, ruta)
    monkeypatch.setattr(almacen.config, "SECRET_KEY", "otra-clave-completamente-distinta-0123456789")
    assert almacen.verificar_checksum(ruta) == (False, "firma_hmac_invalida")


def test_sin_secretos_falla_cerrado(almacen, uasi, tmp_path, monkeypatch):
    monkeypatch.setattr(almacen.config, "SECRET_KEY", "")
    monkeypatch.setattr(almacen.config, "SECRET_JWT_KEY", "")
    with pytest.raises(uasi.IntegridadModeloError):
        almacen.guardar_modelo_seguro({"k": 1}, str(tmp_path / "m.joblib"))


def test_el_codigo_no_usa_pickle():
    """Análisis estático (AST): el módulo no importa pickle ni lo invoca."""
    import ast
    import pathlib

    fuente = pathlib.Path(__file__).parents[1] / "src" / "uasi_v5" / "persistencia" / "almacen_modelos.py"
    arbol = ast.parse(fuente.read_text(encoding="utf-8"))
    importados = {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    importados |= {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in importados if "pickle" in m}
    assert not [n for n in ast.walk(arbol) if isinstance(n, ast.Name) and "pickle" in n.id]
