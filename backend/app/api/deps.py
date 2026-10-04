from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import candidate_id_var
from app.core.security import decode_access_token
from app.database.models import AuditLog, User
from app.database.session import get_db

DB = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def _token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(get_settings().cookie_name)


def current_user(request: Request, db: DB) -> User:
    token = _token(request)
    user_id = decode_access_token(token) if token else None
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated or session expired",
                            headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled")
    candidate_id_var.set(user.id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def audit(db: Session, request: Request | None, user_id: str | None, action: str, target: str | None = None,
          **meta: Any) -> None:
    db.add(AuditLog(user_id=user_id, action=action, target=target,
                    ip=client_ip(request) if request else None, meta=meta))


def owned(db: Session, model: type, obj_id: str, user: User) -> Any:
    """Fetch a row owned by `user` or raise 404 (never reveal other tenants' ids)."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj
