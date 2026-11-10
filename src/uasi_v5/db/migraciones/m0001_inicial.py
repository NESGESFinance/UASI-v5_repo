"""
Migración 0001: creación inicial de tablas y salvaguardas append-only del ledger.

Tablas: ledger_entries, agent_metrics, revoked_tokens, admin_sessions.
Además de los listeners ORM, se crean triggers en la propia base de datos que abortan
cualquier UPDATE/DELETE sobre ledger_entries (SQLite y PostgreSQL), de modo que ni
siquiera SQL directo desde la aplicación puede reescribir el historial.

Uso manual:  python -m uasi_v5.db.migraciones.m0001_inicial
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from uasi_v5.db.modelos import Base

_TRIGGERS_SQLITE = (
    """CREATE TRIGGER IF NOT EXISTS ledger_sin_update BEFORE UPDATE ON ledger_entries
       BEGIN SELECT RAISE(ABORT, 'ledger_entries es append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS ledger_sin_delete BEFORE DELETE ON ledger_entries
       BEGIN SELECT RAISE(ABORT, 'ledger_entries es append-only'); END""",
)

_TRIGGERS_POSTGRES = (
    """CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'ledger_entries es append-only'; END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS ledger_sin_modificar ON ledger_entries",
    """CREATE TRIGGER ledger_sin_modificar BEFORE UPDATE OR DELETE ON ledger_entries
       FOR EACH ROW EXECUTE FUNCTION ledger_append_only()""",
)


def aplicar(motor: Engine) -> None:
    """Aplica la migración (idempotente)."""
    Base.metadata.create_all(motor)
    sentencias = {"sqlite": _TRIGGERS_SQLITE, "postgresql": _TRIGGERS_POSTGRES}.get(motor.dialect.name, ())
    with motor.begin() as conexion:
        for sql in sentencias:
            conexion.execute(text(sql))


if __name__ == "__main__":  # pragma: no cover
    from uasi_v5.db.sesion import motor as motor_configurado

    aplicar(motor_configurado)
    print(f"Migración 0001 aplicada sobre {motor_configurado.url.render_as_string(hide_password=True)}")
