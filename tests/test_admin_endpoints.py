"""API admin (/api/v5/admin/*), WebSocket /ws/agents, revocación, rate limiting distribuido y secretos."""

from __future__ import annotations

import sys

import pytest
from starlette.websockets import WebSocketDisconnect

RUTAS_GET = ["/api/v5/admin/agents", "/api/v5/admin/metrics", "/api/v5/admin/ledger",
             "/api/v5/admin/health/detailed"]


@pytest.fixture(scope="module")
def cab_admin(uasi):
    return {"Authorization": f"Bearer {uasi.emitir_token('admin-test', alcance='uasi:full admin')}"}


@pytest.mark.parametrize("ruta", RUTAS_GET)
def test_sin_token_401_y_sin_scope_403(cliente, cabeceras, ruta):
    assert cliente.get(ruta).status_code == 401
    assert cliente.get(ruta, headers=cabeceras).status_code == 403  # token válido sin scope admin


def test_anchor_sin_token(cliente, cabeceras):
    assert cliente.post("/api/v5/admin/anchor").status_code == 401
    assert cliente.post("/api/v5/admin/anchor", headers=cabeceras).status_code == 403


def test_agents(cliente, cab_admin):
    r = cliente.get("/api/v5/admin/agents", headers=cab_admin)
    assert r.status_code == 200, r.text
    agentes = r.json()["agentes"]
    ids = {a["id"] for a in agentes}
    assert {"jev:python", "jev:rust", "ml:svm_legacy"} <= ids
    jev = next(a for a in agentes if a["id"] == "jev:python")
    assert {"estado", "latencia_media_ms", "errores_24h", "ultima_actividad"} <= set(jev)


def test_metrics(cliente, cab_admin):
    r = cliente.get("/api/v5/admin/metrics", headers=cab_admin)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["http"]["peticiones_totales"] > 0 and "p95" in m["http"]["latencia_ms"]
    assert m["ledger"]["bloques"] >= 1 and "agent_metrics" in m["base_datos"]


def test_ledger_paginado_con_integridad(cliente, cab_admin):
    r = cliente.get("/api/v5/admin/ledger?page=1&limit=2", headers=cab_admin)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["integridad"]["integro"] is True and len(d["bloques"]) <= 2
    assert d["bloques"][0]["hash_anterior"] == "0" * 64
    assert cliente.get("/api/v5/admin/ledger?limit=500", headers=cab_admin).status_code == 422


def test_health_detailed(cliente, cab_admin):
    r = cliente.get("/api/v5/admin/health/detailed", headers=cab_admin)
    assert r.status_code == 200, r.text
    comp = r.json()["componentes"]
    assert comp["base_datos"]["estado"] == "ok"
    assert comp["redis"]["estado"] == "no_configurado"
    assert comp["agentes_jev"]["registrados"] == 7


def test_anchor_dispara_anclaje(cliente, cab_admin, monkeypatch):
    servicios = sys.modules["uasi_v5.servicios"]
    llamadas = []

    async def anclar_falso(raiz, fecha, n_bloques):
        llamadas.append((raiz, fecha, n_bloques))
        return {"estado": "pendiente_confirmacion_bitcoin", "calendarios": ["https://cal.test"]}

    monkeypatch.setattr(servicios.merkle_manager, "anclar_en_bitcoin", anclar_falso)
    r = cliente.post("/api/v5/admin/anchor", json={}, headers=cab_admin)
    assert r.status_code == 200, r.text
    assert len(llamadas) == 1 and r.json()["raiz_merkle"] == llamadas[0][0]
    ultimo = list(servicios.ledger.iterar())[-1]
    assert ultimo["evento"] == "ANCLAJE_MERKLE_OTS" and ultimo["actor"] == "admin-test"


def test_sesion_admin_registrada(cliente, cab_admin, uasi):
    from sqlalchemy import select

    cliente.get("/api/v5/admin/agents", headers=cab_admin)
    modelos, sesion = sys.modules["uasi_v5.db.modelos"], sys.modules["uasi_v5.db.sesion"]
    with sesion.sesion_bd() as s:
        filas = s.execute(select(modelos.AdminSession).where(modelos.AdminSession.sujeto == "admin-test")).scalars().all()
    assert filas and filas[0].peticiones >= 1


def test_revocacion_de_token(cliente, cab_admin, uasi):
    import jwt

    tok = uasi.emitir_token("revocable")
    jti = jwt.decode(tok, options={"verify_signature": False})["jti"]
    cab = {"Authorization": f"Bearer {tok}"}
    assert cliente.get("/api/v5/cryptographic-ledger", headers=cab).status_code == 200
    r = cliente.post("/api/v5/admin/tokens/revoke", json={"jti": jti, "motivo": "test"}, headers=cab_admin)
    assert r.status_code == 200 and r.json()["ya_estaba_revocado"] is False
    assert cliente.get("/api/v5/cryptographic-ledger", headers=cab).status_code == 401


def test_websocket_agentes(cliente, uasi):
    tok = uasi.emitir_token("ws-test")
    with cliente.websocket_connect(f"/ws/agents?token={tok}") as ws:
        m1, m2 = ws.receive_json(), ws.receive_json()  # emisión periódica
    assert m1["tipo"] == "estado_agentes" and m2["tipo"] == "estado_agentes"
    assert set(m1["agentes"][0]) == {"id", "nombre", "estado", "ultima_actividad"}


@pytest.mark.parametrize("consulta", ["", "?token=", "?token=basura"])
def test_websocket_sin_token_valido_rechazado(cliente, consulta):
    with pytest.raises(WebSocketDisconnect) as exc, cliente.websocket_connect(f"/ws/agents{consulta}") as ws:
        ws.receive_json()
    assert exc.value.code == 1008


# --- Rate limiting distribuido --------------------------------------------------------------
def _app_limitada(limitador, limite):
    from fastapi import FastAPI

    limites = sys.modules["uasi_v5.seguridad.limites"]
    app = FastAPI()

    @app.get("/api/v5/jev/ping")
    def ping():
        return {"ok": True}

    @app.get("/libre")
    def libre():
        return {"ok": True}

    app.add_middleware(limites.LimiteTasaDistribuidoMiddleware, limitador=limitador,
                       reglas=[("/api/v5/jev", limite, "jev")])
    return app


def test_rate_limit_memoria_429(uasi):
    from fastapi.testclient import TestClient

    limites = sys.modules["uasi_v5.seguridad.limites"]
    lim = limites.LimitadorDistribuido(None)
    assert lim.backend == "memoria"
    c = TestClient(_app_limitada(lim, 3))
    codigos = [c.get("/api/v5/jev/ping").status_code for _ in range(5)]
    assert codigos == [200, 200, 200, 429, 429]
    r = c.get("/api/v5/jev/ping")
    assert r.headers["retry-after"] and r.headers["x-ratelimit-limit"] == "3"
    assert all(c.get("/libre").status_code == 200 for _ in range(10))


def test_rate_limit_redis_compartido_entre_workers(uasi):
    fakeredis = pytest.importorskip("fakeredis")
    from fastapi.testclient import TestClient

    limites = sys.modules["uasi_v5.seguridad.limites"]
    servidor = fakeredis.FakeServer()
    w1 = limites.LimitadorDistribuido(cliente=fakeredis.FakeRedis(server=servidor, decode_responses=True))
    w2 = limites.LimitadorDistribuido(cliente=fakeredis.FakeRedis(server=servidor, decode_responses=True))
    c1, c2 = TestClient(_app_limitada(w1, 4)), TestClient(_app_limitada(w2, 4))
    codigos = [c.get("/api/v5/jev/ping").status_code for c in (c1, c2, c1, c2, c1)]
    assert codigos == [200, 200, 200, 200, 429]  # el contador es global, no por worker


def test_rate_limit_redis_caido_degrada_a_memoria(uasi):
    limites = sys.modules["uasi_v5.seguridad.limites"]

    class RedisRoto:
        def pipeline(self):
            raise ConnectionError("caído")

    lim = limites.LimitadorDistribuido(cliente=RedisRoto())
    assert lim.consumir("x", 1)[0] is True and lim.backend == "memoria"
    assert lim.consumir("x", 1)[0] is False


# --- Validación de secretos al arranque ----------------------------------------------------
def test_validacion_secretos(uasi):
    config = sys.modules["uasi_v5.core.config"]
    fuerte_a, fuerte_b = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7", "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4j3"
    assert config.validar_secretos("production", fuerte_a, fuerte_b) == []
    with pytest.raises(RuntimeError, match="32"):
        config.validar_secretos("development", "corta", fuerte_b)
    with pytest.raises(RuntimeError, match="JWT"):
        config.validar_secretos("development", fuerte_a, "x" * 10)
    with pytest.raises(RuntimeError, match="por defecto"):
        config.validar_secretos("development", "cambie-esto-por-un-secreto-aleatorio-de-al-menos-32-caracteres", fuerte_b)
    with pytest.raises(RuntimeError, match="entropía"):
        config.validar_secretos("development", "a" * 40, fuerte_b)
    with pytest.raises(RuntimeError, match="distintas"):
        config.validar_secretos("development", fuerte_a, fuerte_a)
    with pytest.raises(RuntimeError, match="no está definida"):
        config.validar_secretos("production", "", fuerte_b)
    assert config.validar_secretos("development", "", fuerte_b) == ["SECRET_KEY no está definida"]
