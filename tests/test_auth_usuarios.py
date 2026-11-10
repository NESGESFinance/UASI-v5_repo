"""Login real por usuario/contraseña (POST /api/v5/token) y gestión de usuarios admin."""

from __future__ import annotations

import os
import sys

import pytest

# El paquete `scripts/` vive en la raíz del repositorio (fuera de src/); lo hacemos importable.
_RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ_REPO not in sys.path:
    sys.path.insert(0, _RAIZ_REPO)


@pytest.fixture(scope="module")
def cab_admin(uasi):
    return {"Authorization": f"Bearer {uasi.emitir_token('admin-usuarios', alcance='uasi:full admin')}"}


# --- Hash de contraseñas --------------------------------------------------------------------
def test_hash_y_verifica(uasi):
    from uasi_v5.seguridad.contrasenas import hash_password, verificar_password

    h = hash_password("Clave-Muy-Segura-123")
    assert h != "Clave-Muy-Segura-123" and h.startswith("$2")
    assert verificar_password("Clave-Muy-Segura-123", h) is True
    assert verificar_password("otra", h) is False
    assert verificar_password("x", "") is False


def test_hash_contrasena_larga_sin_truncar(uasi):
    """bcrypt trunca a 72 bytes; el pre-hash SHA-256 evita colisiones por truncamiento."""
    from uasi_v5.seguridad.contrasenas import hash_password, verificar_password

    base = "A" * 72
    h = hash_password(base + "sufijo-distinto-1")
    assert verificar_password(base + "sufijo-distinto-2", h) is False
    assert verificar_password(base + "sufijo-distinto-1", h) is True


# --- Login POST /api/v5/token ---------------------------------------------------------------
def test_login_crea_usuario_y_autentica(cliente, cab_admin):
    alta = cliente.post("/api/v5/admin/users",
                        json={"username": "operador1", "password": "ClaveOperador-2026"}, headers=cab_admin)
    assert alta.status_code == 201, alta.text
    assert alta.json()["username"] == "operador1" and "password_hash" not in alta.json()

    ok = cliente.post("/api/v5/token", json={"username": "operador1", "password": "ClaveOperador-2026"})
    assert ok.status_code == 200, ok.text
    cuerpo = ok.json()
    assert cuerpo["token_type"] == "bearer" and cuerpo["scope"] == "uasi:full"
    assert cuerpo["access_token"] and cuerpo["expires_in"] > 0

    # El token emitido sirve para un endpoint protegido estándar.
    cab = {"Authorization": f"Bearer {cuerpo['access_token']}"}
    assert cliente.get("/api/v5/cryptographic-ledger", headers=cab).status_code == 200


def test_login_credenciales_invalidas_401(cliente, cab_admin):
    cliente.post("/api/v5/admin/users",
                 json={"username": "operador2", "password": "ClaveOperador-2026"}, headers=cab_admin)
    assert cliente.post("/api/v5/token",
                        json={"username": "operador2", "password": "incorrecta"}).status_code == 401
    assert cliente.post("/api/v5/token",
                        json={"username": "inexistente", "password": "loquesea12"}).status_code == 401


def test_login_usuario_admin_obtiene_scope_admin(cliente, cab_admin):
    cliente.post("/api/v5/admin/users",
                 json={"username": "jefe", "password": "ClaveJefe-2026-xyz", "scopes": "uasi:full admin"},
                 headers=cab_admin)
    r = cliente.post("/api/v5/token", json={"username": "jefe", "password": "ClaveJefe-2026-xyz"})
    assert r.status_code == 200 and r.json()["scope"] == "uasi:full admin"
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert cliente.get("/api/v5/admin/agents", headers=cab).status_code == 200


def test_login_usuario_desactivado_401(cliente, cab_admin):
    cliente.post("/api/v5/admin/users",
                 json={"username": "baja", "password": "ClaveBaja-2026-abc"}, headers=cab_admin)
    assert cliente.delete("/api/v5/admin/users/baja", headers=cab_admin).status_code == 200
    assert cliente.post("/api/v5/token",
                        json={"username": "baja", "password": "ClaveBaja-2026-abc"}).status_code == 401


# --- Gestión de usuarios --------------------------------------------------------------------
def test_gestion_usuarios_requiere_admin(cliente, cabeceras):
    assert cliente.get("/api/v5/admin/users").status_code == 401
    assert cliente.get("/api/v5/admin/users", headers=cabeceras).status_code == 403  # sin scope admin


def test_crear_usuario_duplicado_409(cliente, cab_admin):
    datos = {"username": "repetido", "password": "ClaveRepetida-2026"}
    assert cliente.post("/api/v5/admin/users", json=datos, headers=cab_admin).status_code == 201
    assert cliente.post("/api/v5/admin/users", json=datos, headers=cab_admin).status_code == 409


def test_crear_usuario_password_corta_422(cliente, cab_admin):
    assert cliente.post("/api/v5/admin/users",
                        json={"username": "corta", "password": "corta"}, headers=cab_admin).status_code == 422


def test_actualizar_password_y_relogin(cliente, cab_admin):
    cliente.post("/api/v5/admin/users",
                 json={"username": "cambia", "password": "ClaveVieja-2026"}, headers=cab_admin)
    r = cliente.patch("/api/v5/admin/users/cambia",
                      json={"password": "ClaveNueva-2026"}, headers=cab_admin)
    assert r.status_code == 200, r.text
    assert cliente.post("/api/v5/token",
                        json={"username": "cambia", "password": "ClaveVieja-2026"}).status_code == 401
    assert cliente.post("/api/v5/token",
                        json={"username": "cambia", "password": "ClaveNueva-2026"}).status_code == 200


def test_listar_no_expone_hash(cliente, cab_admin):
    r = cliente.get("/api/v5/admin/users", headers=cab_admin)
    assert r.status_code == 200, r.text
    assert all("password_hash" not in u and "password" not in u for u in r.json()["usuarios"])


def test_no_autodesactivarse(cliente, uasi):
    # Crea un admin real, inicia sesión y trata de borrarse a sí mismo.
    cab0 = {"Authorization": f"Bearer {uasi.emitir_token('raiz', alcance='uasi:full admin')}"}
    cliente.post("/api/v5/admin/users",
                 json={"username": "raiz", "password": "ClaveRaiz-2026-xyz", "scopes": "uasi:full admin"}, headers=cab0)
    r = cliente.post("/api/v5/token", json={"username": "raiz", "password": "ClaveRaiz-2026-xyz"})
    cab = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert cliente.delete("/api/v5/admin/users/raiz", headers=cab).status_code == 400


def test_crear_admin_script(uasi, monkeypatch):
    """El script scripts/crear_admin.py crea un admin idempotentemente."""
    import importlib

    monkeypatch.setenv("UASI_ADMIN_USER", "admin_script")
    monkeypatch.setenv("UASI_ADMIN_PASSWORD", "ClaveScript-2026")
    monkeypatch.setattr(sys, "argv", ["crear_admin"])
    crear_admin = importlib.import_module("scripts.crear_admin")
    assert crear_admin.main() == 0
    # Segunda ejecución sin --reset: idempotente (no falla).
    assert crear_admin.main() == 0

    from sqlalchemy import select

    from uasi_v5.db.modelos import Usuario
    from uasi_v5.db.sesion import sesion_bd

    with sesion_bd() as s:
        u = s.execute(select(Usuario).where(Usuario.username == "admin_script")).scalar_one_or_none()
    assert u is not None and bool(u.activo) and "admin" in u.scopes
