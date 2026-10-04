"""Hybrid retrieval: dense (Qdrant) + sparse (BM25) + metadata filters,
fused with reciprocal-rank fusion and weighted by source priority.

Priorities follow the spec (verified candidate facts 1.00, verified project
information 0.95, interview history 0.90, job description 0.85, technical
knowledge 0.75); unverified candidate facts are discounted. They are tunable
via `PRIORITIES` and are evaluated by `app.evaluation.benchmark`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import Timer
from app.database.models import DocumentChunk
from app.models.domain import ContextBundle, FocusState, RetrievedChunk
from app.rag.embeddings import get_embedder, tokenize
from app.rag.keyword import KeywordDoc, candidate_index, global_technical_index
from app.rag.vectorstore import GLOBAL_CANDIDATE, get_vector_store
from app.services.taxonomy import find_technologies

log = logging.getLogger(__name__)

PRIORITIES = {
    "candidate_verified": 1.00,
    "project_verified": 0.95,
    "history": 0.90,
    "job": 0.85,
    "technical": 0.75,
    "unverified_factor": 0.70,
}
RRF_K = 60

# which project fields answer which follow-up aspect
ASPECT_FIELDS: dict[str, set[str]] = {
    "challenge": {"challenges", "solutions"},
    "solution": {"solutions", "challenges"},
    "role": {"overview", "responsibilities"},
    "why_choice": {"overview", "architecture"},
    "architecture": {"architecture", "overview", "responsibilities"},
    "result": {"results", "metrics"},
    "metric": {"metrics", "results"},
    "improvement": {"future_work", "limitations"},
    "limitation": {"limitations", "future_work"},
    "testing": {"testing", "results"},
    "overview": {"overview", "responsibilities", "architecture"},
    "technology": {"overview", "architecture", "responsibilities"},
}


@dataclass
class RetrievalPlan:
    candidate: bool = True
    job: bool = False
    technical: bool = False
    history: bool = False
    overview: bool = False  # include profile overview chunks (introduction questions)
    k_candidate: int = 8
    k_job: int = 3
    k_technical: int = 3
    k_history: int = 3
    extra_queries: list[str] = field(default_factory=list)


def _to_chunk(row: DocumentChunk, score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(id=row.id, collection=row.collection, source_type=row.source_type, text=row.text,
                          score=score, verified=row.verified, meta=dict(row.meta or {}))


def _priority(chunk: RetrievedChunk) -> float:
    if chunk.collection == "job_descriptions":
        return PRIORITIES["job"]
    if chunk.collection == "technical_knowledge":
        return PRIORITIES["technical"]
    if chunk.collection == "interview_history":
        return PRIORITIES["history"]
    base = PRIORITIES["project_verified"] if chunk.collection == "projects" else PRIORITIES["candidate_verified"]
    return base if chunk.verified else base * PRIORITIES["unverified_factor"]


class HybridRetriever:
    def __init__(self, db: Session, candidate_id: str) -> None:
        self.db = db
        self.candidate_id = candidate_id

    # -------------------------------------------------------------- sources

    def _keyword_docs(self) -> list[KeywordDoc]:
        rows = self.db.scalars(select(DocumentChunk).where(DocumentChunk.user_id == self.candidate_id)).all()
        return [KeywordDoc(id=r.id, collection=r.collection, source_type=r.source_type, text=r.text,
                           verified=r.verified, meta=dict(r.meta or {})) for r in rows]

    def _rows_by_id(self, ids: list[str]) -> dict[str, DocumentChunk]:
        if not ids:
            return {}
        rows = self.db.scalars(select(DocumentChunk).where(
            DocumentChunk.user_id == self.candidate_id, DocumentChunk.id.in_(ids))).all()
        return {r.id: r for r in rows}

    def _dense(self, query_vec: list[float], collection: str, limit: int,
               filters: dict[str, str] | None = None) -> list[tuple[str, float]]:
        store = get_vector_store()
        if store is None:
            return []
        try:
            hits = store.search(collection, query_vec, self.candidate_id, limit=limit, filters=filters)
        except Exception as exc:  # Qdrant down mid-session -> keyword only
            log.warning("dense retrieval failed", extra={"error": str(exc), "stage": collection})
            return []
        return [(h.id, h.score) for h in hits]

    def _dense_technical(self, query_vec: list[float], limit: int) -> list[tuple[str, float]]:
        store = get_vector_store()
        if store is None:
            return []
        try:
            hits = store.search("technical_knowledge", query_vec, GLOBAL_CANDIDATE, limit=limit)
        except Exception as exc:
            log.warning("technical retrieval failed", extra={"error": str(exc)})
            return []
        return [(str(h.payload.get("kb_id")), h.score) for h in hits]

    # -------------------------------------------------------------- fusion

    @staticmethod
    def _rrf(rankings: list[list[str]]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for ranking in rankings:
            for rank, doc_id in enumerate(ranking):
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        return scores

    def _boost(self, chunk: RetrievedChunk, focus: FocusState, aspect: str | None, query_techs: set[str]) -> float:
        factor = 1.0
        m = chunk.meta
        if focus.project_id and m.get("project_id") == focus.project_id:
            factor *= 1.6
            if aspect and m.get("field") in ASPECT_FIELDS.get(aspect, set()):
                factor *= 1.8
        elif focus.experience_id and m.get("experience_id") == focus.experience_id:
            factor *= 1.5
        elif aspect and m.get("field") in ASPECT_FIELDS.get(aspect, set()):
            factor *= 1.15
        techs = m.get("technology") or []
        if isinstance(techs, str):
            techs = [techs]
        if query_techs and (query_techs & {t.lower() for t in techs} or any(t in chunk.text.lower() for t in query_techs)):
            factor *= 1.25
        return factor

    def _fuse(self, rankings: list[list[str]], rows: dict[str, RetrievedChunk], focus: FocusState,
              aspect: str | None, query_techs: set[str], k: int) -> list[RetrievedChunk]:
        rrf = self._rrf(rankings)
        out = []
        for cid, s in rrf.items():
            chunk = rows.get(cid)
            if chunk is None:
                continue
            chunk.priority = _priority(chunk)
            chunk.score = s * chunk.priority * self._boost(chunk, focus, aspect, query_techs)
            out.append(chunk)
        out.sort(key=lambda c: -c.score)
        top = out[:k]
        if top:
            best = top[0].score
            for c in top:
                c.score = round(c.score / best, 4) if best else 0.0
        return top

    # -------------------------------------------------------------- layers

    def candidate_layer(self, query: str, qvec: list[float], plan: RetrievalPlan, focus: FocusState,
                        aspect: str | None) -> list[RetrievedChunk]:
        bm25 = candidate_index(self.candidate_id, self._keyword_docs)
        colls = {"candidate_documents", "projects", "experience"}
        rankings: list[list[str]] = []
        for coll in colls:
            rankings.append([pid for pid, _ in self._dense(qvec, coll, plan.k_candidate)])
        for q in [query, *plan.extra_queries]:
            rankings.append([d.id for d, _ in bm25.search(q, limit=plan.k_candidate * 2, collections=colls)])
        if focus.project_id:
            focus_ids = [d.id for d in bm25.docs if d.meta.get("project_id") == focus.project_id]
            rankings.append(focus_ids)
        if focus.experience_id:
            rankings.append([d.id for d in bm25.docs if d.meta.get("experience_id") == focus.experience_id])
        if plan.overview:
            rankings.append([d.id for d in bm25.docs if d.collection in colls and (
                d.meta.get("field") == "overview" or d.source_type in ("summary", "education", "skills"))])
        ids = list({i for r in rankings for i in r})
        by_id = {d.id: d for d in bm25.docs if d.id in set(ids)}
        rows = {i: RetrievedChunk(id=d.id, collection=d.collection, source_type=d.source_type, text=d.text,
                                  verified=d.verified, meta=dict(d.meta)) for i, d in by_id.items()}
        techs = {t.lower() for t in find_technologies(query)}
        top = self._fuse(rankings, rows, focus, aspect, techs, plan.k_candidate)
        if plan.overview:
            # introduction-style questions draw on the whole profile overview:
            # include every overview fact (bounded by profile size), after the ranked hits
            seen = {c.id for c in top}
            for d in bm25.docs:
                if d.id in seen or d.collection not in colls:
                    continue
                if d.meta.get("field") == "overview" or d.source_type in (
                        "summary", "education", "skills", "certification", "achievements", "internship", "job"):
                    if d.collection == "experience" and d.meta.get("field") != "overview":
                        continue
                    c = RetrievedChunk(id=d.id, collection=d.collection, source_type=d.source_type, text=d.text,
                                       verified=d.verified, meta=dict(d.meta), score=0.0)
                    c.priority = _priority(c)
                    top.append(c)
        return top

    def job_layer(self, query: str, qvec: list[float], job_id: str, plan: RetrievalPlan) -> list[RetrievedChunk]:
        bm25 = candidate_index(self.candidate_id, self._keyword_docs)
        dense = [pid for pid, _ in self._dense(qvec, "job_descriptions", plan.k_job * 2, {"job_id": job_id})]
        sparse = [d.id for d, _ in bm25.search(query, limit=plan.k_job * 2, collections={"job_descriptions"},
                                               where={"job_id": job_id})]
        rows = {d.id: RetrievedChunk(id=d.id, collection=d.collection, source_type=d.source_type, text=d.text,
                                     verified=True, meta=dict(d.meta))
                for d in bm25.docs if d.id in set(dense + sparse)}
        return self._fuse([dense, sparse], rows, FocusState(), None, set(), plan.k_job)

    def history_layer(self, query: str, qvec: list[float], session_id: str, plan: RetrievalPlan) -> list[RetrievedChunk]:
        bm25 = candidate_index(self.candidate_id, self._keyword_docs)
        dense = [pid for pid, _ in self._dense(qvec, "interview_history", plan.k_history * 2, {"session_id": session_id})]
        sparse = [d.id for d, _ in bm25.search(query, limit=plan.k_history * 2, collections={"interview_history"},
                                               where={"session_id": session_id})]
        rows = {d.id: RetrievedChunk(id=d.id, collection=d.collection, source_type=d.source_type, text=d.text,
                                     verified=False, meta=dict(d.meta))
                for d in bm25.docs if d.id in set(dense + sparse)}
        return self._fuse([dense, sparse], rows, FocusState(), None, set(), plan.k_history)

    def technical_layer(self, query: str, qvec: list[float], plan: RetrievalPlan) -> list[RetrievedChunk]:
        idx = global_technical_index()
        dense = [pid for pid, _ in self._dense_technical(qvec, plan.k_technical * 2)]
        sparse = [d.id for d, _ in idx.search(query, limit=plan.k_technical * 2)]
        rows = {d.id: RetrievedChunk(id=d.id, collection="technical_knowledge", source_type="technical",
                                     text=d.text, verified=False, meta=dict(d.meta))
                for d in idx.docs if d.id in set(dense + sparse)}
        techs = {t.lower() for t in find_technologies(query)}
        # an entry *about* the asked topic outranks entries that merely mention it:
        # entries whose title terms are all in the question, most specific title first
        q_tokens = set(tokenize(query))
        titled_scored = []
        for d in idx.docs:
            t_tokens = {t for t in tokenize(d.meta.get("title") or "") if len(t) > 2}
            if t_tokens and t_tokens <= q_tokens:
                titled_scored.append((len(t_tokens), d.id))
            elif (d.meta.get("title") or "").lower() in techs:
                titled_scored.append((1, d.id))
        titled = [i for _, i in sorted(titled_scored, reverse=True)]
        return self._fuse([dense, sparse, titled, titled, titled], rows | {
            d.id: RetrievedChunk(id=d.id, collection="technical_knowledge", source_type="technical", text=d.text,
                                 verified=False, meta=dict(d.meta)) for d in idx.docs if d.id in titled
        }, FocusState(), None, techs, plan.k_technical)

    def evidence_pool(self, session_id: str | None) -> list[RetrievedChunk]:
        """All of the candidate's knowledge-base chunks plus this session's
        history - the admissible evidence for candidate-specific claims."""
        bm25 = candidate_index(self.candidate_id, self._keyword_docs)
        out = []
        for d in bm25.docs:
            if d.collection == "job_descriptions":
                continue
            if d.collection == "interview_history" and d.meta.get("session_id") != session_id:
                continue
            out.append(RetrievedChunk(id=d.id, collection=d.collection, source_type=d.source_type, text=d.text,
                                      verified=d.verified, meta=dict(d.meta)))
        return out

    # -------------------------------------------------------------- entry

    async def retrieve(self, query: str, plan: RetrievalPlan, focus: FocusState | None = None,
                       aspect: str | None = None, job_id: str | None = None,
                       session_id: str | None = None, technical_query: str | None = None) -> ContextBundle:
        focus = focus or FocusState()
        bundle = ContextBundle()
        with Timer() as t:
            qvec = await asyncio.to_thread(get_embedder().embed_one, query)
            tq = technical_query or query
            tvec = qvec if tq == query else await asyncio.to_thread(get_embedder().embed_one, tq)
            tasks: dict[str, asyncio.Future] = {}
            # Note: SQLAlchemy sessions are not thread-safe; the keyword corpus is
            # loaded once here (cached per candidate) before fanning out.
            candidate_index(self.candidate_id, self._keyword_docs)
            if plan.candidate:
                tasks["candidate"] = asyncio.to_thread(self.candidate_layer, query, qvec, plan, focus, aspect)
            if plan.job and job_id:
                tasks["job"] = asyncio.to_thread(self.job_layer, query, qvec, job_id, plan)
            if plan.history and session_id:
                tasks["history"] = asyncio.to_thread(self.history_layer, query, qvec, session_id, plan)
            if plan.technical:
                tasks["technical"] = asyncio.to_thread(self.technical_layer, tq, tvec, plan)
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for name, res in zip(tasks.keys(), results, strict=True):
                if isinstance(res, BaseException):
                    log.error("retrieval layer failed", extra={"stage": name, "error": repr(res)})
                    continue
                setattr(bundle, name, res)
        bundle.retrieval_ms = t.ms
        return bundle

    def retrieve_sync(self, *args, **kwargs) -> ContextBundle:
        return asyncio.run(self.retrieve(*args, **kwargs))
