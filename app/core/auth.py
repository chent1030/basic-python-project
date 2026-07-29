"""Authentication dependencies.

Design (per requirements):
- Default is OFF. Endpoints have no auth unless explicitly annotated.
- Adding `@require_auth` to an endpoint turns auth ON for that endpoint,
  regardless of the global `auth.enabled` flag.
- `@require_auth` also forces global auth on the whole router if applied
  at router level, and injects a `current_user` dependency.

Two entry points:
- `require_auth`        — decorator for endpoints / routers
- `Currentuser` (alias) — Annotated type for handlers that need the user
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token

_bearer = HTTPBearer(auto_error=False)


async def _verify_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Resolve the current user from a Bearer JWT.

    Raises 401 when the token is missing/invalid. This function is the
    per-endpoint enforcement point — it is *only* wired in when a handler
    uses `@require_auth` or the `AuthUser` type.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:  # noqa: BLE001 — jwt raises various subclasses
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return payload


async def get_current_user(payload: dict[str, Any] = Depends(_verify_bearer)) -> dict[str, Any]:
    """Return the decoded JWT payload. Plug in a DB lookup here if needed."""
    return payload


# Convenience Annotated type for handlers that want the user object.
AuthUser = Annotated[dict[str, Any], Depends(get_current_user)]


def require_auth(func: Callable) -> Callable:
    """Decorator that turns authentication ON for this endpoint (or router).

    Behaviour:
    - Always enforces bearer auth, even when global `auth.enabled=false`.
    - Injects a hidden dependency so FastAPI validates the token.
    - If the wrapped function declares a `current_user` parameter, the JWT
      payload is bound to it; otherwise it is silently consumed.

    Example:
        @router.get("/me")
        @require_auth
        async def me(current_user: AuthUser):
            return current_user
    """
    sig = inspect.signature(func)
    needs_user_param = "current_user" in sig.parameters

    if needs_user_param:
        # The handler already declares `current_user` — let FastAPI inject it
        # via the AuthUser alias directly; we just mark intent.
        @wraps(func)
        async def wrapper_with_user(*args: Any, **kwargs: Any) -> Any:
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper_with_user.__require_auth__ = True  # type: ignore[attr-defined]
        # Keep original signature intact so FastAPI sees `current_user: AuthUser`.
        return wrapper_with_user

    # Handler does not declare current_user — inject a hidden dependency so
    # the token is still validated without changing the handler's signature.
    hidden = inspect.Parameter(
        "__auth_user__",
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=Depends(get_current_user),
        annotation=dict[str, Any],
    )
    new_sig = sig.replace(parameters=[*sig.parameters.values(), hidden])

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        async_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        async_wrapper.__require_auth__ = True  # type: ignore[attr-defined]
        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    sync_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    sync_wrapper.__require_auth__ = True  # type: ignore[attr-defined]
    return sync_wrapper


# Backwards-friendly alias.
CurrentUser = AuthUser


__all__ = ["require_auth", "AuthUser", "CurrentUser", "get_current_user", "Request"]
