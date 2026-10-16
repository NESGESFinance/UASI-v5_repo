"""Tests de la capa JEV: JEV → UASI (dispatcher multiagente) → JEV con firma HMAC-SHA256."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json

import pytest

CLAVE_JEV = "clave-jev-de-test-0123456789abcdef0123456789"
LENGUAJES = ["python", "typescript", "javascript", "solidity", "rust", "go", "java"]
RUTA = "/api/v5/jev-dispatch"


def firmar_como_jev(cuerpo: dict, clave: str = CLAVE_JEV) -> str:
    """Implementación independiente (lado JEV) del protocolo de firma."""
    sin_firma = {k: v for k, v in cuerpo.items() if k not in ("firma_hmac", "firma_respuesta")}
    canonico = json.dumps(sin_firma, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hmac.new(clave.encode(), canonico.encode(), hashlib.sha256).hexdigest()


def solicitud(tipo: str = "GENERACION_BINDINGS", lenguajes: list[str] | None = None, **extra) -> dict:
    cuerpo = {
        "id_jev": "jev-test-001",
        "version_jev": "1.0",
        "tipo_tarea": tipo,
        "lenguajes_destino": lenguajes or LENGUAJES,
        "datos": {
            "activo_id": "RWA-EC-0001",
            "valor": 1250.5,
            "cantidad": 3,
            "activo": True,
            "etiquetas": ["inmueble", "quito"],
            "precios": [1.5, 2, 3.25],
            "detalle": {"pais": "EC"},
            "nota": None,
        },
        "metadatos": {"origen": "jev-test", "correlacion": "abc-123"},
        **extra,
    }
    cuerpo["firma_hmac"] = firmar_como_jev(cuerpo)
    return cuerpo


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setenv("JEV_HMAC_KEY", CLAVE_JEV)


@pytest.fixture
def jev_firma(uasi):
    """Módulo de firma importado DESPUÉS de que `uasi` reimporte el paquete."""
    return importlib.import_module("uasi_v5.seguridad.jev_firma")


def test_dispatch_firma_valida_todos_los_lenguajes(cliente, cabeceras, con_clave):
    r = cliente.post(RUTA, json=solicitud(), headers=cabeceras)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "exito"
    assert cuerpo["id_jev"] == "jev-test-001"
    assert cuerpo["lenguajes_ejecutados"] == LENGUAJES
    assert set(cuerpo["resultados_por_lenguaje"]) == set(LENGUAJES)
    for lang, res in cuerpo["resultados_por_lenguaje"].items():
        assert res["estado"] == "exito", res
        datos = res["datos_resultado"]
        assert datos["lenguaje"] == lang
        assert datos["inferencia_tipos"]["valor"]["tipo_abstracto"] == "float"
        assert datos["inferencia_tipos"]["precios"]["tipo_abstracto"] == "list:float"
        assert "JevGeneracionBindings" in datos["codigo_binding"]
        assert len(res["hash_resultado"]) == 64
    codigo = {lang: res["datos_resultado"]["codigo_binding"] for lang, res in cuerpo["resultados_por_lenguaje"].items()}
    assert "export interface JevGeneracionBindings" in codigo["typescript"]
    assert "struct JevGeneracionBindings" in codigo["solidity"]
    assert "pub struct JevGeneracionBindings" in codigo["rust"]
    assert "type JevGeneracionBindings struct" in codigo["go"]
    assert "public final class JevGeneracionBindings" in codigo["java"]
    assert "@dataclass(frozen=True)" in codigo["python"]
    assert "@typedef {Object} JevGeneracionBindings" in codigo["javascript"]
    assert isinstance(cuerpo["bloque_ledger"]["indice"], int)
    assert len(cuerpo["bloque_ledger"]["hash_bloque"]) == 64


def test_resultado_determinista(cliente, cabeceras, con_clave):
    a = cliente.post(RUTA, json=solicitud(lenguajes=["rust"]), headers=cabeceras).json()
    b = cliente.post(RUTA, json=solicitud(lenguajes=["rust"]), headers=cabeceras).json()
    assert (
        a["resultados_por_lenguaje"]["rust"]["hash_resultado"] == b["resultados_por_lenguaje"]["rust"]["hash_resultado"]
    )


def test_firma_respuesta_verificable(cliente, cabeceras, con_clave):
    r = cliente.post(RUTA, json=solicitud(lenguajes=["go"]), headers=cabeceras)
    assert r.status_code == 200
    cuerpo = r.json()
    assert hmac.compare_digest(cuerpo["firma_respuesta"], firmar_como_jev(cuerpo))


def test_auditoria_ledger_inyecta_integridad(cliente, cabeceras, con_clave):
    r = cliente.post(RUTA, json=solicitud("AUDITORIA_LEDGER", ["python"]), headers=cabeceras)
    assert r.status_code == 200
    resumen = r.json()["resultados_por_lenguaje"]["python"]["datos_resultado"]["resumen"]
    assert resumen["integridad_ledger"]["integro"] is True
    assert resumen["integridad_ledger"]["bloques"] >= 1


def test_evaluacion_modelo_inyecta_salud(cliente, cabeceras, con_clave):
    r = cliente.post(RUTA, json=solicitud("EVALUACION_MODELO", ["java"]), headers=cabeceras)
    assert r.status_code == 200
    resumen = r.json()["resultados_por_lenguaje"]["java"]["datos_resultado"]["resumen"]
    assert resumen["salud_uasi"]["estado"] in ("ok", "degradado", "critico")


def test_firma_invalida_401(cliente, cabeceras, con_clave):
    cuerpo = solicitud()
    cuerpo["datos"]["valor"] = 999999  # manipulado tras firmar
    r = cliente.post(RUTA, json=cuerpo, headers=cabeceras)
    assert r.status_code == 401


def test_sin_clave_503(cliente, cabeceras, monkeypatch):
    monkeypatch.delenv("JEV_HMAC_KEY", raising=False)
    r = cliente.post(RUTA, json=solicitud(), headers=cabeceras)
    assert r.status_code == 503


def test_lenguaje_desconocido_422(cliente, cabeceras, con_clave):
    r = cliente.post(RUTA, json=solicitud(lenguajes=["cobol"]), headers=cabeceras)
    assert r.status_code == 422


def test_sin_jwt_rechazado(cliente, con_clave):
    r = cliente.post(RUTA, json=solicitud())
    assert r.status_code in (401, 403)


def test_get_agentes(cliente, cabeceras, con_clave):
    r = cliente.get("/api/v5/jev/agentes", headers=cabeceras)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["lenguajes"] == LENGUAJES
    assert "GENERACION_BINDINGS" in cuerpo["tipos_tarea"]
    assert cuerpo["jev_configurado"] is True


def test_modulo_firma_unitario(jev_firma, con_clave):
    cuerpo = {"b": 1, "a": "ñ", "firma_hmac": "x" * 64}
    firma = jev_firma.firmar_jev(cuerpo)
    assert firma == firmar_como_jev(cuerpo)
    jev_firma.verificar_firma_jev(cuerpo, firma)
    with pytest.raises(Exception) as exc:
        jev_firma.verificar_firma_jev(cuerpo, "0" * 64)
    assert exc.value.status_code == 401


def test_dispatcher_aisla_errores(uasi):
    mod = importlib.import_module("uasi_v5.agentes_jev.dispatcher")
    base = importlib.import_module("uasi_v5.agentes_jev.base")

    class AgenteRoto(base.AgenteJEVBase):
        lenguaje = "roto"
        TIPOS = {"any": "x"}

        def formato_lista(self, tipo_interno):
            return tipo_interno

        def generar_binding(self, nombre, campos):
            raise RuntimeError("fallo simulado")

    d = mod.DispatcherMultiagenteJEV(max_workers=2)
    d.registrar_agente(AgenteRoto)
    salida = d.despachar("GENERACION_BINDINGS", ["python", "roto", "kotlin"], {"x": 1})
    res = salida["resultados_por_lenguaje"]
    assert salida["estado"] == "error_parcial"
    assert res["python"].estado == "exito"
    assert res["roto"].estado == "error" and "fallo simulado" in res["roto"].mensaje_error
    assert res["kotlin"].estado == "error"
