#!/usr/bin/env python3
"""
Crea o actualiza el usuario administrador inicial de UASI (idempotente).

Lee las credenciales del entorno:
  UASI_ADMIN_USER      nombre de usuario (por defecto: "admin")
  UASI_ADMIN_PASSWORD  contraseña; si no se define, se genera una aleatoria segura
                       y se imprime UNA sola vez.

Uso:
  python -m scripts.crear_admin                 # crea el admin si no existe
  python -m scripts.crear_admin --reset-password # restablece la contraseña del admin

El usuario se crea con scopes 'uasi:full admin' (acceso a /api/v5/admin/*).
Ejecute este script con las mismas variables de entorno (DATABASE_URL, etc.) que la API.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea o actualiza el usuario administrador de UASI.")
    parser.add_argument("--reset-password", action="store_true",
                        help="Si el usuario ya existe, restablece su contraseña y lo reactiva.")
    args = parser.parse_args()

    # Imports perezosos: así el --help no requiere dependencias ni configuración.
    from sqlalchemy import select

    from uasi_v5.db.modelos import Usuario
    from uasi_v5.db.sesion import inicializar_bd, sesion_bd
    from uasi_v5.seguridad.contrasenas import hash_password

    username = os.getenv("UASI_ADMIN_USER", "admin").strip() or "admin"
    password = os.getenv("UASI_ADMIN_PASSWORD", "")
    generada = False
    if not password:
        password = secrets.token_urlsafe(18)
        generada = True
    elif len(password) < 10:
        print("ERROR: UASI_ADMIN_PASSWORD debe tener al menos 10 caracteres.", file=sys.stderr)
        return 2

    inicializar_bd()
    with sesion_bd() as s:
        usuario = s.execute(select(Usuario).where(Usuario.username == username)).scalar_one_or_none()
        if usuario is None:
            s.add(Usuario(username=username, password_hash=hash_password(password),
                          scopes="uasi:full admin", activo=1))
            accion = "creado"
        elif args.reset_password:
            usuario.password_hash = hash_password(password)
            usuario.activo = 1
            accion = "actualizado"
        else:
            print(f"El usuario '{username}' ya existe. Use --reset-password para cambiar su contraseña.")
            return 0

    print(f"Usuario administrador {accion}: {username}  (scopes: uasi:full admin)")
    if generada:
        print("Contraseña generada (guárdela ahora, NO se volverá a mostrar):")
        print(f"    {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
