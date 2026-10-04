"""InterviewOS AI - FastAPI application."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import client_ip
from app.api.routes import account, auth, intelligence, interviews, jobs, practice, resume
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.ratelimit import rate_limiter
from app.database.models import Base
from app.database.session import get_engine
from app.rag.indexer import seed_technical_knowledge
from app.rag.vectorstore import get_vector_store
from app.websocket.live import live_endpoint, reap_idle_runners, shutdown_runners

log = logging.getLogger("interviewos")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_HEADER = "x-interviewos-csrf"
_AUTH_PATHS = ("/auth/login", "/auth/register")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging()
    s.validate_for_production()
    get_engine()  # initialises from DATABASE_URL unless already initialised
    if s.database_url.startswith("sqlite"):
        # zero-dependency local runs; PostgreSQL deployments use `alembic upgrade head`
        Base.metadata.create_all(get_engine())
    store = get_vector_store()
    if store is not None:
        try:
            n = await asyncio.to_thread(seed_technical_knowledge)
            if n:
                log.info("technical knowledge seeded", extra={"count": n})
        except Exception as exc:
            log.warning("technical knowledge seeding failed", extra={"error": str(exc)})
    reaper = asyncio.create_task(reap_idle_runners())
    log.info("InterviewOS started", extra={"provider": s.resolved_llm_provider})
    yield
    reaper.cancel()
    await shutdown_runners()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="InterviewOS AI",
        version="0.1.0",
        description="Resume-grounded interview copilot: resume intelligence, hybrid RAG, real-time conversation "
                    "processing, grounded answer generation, mock interviews and analytics.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization", _CSRF_HEADER, "x-request-id"],
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        start = time.perf_counter()
        path = request.url.path
        ip = client_ip(request)
        # rate limiting (stricter on authentication endpoints)
        if path.startswith(_AUTH_PATHS):
            allowed = rate_limiter.hit(f"auth:{ip}", s.auth_rate_limit_per_minute)
        else:
            allowed = rate_limiter.hit(f"api:{ip}", s.rate_limit_per_minute)
        if not allowed:
            return JSONResponse({"detail": "Too many requests - please slow down."}, status_code=429,
                                headers={"Retry-After": "60", "x-request-id": rid})
        # CSRF: cookie-authenticated state changes must carry a custom header,
        # which browsers cannot send cross-site without passing CORS.
        if (request.method not in _SAFE_METHODS and request.cookies.get(s.cookie_name)
                and not request.headers.get("authorization") and request.headers.get(_CSRF_HEADER) != "1"):
            return JSONResponse({"detail": "Missing CSRF header"}, status_code=403, headers={"x-request-id": rid})
        # upload size guard before reading the body
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > (s.max_upload_mb + 1) * 1024 * 1024:
            return JSONResponse({"detail": f"Request exceeds the {s.max_upload_mb} MB limit"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": path, "method": request.method})
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        response.headers["x-request-id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        if s.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        log.info("request", extra={"method": request.method, "path": path, "status_code": response.status_code,
                                   "latency_ms": round((time.perf_counter() - start) * 1000, 2)})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    for r in (auth.router, resume.router, jobs.router, interviews.router, intelligence.router, practice.router,
              account.router):
        app.include_router(r)

    @app.websocket("/interviews/{session_id}/live")
    async def live(websocket: WebSocket, session_id: str) -> None:
        await live_endpoint(websocket, session_id)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        from app.rag.vectorstore import vector_store_status

        return {"status": "ok", "vector_store": vector_store_status()["available"],
                "llm": get_settings().resolved_llm_provider}

    return app


app = create_app()
