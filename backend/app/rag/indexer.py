"""Builds and maintains each candidate's knowledge base in Qdrant, mirrored in
the `document_chunks` table (keyword search, source display, re-indexing)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib import resources

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import Timer
from app.database.models import CandidateProfile, DocumentChunk, JobDescription
from app.rag.chunking import ChunkSpec, job_chunks, profile_chunks
from app.rag.keyword import invalidate_keyword_cache
from app.rag.vectorstore import GLOBAL_CANDIDATE, get_vector_store

log = logging.getLogger(__name__)

PROFILE_COLLECTIONS = ("candidate_documents", "projects", "experience")


def _persist(db: Session, candidate_id: str, specs: list[ChunkSpec]) -> list[tuple[str, ChunkSpec]]:
    rows = []
    counters: dict[tuple, int] = {}
    for spec in specs:
        key = (spec.collection, spec.source_type, spec.source_ref, spec.meta.get("field"))
        ordinal = counters.get(key, 0)
        counters[key] = ordinal + 1
        pid = spec.point_id(candidate_id, ordinal)
        db.add(DocumentChunk(
            id=pid, user_id=candidate_id, document_id=spec.document_id, collection=spec.collection,
            source_type=spec.source_type, source_ref=spec.source_ref, text=spec.text, meta=spec.meta,
            verified=spec.verified,
        ))
        rows.append((pid, spec))
    db.flush()
    return rows


def _upsert_vectors(rows: list[tuple[str, ChunkSpec]]) -> bool:
    store = get_vector_store()
    if store is None:
        return False
    by_collection: dict[str, list] = {}
    for pid, spec in rows:
        by_collection.setdefault(spec.collection, []).append((pid, spec.text, spec.meta))
    for coll, items in by_collection.items():
        store.upsert(coll, items)
    return True


def reindex_profile(db: Session, profile: CandidateProfile) -> dict:
    """Rebuild the candidate's profile-derived knowledge base (idempotent)."""
    cid = profile.user_id
    with Timer() as t:
        db.execute(delete(DocumentChunk).where(
            DocumentChunk.user_id == cid, DocumentChunk.collection.in_(PROFILE_COLLECTIONS)
        ))
        store = get_vector_store()
        if store is not None:
            for coll in PROFILE_COLLECTIONS:
                store.delete_where(coll, cid)
        specs = profile_chunks(profile)
        rows = _persist(db, cid, specs)
        vectors_ok = _upsert_vectors(rows)
        profile.kb_version = (profile.kb_version or 0) + 1
        profile.kb_indexed_at = datetime.now(UTC)
        db.flush()
    invalidate_keyword_cache(cid)
    log.info("profile indexed", extra={"count": len(rows), "latency_ms": t.ms, "stage": "index"})
    return {"chunks": len(rows), "vector_index": vectors_ok, "version": profile.kb_version, "latency_ms": t.ms}


def index_job(db: Session, job: JobDescription) -> int:
    cid = job.user_id
    db.execute(delete(DocumentChunk).where(
        DocumentChunk.user_id == cid, DocumentChunk.collection == "job_descriptions",
        DocumentChunk.source_ref == job.id,
    ))
    store = get_vector_store()
    if store is not None:
        store.delete_where("job_descriptions", cid, job_id=job.id)
    rows = _persist(db, cid, job_chunks(job))
    _upsert_vectors(rows)
    invalidate_keyword_cache(cid)
    return len(rows)


def remove_job(db: Session, job: JobDescription) -> None:
    db.execute(delete(DocumentChunk).where(DocumentChunk.source_ref == job.id, DocumentChunk.user_id == job.user_id))
    store = get_vector_store()
    if store is not None:
        store.delete_where("job_descriptions", job.user_id, job_id=job.id)
    invalidate_keyword_cache(job.user_id)


def index_history_turn(db: Session, candidate_id: str, session_id: str, turn_id: str, question: str,
                       answer: str, topic: str | None, focus_project: str | None) -> None:
    """Index a finished Q/A exchange so later follow-ups can retrieve what was
    already said in this interview."""
    meta = {
        "candidate_id": candidate_id, "document_type": "interview_history", "session_id": session_id,
        "section": "Interview", "verification_status": "session", "source": "This interview",
        "topic": topic, "project": focus_project, "created_at": datetime.now(UTC).isoformat(),
        "question": question[:300], "field": turn_id,
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    spec = ChunkSpec("interview_history", "history", turn_id,
                     f"Interviewer asked: {question}\nCandidate answered: {answer}", False, meta=meta)
    rows = _persist(db, candidate_id, [spec])
    try:
        _upsert_vectors(rows)
    except Exception as exc:  # history indexing must never break the live loop
        log.warning("history vector upsert failed", extra={"error": str(exc)})
    invalidate_keyword_cache(candidate_id)


def remove_session_history(db: Session, candidate_id: str, session_id: str) -> None:
    ids = [r.id for r in db.scalars(select(DocumentChunk).where(
        DocumentChunk.user_id == candidate_id, DocumentChunk.collection == "interview_history"))
        if (r.meta or {}).get("session_id") == session_id]
    if ids:
        db.execute(delete(DocumentChunk).where(DocumentChunk.id.in_(ids)))
    store = get_vector_store()
    if store is not None:
        store.delete_where("interview_history", candidate_id, session_id=session_id)
    invalidate_keyword_cache(candidate_id)


def load_technical_knowledge() -> list[dict]:
    raw = resources.files("app.rag").joinpath("technical_knowledge.json").read_text(encoding="utf-8")
    return json.loads(raw)


def seed_technical_knowledge() -> int:
    store = get_vector_store()
    if store is None:
        return 0
    entries = load_technical_knowledge()
    if store.count("technical_knowledge", GLOBAL_CANDIDATE) == len(entries):
        return 0
    store.delete_where("technical_knowledge", GLOBAL_CANDIDATE)
    import uuid

    items = [
        (str(uuid.uuid5(uuid.NAMESPACE_URL, e["id"])), f"{e['title']}: {e['text']}",
         {"candidate_id": GLOBAL_CANDIDATE, "document_type": "technical_knowledge", "section": e["topic"],
          "source": "Technical knowledge base", "title": e["title"], "kb_id": e["id"],
          "verification_status": "reference"})
        for e in entries
    ]
    store.upsert("technical_knowledge", items)
    return len(items)
