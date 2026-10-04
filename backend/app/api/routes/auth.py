from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, audit
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RegisterBody(Credentials):
    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password must contain letters and numbers")
        return v


class UserOut(BaseModel):
    id: str
    email: str
    preferences: dict


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.cookie_name, token, httponly=True, secure=s.cookie_secure, samesite="lax",
        max_age=s.access_token_ttl_minutes * 60, path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterBody, request: Request, response: Response, db: DB) -> UserOut:
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password),
                preferences={"answer_length": "45s", "store_recordings": False})
    db.add(user)
    db.flush()
    audit(db, request, user.id, "auth.register")
    db.commit()
    _set_cookie(response, create_access_token(user.id))
    return UserOut(id=user.id, email=user.email, preferences=user.preferences)


@router.post("/login", response_model=UserOut)
def login(body: Credentials, request: Request, response: Response, db: DB) -> UserOut:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        audit(db, request, user.id if user else None, "auth.login_failed")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    audit(db, request, user.id, "auth.login")
    db.commit()
    _set_cookie(response, create_access_token(user.id))
    return UserOut(id=user.id, email=user.email, preferences=user.preferences or {})


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    s = get_settings()
    response.delete_cookie(s.cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email, preferences=user.preferences or {})
