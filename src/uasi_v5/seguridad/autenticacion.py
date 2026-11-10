"""Autenticación JWT fail-closed [P1-01] (PyJWT; solo algoritmos HMAC)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from uasi_v5.core.config import JWT_ALGORITHM, JWT_EXP_MINUTES, JWT_ISSUER, SECRET_JWT_KEY
from uasi_v5.core.utilidades import ahora_utc

# El secreto es simétrico: solo se aceptan algoritmos HMAC. Cualquier otro valor de
# JWT_ALGORITHM (RS*, ES*, "none"...) se rechaza (fail-closed) para impedir confusión de algoritmos.
ALGORITMOS_JWT_PERMITIDOS = frozenset({"HS256", "HS384", "HS512"})


def _algoritmo_jwt() -> str:
    if JWT_ALGORITHM not in ALGORITMOS_JWT_PERMITIDOS:
        raise RuntimeError(
            f"JWT_ALGORITHM={JWT_ALGORITHM!r} no permitido; use uno de {sorted(ALGORITMOS_JWT_PERMITIDOS)}"
        )
    return JWT_ALGORITHM


api_key_header = APIKeyHeader(
    name="Authorization",
    auto_error=False,
    description="Cabecera 'Authorization: Bearer <JWT>'",
)


def emitir_token(sujeto: str, minutos: int = JWT_EXP_MINUTES, alcance: str = "uasi:full") -> str:
    """Emite un JWT firmado (HS256 por defecto) con iss/sub/iat/exp/jti."""
    if not SECRET_JWT_KEY:
        raise RuntimeError("SECRET_JWT_KEY no configurada")
    ahora = ahora_utc()
    claims = {
        "iss": JWT_ISSUER,
        "sub": sujeto,
        "scope": alcance,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=minutos)).timestamp()),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, SECRET_JWT_KEY, algorithm=_algoritmo_jwt())


async def require_auth(authorization: Optional[str] = Security(api_key_header)) -> Dict[str, Any]:
    """
    Dependencia FastAPI (equivalente idiomático a un decorador) que valida el JWT.
    - Falla CERRADO (503) si el servidor no tiene SECRET_JWT_KEY.
    - 401 si falta la cabecera, el esquema no es Bearer, la firma es inválida,
      el emisor no coincide o el token ha expirado.
    """
    if not SECRET_JWT_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticación no configurada en el servidor (SECRET_JWT_KEY ausente).",
        )
    if JWT_ALGORITHM not in ALGORITMOS_JWT_PERMITIDOS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticación mal configurada en el servidor (JWT_ALGORITHM no permitido).",
        )
    cabecera_auth = {"WWW-Authenticate": "Bearer"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token Bearer requerido.", headers=cabecera_auth)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decodificar_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.", headers=cabecera_auth) from None
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido.", headers=cabecera_auth) from None
    if await run_in_threadpool(token_revocado, claims.get("jti")):
        raise HTTPException(status_code=401, detail="Token revocado.", headers=cabecera_auth)
    return claims


def decodificar_token(token: str) -> Dict[str, Any]:
    """Valida firma, algoritmo, emisor y claims obligatorios. Lanza jwt.PyJWTError si no es válido."""
    if not SECRET_JWT_KEY or JWT_ALGORITHM not in ALGORITMOS_JWT_PERMITIDOS:
        raise jwt.InvalidTokenError("Autenticación no configurada en el servidor.")
    claims: Dict[str, Any] = jwt.decode(
        token,
        SECRET_JWT_KEY,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )
    return claims


def token_revocado(jti: Optional[str]) -> bool:
    """Consulta la tabla revoked_tokens (sin jti no hay revocación posible)."""
    if not jti:
        return False
    from uasi_v5.db.modelos import RevokedToken
    from uasi_v5.db.sesion import sesion_bd

    with sesion_bd() as s:
        return s.get(RevokedToken, str(jti)) is not None


def revocar_token(jti: str, sujeto: str = "", motivo: str = "", revocado_por: str = "",
                  expira_en: Optional[int] = None) -> bool:
    """Revoca un token por jti. Devuelve False si ya estaba revocado."""
    from uasi_v5.db.modelos import RevokedToken
    from uasi_v5.db.sesion import sesion_bd

    with sesion_bd() as s:
        if s.get(RevokedToken, jti) is not None:
            return False
        s.add(RevokedToken(jti=jti, sujeto=sujeto[:128], motivo=motivo[:256], revocado_por=revocado_por[:128],
                           expira_en=datetime.fromtimestamp(expira_en, UTC) if expira_en else None))
    return True


def alcances(claims: Dict[str, Any]) -> set[str]:
    """Scopes del token (claim `scope` separado por espacios, RFC 8693)."""
    return set(str(claims.get("scope", "")).split())


def require_scope(alcance: str) -> Callable[..., Awaitable[Dict[str, Any]]]:
    """Fábrica de dependencias: exige un JWT válido que incluya `alcance` (403 si falta)."""

    async def _dependencia(claims: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
        if alcance not in alcances(claims):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Se requiere el scope '{alcance}'.")
        return claims

    return _dependencia


_require_scope_admin = require_scope("admin")


def _registrar_sesion_admin(claims: Dict[str, Any], ip: str) -> None:
    """Crea o actualiza la fila de admin_sessions asociada al jti del token."""
    from sqlalchemy import select

    from uasi_v5.db.modelos import AdminSession
    from uasi_v5.db.sesion import sesion_bd

    jti = str(claims.get("jti") or f"sin-jti:{claims.get('sub')}:{claims.get('iat')}")[:64]
    ahora = ahora_utc()
    with sesion_bd() as s:
        sesion = s.execute(select(AdminSession).where(AdminSession.jti == jti)).scalar_one_or_none()
        if sesion is None:
            s.add(AdminSession(jti=jti, sujeto=str(claims.get("sub", ""))[:128], ip=ip[:64], iniciada=ahora,
                               ultima_actividad=ahora, peticiones=1))
        else:
            sesion.ultima_actividad = ahora
            sesion.peticiones += 1
            sesion.ip = ip[:64]


async def require_admin(request: Request, claims: Dict[str, Any] = Depends(_require_scope_admin)) -> Dict[str, Any]:
    """JWT válido + scope `admin`; registra la actividad en admin_sessions."""
    ip = request.client.host if request.client else ""
    await run_in_threadpool(_registrar_sesion_admin, claims, ip)
    return claims
