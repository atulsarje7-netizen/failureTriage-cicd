from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthSession, User

COOKIE_NAME = "session"
CSRF_COOKIE = "csrf"
SESSION_MAX_AGE = timedelta(hours=12)
_bearer = HTTPBearer(auto_error=False)
_ITERATIONS = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    """Return a salt$hex PBKDF2 hash of the password."""
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        resolved_salt.encode(),
        _ITERATIONS,
    ).hex()
    return f"{resolved_salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Return True if password matches the stored hash."""
    salt, _digest = stored.split("$", 1)
    return hmac.compare_digest(stored, hash_password(password, salt))


def new_token() -> str:
    """Generate a random session token."""
    return secrets.token_hex(32)


def set_signed_cookie(response: Response, request: Request, name: str, value: str) -> None:
    """Set an HttpOnly SameSite=Lax cookie; Secure is on when the request is HTTPS."""
    response.set_cookie(
        name,
        value,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def create_session(db: Session, user: User) -> str:
    """Persist a new session token for the user and return it."""
    token = new_token()
    db.add(AuthSession(token=token, user_id=user.id))
    db.flush()
    return token


def revoke_session(db: Session, token: str | None) -> None:
    """Delete a session row so the token can no longer authenticate."""
    if not token:
        return
    session = db.get(AuthSession, token)
    if session is not None:
        db.delete(session)
        db.flush()


def user_for_token(db: Session, token: str | None) -> User | None:
    """Look up a user by session token, or None if missing, expired, or invalid."""
    if not token:
        return None
    session = db.get(AuthSession, token)
    if session is None:
        return None
    created = session.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > SESSION_MAX_AGE:
        db.delete(session)
        db.flush()
        return None
    return db.get(User, session.user_id)


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolve a Bearer token to a user (JSON API only; cookies are ignored)."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = user_for_token(db, creds.credentials)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """Return the HTML-session user, or None when anonymous."""
    return user_for_token(db, request.cookies.get(COOKIE_NAME))


def csrf_token_for(request: Request) -> str:
    """Reuse the CSRF cookie or mint a new token."""
    existing = request.cookies.get(CSRF_COOKIE)
    return existing if existing else secrets.token_urlsafe(32)


def require_csrf(request: Request, csrf_token: str | None) -> None:
    """Reject the POST when the form token does not match the CSRF cookie."""
    cookie = request.cookies.get(CSRF_COOKIE)
    if (
        not cookie
        or not csrf_token
        or not hmac.compare_digest(cookie, csrf_token)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def seed_secret() -> str:
    """Password used for seeded demo users."""
    return os.environ.get("APP_SEED_PASSWORD") or "password"
