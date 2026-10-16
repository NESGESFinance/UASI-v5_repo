"""Autenticación JWT fail-closed [P1-01]."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from jose import ExpiredSignatureError, JWTError, jwt

from uasi_v5.core.config import JWT_ALGORITHM, JWT_EXP_MINUTES, JWT_ISSUER, SECRET_JWT_KEY
from uasi_v5.core.utilidades import ahora_utc

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
    return jwt.encode(claims, SECRET_JWT_KEY, algorithm=JWT_ALGORITHM)


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
    cabecera_auth = {"WWW-Authenticate": "Bearer"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token Bearer requerido.", headers=cabecera_auth)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(
            token,
            SECRET_JWT_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado.", headers=cabecera_auth)
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido.", headers=cabecera_auth)
    return claims
