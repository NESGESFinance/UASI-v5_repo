"""
Gestión de usuarios administradores (/api/v5/admin/users/*).

Todas las rutas exigen un JWT con scope `admin` (dependencia `require_admin`). Permiten
listar, crear, actualizar y desactivar (borrado lógico) las cuentas que pueden iniciar
sesión contra `POST /api/v5/token`. El hash de la contraseña nunca se expone.

- GET    /api/v5/admin/users            lista de usuarios (sin password_hash)
- POST   /api/v5/admin/users            crea un usuario (409 si ya existe)
- PATCH  /api/v5/admin/users/{username} actualiza contraseña, scopes o estado activo
- DELETE /api/v5/admin/users/{username} desactiva (borrado lógico, no destruye el registro)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.utilidades import ahora_utc
from uasi_v5.db.modelos import Usuario
from uasi_v5.db.sesion import sesion_bd
from uasi_v5.seguridad.autenticacion import require_admin
from uasi_v5.seguridad.contrasenas import hash_password

router = APIRouter(prefix="/api/v5/admin/users", tags=["admin"])

_PATRON_USUARIO = r"^[A-Za-z0-9_.@\-]+$"
_PATRON_SCOPES = r"^uasi:full( admin)?$"


class CrearUsuario(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=_PATRON_USUARIO)
    password: str = Field(..., min_length=10, max_length=256)
    scopes: str = Field("uasi:full", max_length=128, pattern=_PATRON_SCOPES,
                        description="'uasi:full' o 'uasi:full admin' (acceso a /api/v5/admin/*).")


class ActualizarUsuario(BaseModel):
    password: Optional[str] = Field(None, min_length=10, max_length=256)
    scopes: Optional[str] = Field(None, max_length=128, pattern=_PATRON_SCOPES)
    activo: Optional[bool] = None


def _serializar(u: Usuario) -> Dict[str, Any]:
    return {
        "username": u.username,
        "scopes": u.scopes,
        "activo": bool(u.activo),
        "creado": u.creado.isoformat() if u.creado else None,
        "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None,
    }


def _listar() -> List[Dict[str, Any]]:
    with sesion_bd() as s:
        filas = s.execute(select(Usuario).order_by(Usuario.username)).scalars().all()
        return [_serializar(u) for u in filas]


def _crear(datos: CrearUsuario) -> Dict[str, Any]:
    with sesion_bd() as s:
        existe = s.execute(select(Usuario).where(Usuario.username == datos.username)).scalar_one_or_none()
        if existe is not None:
            raise HTTPException(status_code=409, detail=f"El usuario '{datos.username}' ya existe.")
        usuario = Usuario(username=datos.username, password_hash=hash_password(datos.password),
                          scopes=datos.scopes, activo=1)
        s.add(usuario)
        s.flush()
        return _serializar(usuario)


def _actualizar(username: str, datos: ActualizarUsuario) -> Dict[str, Any]:
    with sesion_bd() as s:
        usuario = s.execute(select(Usuario).where(Usuario.username == username)).scalar_one_or_none()
        if usuario is None:
            raise HTTPException(status_code=404, detail=f"Usuario '{username}' no encontrado.")
        if datos.password is not None:
            usuario.password_hash = hash_password(datos.password)
        if datos.scopes is not None:
            usuario.scopes = datos.scopes
        if datos.activo is not None:
            usuario.activo = 1 if datos.activo else 0
        s.flush()
        return _serializar(usuario)


def _desactivar(username: str) -> Dict[str, Any]:
    with sesion_bd() as s:
        usuario = s.execute(select(Usuario).where(Usuario.username == username)).scalar_one_or_none()
        if usuario is None:
            raise HTTPException(status_code=404, detail=f"Usuario '{username}' no encontrado.")
        usuario.activo = 0
        return _serializar(usuario)


@router.get("")
async def listar_usuarios(claims: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    usuarios = await run_in_threadpool(_listar)
    return {"timestamp": ahora_utc().isoformat(), "total": len(usuarios), "usuarios": usuarios}


@router.post("", status_code=201)
async def crear_usuario(datos: CrearUsuario, claims: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    return await run_in_threadpool(_crear, datos)


@router.patch("/{username}")
async def actualizar_usuario(
    datos: ActualizarUsuario,
    username: str = Path(..., min_length=1, max_length=64, pattern=_PATRON_USUARIO),
    claims: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    return await run_in_threadpool(_actualizar, username, datos)


@router.delete("/{username}")
async def desactivar_usuario(
    username: str = Path(..., min_length=1, max_length=64, pattern=_PATRON_USUARIO),
    claims: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    if username == str(claims.get("sub", "")):
        raise HTTPException(status_code=400, detail="No puede desactivar su propia cuenta en uso.")
    return await run_in_threadpool(_desactivar, username)
