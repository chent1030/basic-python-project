"""Security & JWT helpers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)


def create_access_token(subject: str | dict[str, Any]) -> str:
    """`subject` may be a username string or a dict of claims.

    A dict is flattened into the JWT payload (RFC 7519 requires `sub` to be a
    string), with `sub` set to the dict's `username`/`sub` key if present.
    """
    payload: dict[str, Any] = {"type": "access"}
    if isinstance(subject, dict):
        payload.update(subject)
        if "sub" not in payload and "username" in payload:
            payload["sub"] = str(payload["username"])
    else:
        payload["sub"] = str(subject)
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.auth.access_token_expire_minutes
    )
    payload["exp"] = expire
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def create_refresh_token(subject: str | dict[str, Any]) -> str:
    payload: dict[str, Any] = {"type": "refresh"}
    if isinstance(subject, dict):
        payload.update(subject)
        if "sub" not in payload and "username" in payload:
            payload["sub"] = str(payload["username"])
    else:
        payload["sub"] = str(subject)
    expire = datetime.now(UTC) + timedelta(
        days=settings.auth.refresh_token_expire_days
    )
    payload["exp"] = expire
    return jwt.encode(payload, settings.auth.secret_key, algorithm=settings.auth.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    # `options={"verify_sub": False}` — we may store non-string subjects in
    # other claims; keep decode lenient on the sub type.
    return jwt.decode(
        token,
        settings.auth.secret_key,
        algorithms=[settings.auth.algorithm],
        options={"verify_sub": False},
    )
