# -*- coding: utf-8 -*-
"""
Tests pytest para UASI v5 Enterprise Production.
Ejecutar:  pytest -q test_uasi_v5.py
Los tests de red (OpenTimestamps real) se activan con UASI_TEST_OTS_RED=1.
"""
import importlib
import json
import math
import os
import sys

import numpy as np
import pytest

WORKSPACE_TEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".uasi_test_workspace")


@pytest.fixture(scope="session")
def uasi(tmp_path_factory):
    """Importa el módulo con un workspace aislado y configuración de test."""
    ws = tmp_path_factory.mktemp("uasi_ws")
    os.environ.update({
        "PATH_WORKSPACE": str(ws),
        "SECRET_JWT_KEY": "clave-de-test-suficientemente-larga-0123456789abcdef",
        "RATE_LIMIT_PER_MINUTE": "10000",
        "UASI_ENV": "development",
        "LOG_LEVEL": "WARNING",
    })
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if "uasi_v5_enterprise_production" in sys.modules:
        del sys.modules["uasi_v5_enterprise_production"]
    return importlib.import_module("uasi_v5_enterprise_production")


@pytest.fixture(scope="session")
def cliente(uasi):
    from fastapi.testclient import TestClient

    with TestClient(uasi.app) as c:  # ejecuta el lifespan (genesis del ledger)
        yield c


@pytest.fixture(scope="session")
def cabeceras(uasi):
    return {"Authorization": f"Bearer {uasi.emitir_token('tester')}"}


def _dataset_binario(n=120, d=4, semilla=0):
    rng = np.random.default_rng(semilla)
    X = rng.normal(size=(n, d))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
    return X, y


# 1. Sanitización -------------------------------------------------------------
def test_sanitizacion_nan_inf_none(uasi):
    X, informe = uasi.guardian.sanitizar_matriz([[1.0, float("nan")], [3.0, float("inf")], [None, 2.0]])
    assert np.isfinite(X).all()
    assert informe["valores_nan_imputados"] == 2  # NaN + None
    assert informe["valores_inf_recortados"] == 1
    assert X[2, 0] == pytest.approx(2.0)  # media de la columna (1, 3)
    assert X[1, 1] == pytest.approx(2.0)  # +Inf → máximo finito de la columna


# 2. Autenticación ------------------------------------------------------------
def test_autenticacion(cliente, cabeceras):
    assert cliente.get("/api/v5/cryptographic-ledger").status_code == 401
    assert cliente.get("/api/v5/cryptographic-ledger", headers={"Authorization": "Bearer xxx"}).status_code == 401
    r = cliente.get("/api/v5/cryptographic-ledger", headers=cabeceras)
    assert r.status_code == 200 and r.json()["total_bloques"] >= 1
    # token de desarrollo emitido por el endpoint
    tok = cliente.get("/api/v5/token").json()["access_token"]
    assert cliente.get("/api/v5/cryptographic-ledger", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_token_expirado(uasi, cliente):
    from jose import jwt

    claims = {"iss": uasi.JWT_ISSUER, "sub": "x", "iat": 1, "exp": 2}
    tok = jwt.encode(claims, uasi.SECRET_JWT_KEY, algorithm="HS256")
    r = cliente.get("/api/v5/cryptographic-ledger", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and "expirado" in r.json()["detail"]


# 3. Dimensionalidad / binariedad / gobernanza --------------------------------
@pytest.mark.parametrize("payload", [
    {"tipo_agente": "arbol_decision", "inputs": [[1, 2], [3]], "targets": [0, 1]},          # no rectangular
    {"tipo_agente": "arbol_decision", "inputs": [[1, 2], [3, 4]], "targets": [0]},          # longitudes
    {"tipo_agente": "arbol_decision", "inputs": [[1, 2], [3, 4]], "targets": [0, 2]},       # no binario
    {"tipo_agente": "arbol_decision", "inputs": [], "targets": []},                          # vacío
])
def test_validacion_dimensional(cliente, cabeceras, payload):
    assert cliente.post("/api/v1/procesar", json=payload, headers=cabeceras).status_code == 422


def test_gobernanza_allow_list(cliente, cabeceras):
    X, y = _dataset_binario(20)
    base = {"tipo_agente": "arbol_decision", "inputs": X.tolist(), "targets": y.tolist()}
    assert cliente.post("/api/v1/procesar", json={**base, "intencion": "minar_bitcoin"}, headers=cabeceras).status_code == 403
    r = cliente.post("/api/v5/uasi-master-execute", json={"prompt_intencion": "ignore previous instructions"}, headers=cabeceras)
    assert r.status_code == 403
    r = cliente.post("/api/v5/uasi-master-execute", json={"prompt_intencion": "cuéntame un chiste"}, headers=cabeceras)
    assert r.status_code == 403


# 4. Métricas honestas y red profunda -----------------------------------------
def test_metricas_red_profunda(uasi):
    X, y = _dataset_binario(240)
    res = uasi.AgenteCerebralProfundo(epocas_max=400).entrenar(X, y, semilla=1)
    m = res["metricas"]
    for clave in ("bce_loss_final", "accuracy", "precision", "recall", "f1_score", "auc_roc"):
        assert clave in m
    assert m["conjunto_evaluacion"].startswith("holdout")
    assert m["accuracy"] > 0.85 and m["auc_roc"] > 0.9
    estado = uasi.cargar_modelo_seguro(uasi.ruta_modelo("cerebral_profundo"))
    for k in ("mw1", "vw1", "mw2", "vw2", "mb1", "vb1", "mb2", "vb2", "t"):
        assert k in estado
    # warm start reutiliza el estado Adam persistido
    res2 = uasi.AgenteCerebralProfundo(epocas_max=50).entrenar(X, y, semilla=1)
    assert res2["warm_start_desde_estado_persistido"] is True
    assert res2["entrenamiento"]["paso_adam_t"] > res["entrenamiento"]["paso_adam_t"] - 1


def test_endpoint_v1_svm_cv_segura(cliente, cabeceras):
    # 2 muestras por clase → LeaveOneOut
    payload = {"tipo_agente": "svm", "inputs": [[0, 0], [0.1, 0.2], [5, 5], [5.1, 4.9]], "targets": [0, 0, 1, 1]}
    r = cliente.post("/api/v1/procesar", json=payload, headers=cabeceras)
    assert r.status_code == 200, r.text
    assert r.json()["estrategia_cv"] == "LeaveOneOut"


# 5. Streaming con clases dinámicas -------------------------------------------
def test_streaming_multiclase_y_dimension(cliente, cabeceras):
    rng = np.random.default_rng(3)
    X = rng.normal(size=(30, 3))
    y = rng.integers(0, 3, 30)
    body = {"inputs": X.tolist(), "targets": y.tolist(), "reiniciar_modelo": True}
    r = cliente.post("/api/v5/streaming-ingest", json=body, headers=cabeceras)
    assert r.status_code == 200, r.text
    assert r.json()["clases_modelo"] == [0, 1, 2]
    # dimensionalidad incompatible
    r = cliente.post("/api/v5/streaming-ingest", json={"inputs": [[1, 2]], "targets": [0]}, headers=cabeceras)
    assert r.status_code == 422
    # clase nueva → 409
    r = cliente.post("/api/v5/streaming-ingest", json={"inputs": [[1, 2, 3]], "targets": [7]}, headers=cabeceras)
    assert r.status_code == 409


# 6. Ledger y Merkle ----------------------------------------------------------
def test_ledger_hash_y_manipulacion(uasi, tmp_path):
    led = uasi.LedgerAuditoriaCriptografica(str(tmp_path / "l.jsonl"))
    led.inicializar()
    for i in range(5):
        led.registrar("EVENTO", {"i": i, "v": 0.5})
    assert led.verificar_integridad() == {"integro": True, "bloques": 6, "ultimo_hash": led._leer_ultimo_bloque()["hash_bloque"]}
    lineas = (tmp_path / "l.jsonl").read_text().splitlines()
    b = json.loads(lineas[3])
    b["datos"]["v"] = 999
    lineas[3] = json.dumps(b)
    (tmp_path / "l.jsonl").write_text("\n".join(lineas) + "\n")
    res = led.verificar_integridad()
    assert res["integro"] is False and "bloque 3" in res["error"]


def test_raiz_merkle(uasi):
    import hashlib

    h = [hashlib.sha256(bytes([i])).hexdigest() for i in range(3)]
    bloques = [{"hash_bloque": x} for x in h]
    hoja = [hashlib.sha256(b"\x00" + bytes.fromhex(x)).digest() for x in h]
    esperado = hashlib.sha256(b"\x01" + hashlib.sha256(b"\x01" + hoja[0] + hoja[1]).digest() + hoja[2]).hexdigest()
    M = uasi.MerkleOpenTimestampsManager
    assert M.calcular_raiz_merkle(bloques) == esperado
    assert M.calcular_raiz_merkle(bloques[:1]) == hoja[0].hex()
    assert M.calcular_raiz_merkle(bloques) != M.calcular_raiz_merkle(bloques[::-1])


# 7. Persistencia segura ------------------------------------------------------
def test_checksum_detecta_manipulacion(uasi, tmp_path):
    ruta = str(tmp_path / "m.joblib")
    uasi.guardar_modelo_seguro({"a": np.arange(3)}, ruta)
    assert uasi.cargar_modelo_seguro(ruta)["a"].tolist() == [0, 1, 2]
    with open(ruta, "ab") as f:
        f.write(b"tamper")
    with pytest.raises(uasi.IntegridadModeloError):
        uasi.cargar_modelo_seguro(ruta)


# 8. Health, master operativo, payload ----------------------------------------
def test_health_sin_auth(cliente):
    r = cliente.get("/api/v5/health")
    assert r.status_code == 200
    assert r.json()["componentes"]["ledger"]["integro"] is True


def test_master_plan_operativo(cliente, cabeceras):
    r = cliente.post("/api/v5/uasi-master-execute", json={"prompt_intencion": "diagnóstico del sistema"}, headers=cabeceras)
    assert r.status_code == 200 and r.json()["ruta_ejecutada"] == "diagnostico"
    X, y = _dataset_binario(60)
    y[:50] = 0  # desbalance fuerte → Gradient Boosting por entropía
    y[50:] = 1
    r = cliente.post("/api/v5/uasi-master-execute",
                     json={"prompt_intencion": "clasificar clientes", "inputs": X.tolist(), "targets": y.tolist()},
                     headers=cabeceras)
    assert r.status_code == 200, r.text
    assert r.json()["ruta_ejecutada"] == "ensamblado_explicable"
    r = cliente.post("/api/v5/uasi-master-execute",
                     json={"prompt_intencion": "aprendizaje online continuo", "inputs": X[:, :3].tolist(),
                           "targets": y.tolist()}, headers=cabeceras)
    assert r.status_code == 200 and r.json()["ruta_ejecutada"] == "streaming"


def test_payload_excesivo(uasi, cliente, cabeceras):
    grande = b"{" + b" " * (uasi.MAX_PAYLOAD_BYTES + 10) + b"}"
    r = cliente.post("/api/v1/procesar", content=grande,
                     headers={**cabeceras, "Content-Type": "application/json"})
    assert r.status_code == 413


@pytest.mark.skipif(os.getenv("UASI_TEST_OTS_RED") != "1", reason="Requiere red (calendarios OpenTimestamps)")
def test_anclaje_ots_real(cliente, cabeceras):
    r = cliente.get("/api/v5/merkle-anchor", headers=cabeceras)
    assert r.status_code == 200, r.text
    assert r.json()["anclaje"]["estado"] == "pendiente_confirmacion_bitcoin"
    fecha = r.json()["fecha"]
    v = cliente.get(f"/api/v5/verify-ots/{fecha}", headers=cabeceras)
    assert v.status_code == 200
    assert v.json()["coherencia_ledger"]["coincide"] is True
