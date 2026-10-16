"""Tests de automatización/optimización JEV: anti-replay, idempotencia, caché, lotes, métricas,
callbacks y SDK cliente de referencia."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clientes" / "python"))
import jev_cliente  # noqa: E402

CLAVE_JEV = "clave-jev-de-test-0123456789abcdef0123456789"
RUTA = "/api/v5/jev-dispatch"
RUTA_LOTE = "/api/v5/jev-dispatch/lote"
DATOS = {"activo_id": "RWA-EC-0001", "valor": 1250.5, "cantidad": 3, "etiquetas": ["inmueble"]}


def sol(tipo="GENERACION_BINDINGS", lenguajes=("python",), datos=None, **opciones):
    return jev_cliente.construir_solicitud(CLAVE_JEV, tipo, list(lenguajes), dict(datos or DATOS), **opciones)


@pytest.fixture
def servicios(uasi, monkeypatch):
    """Clave JEV + estado de automatización limpio en cada test."""
    monkeypatch.setenv("JEV_HMAC_KEY", CLAVE_JEV)
    for var in ("JEV_VENTANA_SEGUNDOS", "JEV_LOTE_MAX", "JEV_CALLBACK_HOSTS", "JEV_CALLBACK_REINTENTOS"):
        monkeypatch.delenv(var, raising=False)
    mod = importlib.import_module("uasi_v5.servicios")
    mod.cache_jev.limpiar()
    mod.metricas_jev.reiniciar()
    mod.idempotencia_jev.limpiar()
    return mod


# --- Anti-replay e idempotencia ------------------------------------------------
def test_timestamp_fuera_de_ventana_401(cliente, cabeceras, servicios, monkeypatch):
    monkeypatch.setenv("JEV_VENTANA_SEGUNDOS", "60")
    cuerpo = sol(incluir_timestamp=False)
    cuerpo.pop("firma_hmac")
    cuerpo["timestamp"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    cuerpo["firma_hmac"] = jev_cliente.firmar(cuerpo, CLAVE_JEV)
    r = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    assert r.status_code == 401
    assert "ventana" in r.json()["detail"]
    assert servicios.metricas_jev.instantanea()["rechazos"]["fuera_de_ventana"] == 1


def test_timestamp_actual_aceptado(cliente, cabeceras, servicios):
    r = cliente.post(RUTA, json=sol(), headers=cabeceras)
    assert r.status_code == 200, r.text
    assert r.headers["X-JEV-Idempotente"] == "false"


def test_reintento_identico_es_idempotente(cliente, cabeceras, servicios):
    cuerpo = sol(id_solicitud="idem-001")
    a = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    b = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    assert a.status_code == b.status_code == 200
    assert b.headers["X-JEV-Idempotente"] == "true"
    assert a.json() == b.json()  # misma respuesta firmada, mismo bloque de ledger
    assert servicios.metricas_jev.instantanea()["respuestas_idempotentes"] == 1


def test_mismo_id_cuerpo_distinto_409(cliente, cabeceras, servicios):
    assert cliente.post(RUTA, json=sol(id_solicitud="idem-002"), headers=cabeceras).status_code == 200
    otro = sol(id_solicitud="idem-002", datos={"activo_id": "OTRO"})
    r = cliente.post(RUTA, json=otro, headers=cabeceras)
    assert r.status_code == 409
    assert servicios.metricas_jev.instantanea()["rechazos"]["replay_conflicto"] == 1


def test_firma_invalida_cuenta_rechazo(cliente, cabeceras, servicios):
    cuerpo = sol()
    cuerpo["datos"]["valor"] = 1
    assert cliente.post(RUTA, json=cuerpo, headers=cabeceras).status_code == 401
    assert servicios.metricas_jev.instantanea()["rechazos"]["firma_invalida"] == 1


# --- Caché determinista ---------------------------------------------------------
def test_cache_reutiliza_resultado(cliente, cabeceras, servicios):
    a = cliente.post(RUTA, json=sol(lenguajes=["rust", "go"]), headers=cabeceras).json()
    b = cliente.post(RUTA, json=sol(lenguajes=["rust", "go"]), headers=cabeceras).json()
    for lang in ("rust", "go"):
        assert a["resultados_por_lenguaje"][lang]["desde_cache"] is False
        assert b["resultados_por_lenguaje"][lang]["desde_cache"] is True
        assert (
            a["resultados_por_lenguaje"][lang]["hash_resultado"] == b["resultados_por_lenguaje"][lang]["hash_resultado"]
        )
    assert servicios.cache_jev.estado()["aciertos"] == 2


def test_cache_no_aplica_a_tareas_en_vivo(cliente, cabeceras, servicios):
    for _ in range(2):
        r = cliente.post(RUTA, json=sol("AUDITORIA_LEDGER"), headers=cabeceras).json()
        assert r["resultados_por_lenguaje"]["python"]["desde_cache"] is False
    assert servicios.cache_jev.estado()["entradas"] == 0


def test_cache_lru_y_ttl(servicios):
    auto = importlib.import_module("uasi_v5.agentes_jev.automatizacion")
    esquemas = importlib.import_module("uasi_v5.esquemas.jev")
    c = auto.CacheResultadosJEV(max_entradas=2, ttl_segundos=60)
    for i in range(3):
        c.guardar(f"k{i}", esquemas.ResultadoAgenteJEV(lenguaje="go", estado="exito", hash_resultado=str(i)))
    assert c.obtener("k0") is None and c.obtener("k2").hash_resultado == "2"
    c.guardar("err", esquemas.ResultadoAgenteJEV(lenguaje="go", estado="error"))
    assert c.obtener("err") is None  # los errores no se cachean
    assert auto.CacheResultadosJEV(max_entradas=0).activa is False


# --- Lotes ------------------------------------------------------------------------
def test_lote_mixto(cliente, cabeceras, servicios):
    manipulada = sol(lenguajes=["java"])
    manipulada["datos"]["valor"] = 0
    lote = {"solicitudes": [sol(lenguajes=["typescript"]), manipulada, {"id_jev": "x"}]}
    r = cliente.post(RUTA_LOTE, json=lote, headers=cabeceras)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert (cuerpo["total"], cuerpo["exitosas"], cuerpo["fallidas"]) == (3, 1, 2)
    assert [e["codigo_http"] for e in cuerpo["resultados"]] == [200, 401, 422]
    assert jev_cliente.verificar(cuerpo, CLAVE_JEV)
    assert jev_cliente.verificar(cuerpo["resultados"][0]["respuesta"], CLAVE_JEV)


def test_lote_excedido_422(cliente, cabeceras, servicios, monkeypatch):
    monkeypatch.setenv("JEV_LOTE_MAX", "2")
    r = cliente.post(RUTA_LOTE, json={"solicitudes": [sol(), sol(), sol()]}, headers=cabeceras)
    assert r.status_code == 422


def test_lote_sin_clave_503(cliente, cabeceras, servicios, monkeypatch):
    lote = {"solicitudes": [sol()]}
    monkeypatch.delenv("JEV_HMAC_KEY")
    assert cliente.post(RUTA_LOTE, json=lote, headers=cabeceras).status_code == 503


# --- Métricas ---------------------------------------------------------------------
def test_metricas(cliente, cabeceras, servicios):
    cliente.post(RUTA, json=sol(lenguajes=["python", "solidity"]), headers=cabeceras)
    cliente.post(RUTA, json=sol(lenguajes=["python"]), headers=cabeceras)
    m = cliente.get("/api/v5/jev/metricas", headers=cabeceras).json()
    assert m["por_lenguaje"]["python"]["ejecuciones"] == 2
    assert m["por_lenguaje"]["python"]["aciertos_cache"] == 1
    assert m["por_lenguaje"]["solidity"]["exitos"] == 1
    assert m["por_tipo_tarea"]["GENERACION_BINDINGS"]["despachos"] == 2
    assert m["cache"]["aciertos"] == 1
    assert cliente.get("/api/v5/jev/metricas").status_code in (401, 403)


# --- Callbacks ---------------------------------------------------------------------
@pytest.fixture
def transporte_falso(monkeypatch):
    llamadas: list[tuple] = []
    cb = importlib.import_module("uasi_v5.agentes_jev.callback")

    def falso(url, contenido, cabeceras, timeout):
        llamadas.append((url, contenido, cabeceras))
        return 200

    monkeypatch.setattr(cb, "transporte_httpx", falso)
    return llamadas


def test_callback_host_no_permitido_no_se_envia(cliente, cabeceras, servicios, transporte_falso, monkeypatch):
    monkeypatch.setenv("JEV_CALLBACK_HOSTS", "jev.nesges.example")
    cuerpo = sol(metadatos={"url_callback": "https://atacante.example/hook"})
    r = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    assert r.status_code == 200
    assert r.headers["X-JEV-Callback"].startswith("rechazado")
    assert transporte_falso == []
    assert servicios.metricas_jev.instantanea()["callbacks"]["bloqueados"] == 1


def test_callback_permitido_se_entrega_y_registra(cliente, cabeceras, servicios, transporte_falso, monkeypatch):
    monkeypatch.setenv("JEV_CALLBACK_HOSTS", "jev.nesges.example")
    cuerpo = sol(metadatos={"url_callback": "https://jev.nesges.example/uasi/respuestas"})
    r = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    assert r.status_code == 200 and r.headers["X-JEV-Callback"] == "programado"
    assert len(transporte_falso) == 1
    url, contenido, cab = transporte_falso[0]
    assert url == "https://jev.nesges.example/uasi/respuestas"
    assert contenido == r.json() and cab["X-JEV-Firma"] == contenido["firma_respuesta"]
    assert servicios.metricas_jev.instantanea()["callbacks"]["enviados"] == 1
    ultimo = servicios.ledger.verificar_integridad()
    assert ultimo["integro"] is True


def test_callback_reintentos_y_validacion_url(servicios, monkeypatch):
    cb = importlib.import_module("uasi_v5.agentes_jev.callback")
    monkeypatch.setenv("JEV_CALLBACK_HOSTS", "jev.example,localhost")
    assert cb.validar_url_callback("http://jev.example/x")[0] is False  # http solo en localhost
    assert cb.validar_url_callback("http://localhost:9000/x")[0] is True
    assert cb.validar_url_callback("https://user:pw@jev.example/x")[0] is False
    assert cb.validar_url_callback("https://jev.example.evil.com/x")[0] is False
    monkeypatch.setenv("JEV_CALLBACK_HOSTS", "")
    assert cb.validar_url_callback("https://jev.example/x")[0] is False

    codigos = iter([503, 500, 200])
    esperas: list[float] = []
    informe = cb.enviar_callback(
        "https://jev.example/x", {"firma_respuesta": "f"}, transporte=lambda *a: next(codigos), esperar=esperas.append
    )
    assert informe["entregado"] is True and informe["intentos"] == 3 and esperas == [0.5, 1.0]
    informe = cb.enviar_callback("https://jev.example/x", {}, transporte=lambda *a: 400, esperar=esperas.append)
    assert informe["entregado"] is False and informe["intentos"] == 1  # 4xx no se reintenta


# --- SDK cliente -------------------------------------------------------------------
def test_sdk_firma_coincide_con_servidor(servicios):
    firma_srv = importlib.import_module("uasi_v5.seguridad.jev_firma")
    cuerpo = sol(datos={"ñandú": "ü", "x": [1, 2.5, None], "peque": 1e-05})
    assert cuerpo["firma_hmac"] == firma_srv.firmar_jev(cuerpo)
    firma_srv.verificar_firma_jev(cuerpo)


def test_sdk_verifica_respuesta_real(cliente, cabeceras, servicios):
    r = cliente.post(RUTA, json=sol(lenguajes=["javascript"]), headers=cabeceras)
    assert jev_cliente.verificar(r.json(), CLAVE_JEV)
    manipulada = dict(r.json(), estado="error")
    assert not jev_cliente.verificar(manipulada, CLAVE_JEV)
