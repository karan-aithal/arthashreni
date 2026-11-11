"""Local single-user authentication (ADR-0012).

Argon2id password hash; random session token in an HttpOnly, SameSite=Strict
cookie; only its SHA-256 is stored. State-changing requests also need the
session's CSRF token in the X-CSRF-Token header.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from arthashreni.core.config import get_settings
from arthashreni.db.models import AuthSession, LocalUser
from arthashreni.db.session import session_scope

COOKIE = "artha_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_hasher = PasswordHasher()  # argon2-cffi defaults to Argon2id

router = APIRouter(prefix="/auth", tags=["auth"])


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginIn(BaseModel):
    username: str
    password: str


class SessionOut(BaseModel):
    username: str
    csrf_token: str
    expires_at: datetime


def current_session(
    request: Request,
    artha_session: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> AuthSession:
    if not artha_session:
        raise HTTPException(401, "not logged in")
    with session_scope() as db:
        row = db.get(AuthSession, _token_hash(artha_session))
    if row is None or row.expires_at <= datetime.now(UTC):
        raise HTTPException(401, "session expired")
    if request.method not in SAFE_METHODS and not (
        x_csrf_token and hmac.compare_digest(x_csrf_token, row.csrf_token)
    ):
        raise HTTPException(403, "missing or wrong CSRF token")
    return row


RequireSession = Annotated[AuthSession, Depends(current_session)]


@router.post("/login")
def login(body: LoginIn, response: Response) -> SessionOut:
    with session_scope() as db:
        user = db.scalar(select(LocalUser))
        ok = False
        if user is not None and hmac.compare_digest(user.username, body.username):
            try:
                ok = _hasher.verify(user.password_hash, body.password)
            except VerificationError:
                ok = False
        if not ok or user is None:
            raise HTTPException(401, "wrong username or password")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(hours=get_settings().session_hours)
        csrf = secrets.token_urlsafe(32)
        db.execute(delete(AuthSession).where(AuthSession.expires_at <= datetime.now(UTC)))
        db.add(AuthSession(token_hash=_token_hash(token), csrf_token=csrf, expires_at=expires))
    response.set_cookie(
        COOKIE, token, httponly=True, samesite="strict", max_age=get_settings().session_hours * 3600
    )
    return SessionOut(username=user.username, csrf_token=csrf, expires_at=expires)


@router.post("/logout", status_code=204)
def logout(session: RequireSession, response: Response) -> None:
    with session_scope() as db:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == session.token_hash))
    response.delete_cookie(COOKIE)


@router.get("/me")
def me(session: RequireSession) -> SessionOut:
    with session_scope() as db:
        user = db.scalar(select(LocalUser))
    assert user is not None
    return SessionOut(username=user.username, csrf_token=session.csrf_token, expires_at=session.expires_at)
