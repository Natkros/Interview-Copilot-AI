"""Fixed-window rate limiting. Uses Redis when REDIS_URL is configured so limits
hold across replicas; otherwise an in-process window (single node / tests)."""

from __future__ import annotations

import logging
import threading
import time

from app.core.config import get_settings

log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}
        self._redis = None
        url = get_settings().redis_url
        if url:
            try:
                import redis

                self._redis = redis.Redis.from_url(url, socket_timeout=0.2)
                self._redis.ping()
            except Exception as exc:  # degrade to local limiting, never fail requests
                log.warning("redis unavailable, using in-process rate limits", extra={"error": str(exc)})
                self._redis = None

    def hit(self, key: str, limit: int, window_s: int = 60) -> bool:
        """Record a hit; return True when the request is allowed."""
        bucket = int(time.time() // window_s)
        if self._redis is not None:
            try:
                rkey = f"rl:{key}:{bucket}"
                pipe = self._redis.pipeline()
                pipe.incr(rkey)
                pipe.expire(rkey, window_s + 1)
                count = pipe.execute()[0]
                return int(count) <= limit
            except Exception:
                pass
        with self._lock:
            b, count = self._windows.get(key, (bucket, 0))
            if b != bucket:
                b, count = bucket, 0
            count += 1
            self._windows[key] = (b, count)
            if len(self._windows) > 50_000:
                self._windows = {k: v for k, v in self._windows.items() if v[0] == bucket}
            return count <= limit

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


rate_limiter = RateLimiter()
