"""Pruebas de la integración de observación con NESGESFinanceTrust (/api/v5/trust).

Se inyecta un ``httpx.MockTransport`` para simular la plataforma externa sin red real,
verificando tanto el camino conectado como la degradación elegante cuando no responde.
"""

from __future__ import annotations

import importlib

import httpx
import pytest

# El hook de import histórico del conftest reimporta TODO el árbol ``uasi_v5.*`` al
# construir el fixture ``uasi``; por eso referenciamos el módulo (y sus símbolos)
# de forma perezosa dentro de los tests, para que coincidan con los que usa la app.
MODULO = "uasi_v5.integraciones.nesgesfinancetrust"


def _handler_ok(request: httpx.Request) -> httpx.Response:
    ruta = request.url.path
    if ruta.endswith("/health"):
        return httpx.Response(200, json={"status": "ok", "version": "3.4-dev"})
    if "/runes/" in ruta:
        return httpx.Response(200, json={"name": "NGF•BTC•AM", "id": "923867:120", "supply": "5930000000"})
    if ruta.endswith("/runes"):
        return httpx.Response(200, json=[{"name": "NGF•BTC•AM"}])
    if "/rwa/assets" in ruta:
        return httpx.Response(200, json={"assets": [
            {"class": "UTILITY"}, {"class": "SECURITY"}, {"class": "UTILITY"},
        ]})
    return httpx.Response(404, json={})


def _handler_caido(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("plataforma no disponible")


@pytest.fixture
def _override(uasi):
    """Permite sustituir el cliente trust por uno con transporte simulado."""
    mod = importlib.import_module(MODULO)

    def _aplicar(handler):
        transporte = httpx.MockTransport(handler)
        uasi.app.dependency_overrides[mod.obtener_cliente_trust] = lambda: mod.ClienteNESGESFinanceTrust(
            base_url="http://trust.test/api", transport=transporte
        )
    yield _aplicar
    uasi.app.dependency_overrides.pop(mod.obtener_cliente_trust, None)


def test_estado_requiere_autenticacion(cliente):
    assert cliente.get("/api/v5/trust/estado").status_code == 401


def test_estado_conectado(cliente, cabeceras, _override):
    _override(_handler_ok)
    r = cliente.get("/api/v5/trust/estado", headers=cabeceras)
    assert r.status_code == 200
    datos = r.json()
    assert datos["conectado"] is True
    assert datos["runa_principal"]["name"] == "NGF•BTC•AM"
    assert datos["activos_rwa"]["total"] == 3
    assert datos["activos_rwa"]["por_clase"]["UTILITY"] == 2
    assert len(datos["cumplimiento_mica"]) == 4
    assert datos["errores"] == []


def test_estado_degrada_sin_fallar(cliente, cabeceras, _override):
    _override(_handler_caido)
    r = cliente.get("/api/v5/trust/estado", headers=cabeceras)
    assert r.status_code == 200  # nunca 5xx
    datos = r.json()
    assert datos["conectado"] is False
    assert datos["activos_rwa"] is None
    assert datos["errores"]  # contiene el detalle de los fallos


def test_runes_listado(cliente, cabeceras, _override):
    _override(_handler_ok)
    r = cliente.get("/api/v5/trust/runes?limite=5", headers=cabeceras)
    assert r.status_code == 200
    assert r.json()["conectado"] is True


def test_rwa_listado(cliente, cabeceras, _override):
    _override(_handler_ok)
    r = cliente.get("/api/v5/trust/rwa", headers=cabeceras)
    assert r.status_code == 200
    assert r.json()["conectado"] is True


def test_resumen_rwa_estatico(uasi):
    mod = importlib.import_module(MODULO)
    c = mod.ClienteNESGESFinanceTrust()
    resumen = c._resumen_rwa({"assets": [{"clase": "EMT"}, {"class": "EMT"}, {"tipo": "ART"}]})
    assert resumen["total"] == 3
    assert resumen["por_clase"]["EMT"] == 2
    assert resumen["por_clase"]["ART"] == 1


def test_runes_valida_limite(cliente, cabeceras, _override):
    _override(_handler_ok)
    assert cliente.get("/api/v5/trust/runes?limite=0", headers=cabeceras).status_code == 422
    assert cliente.get("/api/v5/trust/runes?limite=999", headers=cabeceras).status_code == 422
