"""Structured JSON logging with request/session/candidate correlation.

Log records never include transcript text, resume text, or answers: only ids,
counts, latencies and scores. Callers pass metrics through `extra=`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)
candidate_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("candidate_id", default=None)

_ALLOWED_EXTRA = {
    "latency_ms", "model", "tokens_in", "tokens_out", "retrieval_count", "grounding_score",
    "error", "event", "status_code", "path", "method", "stage", "provider", "count",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, var in (
            ("request_id", request_id_var),
            ("session_id", session_id_var),
            ("candidate_id", candidate_id_var),
        ):
            value = var.get()
            if value:
                payload[key] = value
        for key in _ALLOWED_EXTRA:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Timer:
    """`with Timer() as t: ...; t.ms`"""

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000, 2)
