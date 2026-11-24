from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import LoginRequest, TokenResponse
from app.security import create_session, current_user, revoke_session, verify_password

_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Issue a session token for valid credentials."""
    user = db.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_session(db, user)
    return TokenResponse(token=token, username=user.username)


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict[str, str | int]:
    """Return the authenticated user."""
    return {"id": user.id, "username": user.username}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> None:
    """Revoke the Bearer session token."""
    if creds is not None and creds.scheme.lower() == "bearer":
        revoke_session(db, creds.credentials)
