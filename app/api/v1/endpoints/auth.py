"""Auth endpoints — demo: `@require_auth` annotation (default OFF).

- /auth/token   : public — issue a JWT for demo purposes
- /auth/public  : public — no annotation, never protected
- /auth/me      : PROTECTED by `@require_auth`, even with global auth off
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.auth import AuthUser, require_auth
from app.core.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/token", response_model=TokenOut)
async def issue_token(payload: LoginIn) -> TokenOut:
    """Demo only — replace with real credential check against a datasource."""
    # TODO: verify_password(payload.password, stored_hash)
    token = create_access_token({"username": payload.username})
    return TokenOut(access_token=token)


@router.get("/public")
async def public_endpoint() -> dict[str, str]:
    """Open by default — no @require_auth annotation."""
    return {"message": "no auth needed here"}


@router.get("/me")
@require_auth
async def me(current_user: AuthUser) -> dict[str, object]:
    """Protected by annotation. Returns 401 without a valid bearer token,
    even when the global `auth.enabled` flag is false."""
    return {"user": current_user}
