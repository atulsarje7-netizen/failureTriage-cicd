from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuthSession, User
from app.schemas import LoginRequest, TokenResponse
from app.security import current_user, new_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == body.username).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = new_token()
    db.add(AuthSession(token=token, user_id=user.id))
    db.flush()
    return TokenResponse(token=token, username=user.username)


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict[str, str | int]:
    return {"id": user.id, "username": user.username}
