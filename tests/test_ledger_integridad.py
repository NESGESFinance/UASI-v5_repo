"""Ledger SQL append-only: encadenamiento SHA-256, génesis, detección de manipulación y concurrencia."""

from __future__ import annotations

import json
import sys
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def entorno_ledger(uasi, tmp_path):
    ledger_mod = sys.modules["uasi_v5.auditoria.ledger"]
    modelos = sys.modules["uasi_v5.db.modelos"]
    from uasi_v5.db.migraciones.m0001_inicial import aplicar

    motor = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}", connect_args={"check_same_thread": False})
    aplicar(motor)
    fabrica = sessionmaker(bind=motor, expire_on_commit=False)
    led = ledger_mod.LedgerInmutableSQL(fabrica)
    led.inicializar()
    return led, motor, fabrica, ledger_mod, modelos


def test_cadena_valida_y_genesis(entorno_ledger):
    led, _, _, ledger_mod, _ = entorno_ledger
    for i in range(5):
        led.registrar("EVENTO_TEST", {"i": i, "v": 0.5}, "tester")
    bloques = list(led.iterar())
    assert len(bloques) == 6
    assert bloques[0]["evento"] == "GENESIS" and bloques[0]["hash_anterior"] == "0" * 64
    for previo, actual in zip(bloques, bloques[1:], strict=False):
        assert actual["hash_anterior"] == previo["hash_actual"]
    b = bloques[3]
    esperado = ledger_mod.calcular_hash_entrada(b["hash_anterior"], b["timestamp"], b["evento"], b["actor"],
                                                json.dumps(b["datos"], sort_keys=True, separators=(",", ":")))
    assert b["hash_actual"] == esperado
    res = led.verificar_integridad()
    assert res == {"integro": True, "bloques": 6, "ultimo_hash": bloques[-1]["hash_actual"]}
    assert led.paginar(2, 4)["bloques"][0]["id"] == 4 and led.contar() == 6


def test_append_only_orm_y_trigger(entorno_ledger):
    led, motor, fabrica, _, modelos = entorno_ledger
    led.registrar("EVENTO_TEST", {"x": 1})
    with fabrica() as s:
        fila = s.get(modelos.LedgerEntry, 1)
        fila.evento = "OTRO"
        with pytest.raises(modelos.LedgerInmutableError):
            s.commit()
    with pytest.raises(Exception, match="append-only"), motor.begin() as c:
        c.execute(text("UPDATE ledger_entries SET datos = '{}' WHERE id = 1"))
    with pytest.raises(Exception, match="append-only"), motor.begin() as c:
        c.execute(text("DELETE FROM ledger_entries WHERE id = 1"))
    assert led.verificar_integridad()["integro"] is True


def _sin_triggers(motor):
    with motor.begin() as c:
        c.execute(text("DROP TRIGGER ledger_sin_update"))
        c.execute(text("DROP TRIGGER ledger_sin_delete"))


def test_modificar_una_entrada_rompe_la_cadena(entorno_ledger):
    """Aunque un atacante con acceso directo a la BD elimine los triggers, la alteración se detecta."""
    led, motor, _, _, _ = entorno_ledger
    for i in range(4):
        led.registrar("EVENTO_TEST", {"i": i, "importe": 100})
    _sin_triggers(motor)
    with motor.begin() as c:
        c.execute(text("UPDATE ledger_entries SET datos = :d WHERE id = 2"), {"d": '{"i":1,"importe":999}'})
    res = led.verificar_integridad()
    assert res["integro"] is False and "bloque 2" in res["error"]


def test_borrar_o_reencadenar_se_detecta(entorno_ledger):
    led, motor, _, _, _ = entorno_ledger
    for i in range(4):
        led.registrar("EVENTO_TEST", {"i": i})
    _sin_triggers(motor)
    with motor.begin() as c:
        c.execute(text("DELETE FROM ledger_entries WHERE id = 2"))
    res = led.verificar_integridad()
    assert res["integro"] is False and "discontinuo" in res["error"]


def test_evento_invalido_rechazado(entorno_ledger):
    led = entorno_ledger[0]
    with pytest.raises(ValueError):
        led.registrar("evento con espacios", {})


def test_escrituras_concurrentes_mantienen_la_cadena(entorno_ledger):
    led = entorno_ledger[0]
    hilos = [threading.Thread(target=lambda k=k: [led.registrar("CONCURRENTE", {"h": k, "j": j}) for j in range(5)])
             for k in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    res = led.verificar_integridad()
    assert res["integro"] is True and res["bloques"] == 21


def test_servicio_usa_backend_sql(uasi):
    servicios = sys.modules["uasi_v5.servicios"]
    assert type(servicios.ledger).__name__ == "LedgerInmutableSQL"
