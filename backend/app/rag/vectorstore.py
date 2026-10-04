"""Qdrant vector store with enforced tenant isolation.

Every point carries `candidate_id`. Every search issued through this class
adds a `candidate_id` must-filter - there is no code path that searches
private collections without it. The shared `technical_knowledge` collection
stores public reference material under `candidate_id="global"`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.config import get_settings
from app.rag.embeddings import get_embedder

log = logging.getLogger(__name__)

COLLECTIONS = (
    "candidate_documents",
    "projects",
    "experience",
    "job_descriptions",
    "technical_knowledge",
    "interview_history",
)
GLOBAL_CANDIDATE = "global"
_INDEXED_FIELDS = ("candidate_id", "document_id", "document_type", "section", "project_id",
                   "verification_status", "job_id", "session_id", "field")


class VectorStoreUnavailable(RuntimeError):
    pass


@dataclass
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        s = get_settings()
        url = url or s.qdrant_url
        self._lock = threading.RLock()
        self._local = not (url.startswith("http://") or url.startswith("https://"))
        try:
            if url == ":memory:":
                self.client = QdrantClient(location=":memory:")
            elif url.startswith("http://") or url.startswith("https://"):
                self.client = QdrantClient(url=url, api_key=api_key or s.qdrant_api_key, timeout=5)
            else:
                self.client = QdrantClient(path=url)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise VectorStoreUnavailable(str(exc)) from exc
        self.dim = get_embedder().dim
        self._ensure_collections()

    def _ensure_collections(self) -> None:
        for name in COLLECTIONS:
            if self.client.collection_exists(name):
                size = self.client.get_collection(name).config.params.vectors.size  # type: ignore[union-attr]
                if size == self.dim:
                    continue
                log.warning("embedding dimension changed; recreating collection", extra={"event": name})
                self.client.delete_collection(name)
            self.client.create_collection(
                name, vectors_config=qm.VectorParams(size=self.dim, distance=qm.Distance.COSINE)
            )
            if self._local:
                continue  # payload indexes are a server feature
            for f in _INDEXED_FIELDS:
                try:
                    self.client.create_payload_index(name, f, field_schema=qm.PayloadSchemaType.KEYWORD)
                except Exception:  # local mode does not support payload indexes
                    pass

    # ------------------------------------------------------------------ writes

    def upsert(self, collection: str, items: list[tuple[str, str, dict[str, Any]]]) -> None:
        """items: (point_id, text, payload). Payload must include candidate_id."""
        if not items:
            return
        for _, _, payload in items:
            if not payload.get("candidate_id"):
                raise ValueError("refusing to index a point without candidate_id")
        vectors = get_embedder().embed([t for _, t, _ in items])
        points = [
            qm.PointStruct(id=pid, vector=vec, payload={**payload, "text": text})
            for (pid, text, payload), vec in zip(items, vectors, strict=True)
        ]
        with self._lock:
            for i in range(0, len(points), 256):
                self.client.upsert(collection, points=points[i: i + 256], wait=True)

    def delete_where(self, collection: str, candidate_id: str, **conditions: str) -> None:
        must = [qm.FieldCondition(key="candidate_id", match=qm.MatchValue(value=candidate_id))]
        must += [qm.FieldCondition(key=k, match=qm.MatchValue(value=v)) for k, v in conditions.items()]
        with self._lock:
            self.client.delete(collection, points_selector=qm.FilterSelector(filter=qm.Filter(must=must)), wait=True)

    def delete_candidate(self, candidate_id: str) -> None:
        if candidate_id == GLOBAL_CANDIDATE:
            raise ValueError("cannot delete global knowledge through a candidate deletion")
        for name in COLLECTIONS:
            self.delete_where(name, candidate_id)

    def count(self, collection: str, candidate_id: str) -> int:
        flt = qm.Filter(must=[qm.FieldCondition(key="candidate_id", match=qm.MatchValue(value=candidate_id))])
        return self.client.count(collection, count_filter=flt, exact=True).count

    # ------------------------------------------------------------------ reads

    def search(
        self,
        collection: str,
        vector: list[float],
        candidate_id: str,
        limit: int = 8,
        filters: dict[str, str] | None = None,
    ) -> list[VectorHit]:
        if not candidate_id:
            raise ValueError("candidate_id is required for every search")
        must = [qm.FieldCondition(key="candidate_id", match=qm.MatchValue(value=candidate_id))]
        for k, v in (filters or {}).items():
            must.append(qm.FieldCondition(key=k, match=qm.MatchValue(value=v)))
        # embedded/local mode is not thread-safe; the server client is
        with self._lock if self._local else nullcontext():
            res = self.client.query_points(
                collection, query=vector, query_filter=qm.Filter(must=must), limit=limit, with_payload=True
            )
        hits = []
        for p in res.points:
            payload = dict(p.payload or {})
            # defence in depth: never return another tenant's point
            if payload.get("candidate_id") != candidate_id:
                log.error("tenant filter violation suppressed", extra={"event": collection})
                continue
            hits.append(VectorHit(id=str(p.id), score=float(p.score), payload=payload))
        return hits


_store: VectorStore | None = None
_store_error: str | None = None


def get_vector_store() -> VectorStore | None:
    """Return the shared store, or None when Qdrant is unavailable (callers
    degrade to keyword retrieval)."""
    global _store, _store_error
    if _store is None and _store_error is None:
        try:
            _store = VectorStore()
        except Exception as exc:
            _store_error = str(exc)
            log.error("qdrant unavailable - degraded to keyword retrieval", extra={"error": str(exc)})
    return _store


def reset_vector_store(store: VectorStore | None = None) -> None:
    global _store, _store_error
    _store, _store_error = store, None


def vector_store_status() -> dict[str, Any]:
    return {"available": _store is not None, "error": _store_error}
