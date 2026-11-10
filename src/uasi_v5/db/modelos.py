"""Modelos ORM de UASI v5 (SQLAlchemy 2.x, estilo declarativo tipado)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from uasi_v5.core.utilidades import ahora_utc


class Base(DeclarativeBase):
    """Base declarativa común."""


class LedgerEntry(Base):
    """
    Entrada del ledger de auditoría append-only.

    `id` es el índice consecutivo de la cadena (0 = génesis). La unicidad de `id` y de
    `hash_anterior` impide bifurcaciones: si dos workers intentan anexar a la vez sobre el
    mismo último bloque, solo uno puede hacerlo; el otro reintenta sobre la nueva cabeza.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("hash_anterior", name="uq_ledger_hash_anterior"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    timestamp: Mapped[str] = mapped_column(String(40), nullable=False)
    fecha: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    evento: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    datos: Mapped[str] = mapped_column(Text, nullable=False)  # JSON canónico (exactamente lo que se hashea)
    hash_anterior: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_actual: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class AgentMetric(Base):
    """Una ejecución de un agente (JEV por lenguaje o agente ML): estado y latencia."""

    __tablename__ = "agent_metrics"
    __table_args__ = (Index("ix_agent_metrics_agente_creado", "agente", "creado"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agente: Mapped[str] = mapped_column(String(64), nullable=False)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False, default="jev")
    tarea: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    estado: Mapped[str] = mapped_column(String(16), nullable=False)
    duracion_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    desde_cache: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=ahora_utc, index=True)


class RevokedToken(Base):
    """JWT revocado (por `jti`) antes de su expiración natural."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    sujeto: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    motivo: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    revocado_por: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    revocado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=ahora_utc)
    expira_en: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminSession(Base):
    """Sesión administrativa: un token con scope `admin` usado contra /api/v5/admin/*."""

    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sujeto: Mapped[str] = mapped_column(String(128), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    iniciada: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=ahora_utc)
    ultima_actividad: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=ahora_utc)
    peticiones: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Usuario(Base):
    """Cuenta que puede iniciar sesión (POST /api/v5/token) con usuario y contraseña."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # Scopes del JWT emitido al autenticarse: "uasi:full" o "uasi:full admin".
    scopes: Mapped[str] = mapped_column(String(128), nullable=False, default="uasi:full")
    # Borrado lógico: un usuario inactivo no puede autenticarse (1 = activo, 0 = inactivo).
    activo: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=ahora_utc)
    ultimo_acceso: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LedgerInmutableError(RuntimeError):
    """Intento de modificar o borrar una entrada del ledger (append-only)."""


@event.listens_for(LedgerEntry, "before_update")
def _bloquear_update(*_args: object) -> None:
    raise LedgerInmutableError("ledger_entries es append-only: UPDATE prohibido.")


@event.listens_for(LedgerEntry, "before_delete")
def _bloquear_delete(*_args: object) -> None:
    raise LedgerInmutableError("ledger_entries es append-only: DELETE prohibido.")
