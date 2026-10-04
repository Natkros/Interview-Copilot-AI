"""Password hashing, JWT session tokens and single-use WebSocket tickets."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

import bcrypt
import jwt

from app.core.config import get_settings

_ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + s.access_token_ttl_minutes * 60,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, s.auth_secret, algorithm=_ALGO)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, get_settings().auth_secret, algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return str(payload.get("sub")) if payload.get("sub") else None


@dataclass
class _Ticket:
    user_id: str
    session_id: str
    expires_at: float


class WsTicketStore:
    """Single-use, short-lived tickets so the browser can open a WebSocket to
    the API origin without exposing the session cookie cross-origin.

    In-process store; with multiple API replicas use sticky sessions or move
    this to Redis (same interface)."""

    def __init__(self) -> None:
        self._tickets: dict[str, _Ticket] = {}

    def issue(self, user_id: str, session_id: str) -> str:
        self._gc()
        token = secrets.token_urlsafe(32)
        ttl = get_settings().ws_ticket_ttl_seconds
        self._tickets[token] = _Ticket(user_id, session_id, time.time() + ttl)
        return token

    def redeem(self, token: str, session_id: str) -> str | None:
        ticket = self._tickets.pop(token, None)
        if not ticket or ticket.expires_at < time.time() or ticket.session_id != session_id:
            return None
        return ticket.user_id

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, t in self._tickets.items() if t.expires_at < now]:
            self._tickets.pop(k, None)


ws_tickets = WsTicketStore()
