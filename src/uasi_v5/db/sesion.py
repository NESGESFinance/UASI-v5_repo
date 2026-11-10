"""Motor y sesiones SQLAlchemy. DATABASE_URL decide el backend (SQLite por defecto)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from uasi_v5.core.config import DATABASE_URL


def crear_motor(url: str = DATABASE_URL) -> Engine:
    """Crea el motor. En SQLite: hilos compartidos, WAL y espera ante bloqueos (multi-worker)."""
    if url.startswith("sqlite"):
        ruta = url.split("///", 1)[-1]
        if ruta and ruta != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

        @event.listens_for(eng, "connect")
        def _pragmas(conexion: Any, _registro: Any) -> None:
            cur = conexion.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng
    # PostgreSQL (u otro): pool con verificación previa de conexiones.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10, future=True)


motor: Engine = crear_motor()
FabricaSesiones = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False, future=True)
_inicializada = False


def inicializar_bd() -> None:
    """Crea tablas y salvaguardas append-only (idempotente)."""
    global _inicializada
    if _inicializada:
        return
    from uasi_v5.db.migraciones.m0001_inicial import aplicar

    aplicar(motor)
    _inicializada = True


@contextmanager
def sesion_bd() -> Iterator[Session]:
    """Sesión transaccional para uso fuera de FastAPI (commit al salir, rollback si hay error)."""
    inicializar_bd()
    sesion = FabricaSesiones()
    try:
        yield sesion
        sesion.commit()
    except BaseException:
        sesion.rollback()
        raise
    finally:
        sesion.close()


def get_db() -> Iterator[Session]:
    """Dependencia FastAPI: `db: Session = Depends(get_db)`."""
    with sesion_bd() as sesion:
        yield sesion


def comprobar_bd() -> dict[str, Any]:
    """Comprobación de salud: SELECT 1 y dialecto."""
    import time

    inicio = time.perf_counter()
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SELECT 1"))
        return {"estado": "ok", "dialecto": motor.dialect.name,
                "latencia_ms": round((time.perf_counter() - inicio) * 1000, 3)}
    except Exception as exc:  # noqa: BLE001 - se informa del fallo, no se propaga
        return {"estado": "error", "dialecto": motor.dialect.name, "error": type(exc).__name__}
