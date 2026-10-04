"""Supervisor and agents.

                    Supervisor
                        |
        +---------------+----------------+
        v               v                v
  Resume Agent     Technical Agent   Conversation Agent      (+ job context)
        +---------------+----------------+
                        v
                  Answer Agent  -> Validation Agent -> Evaluation Agent

The supervisor decides per question which agents run (a greeting needs no
retrieval; a textbook question needs no resume lookup). Retrieval agents run
concurrently. Every step is timed; the pipeline yields structured events that
the WebSocket layer forwards to the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.composer import OfflineComposer, word_count
from app.agents.conversation import ConversationManager
from app.agents.evaluation import evaluate_answer
from app.agents.llm import LLMError, StreamResult, get_llm
from app.agents.prompts import (
    META_DELIMITER,
    SYSTEM_PROMPT,
    build_user_prompt,
    max_tokens_for,
    structure_for,
)
from app.agents.validation import GroundingValidator
from app.core.logging import Timer
from app.database.models import (
    Answer,
    AnswerEvaluation,
    CandidateProfile,
    InterviewSession,
    JobDescription,
    Project,
    Question,
    User,
)
from app.models.domain import (
    NON_ANSWERABLE_TYPES,
    AnswerResult,
    Classification,
    ContextBundle,
    LatencyBreakdown,
    QuestionType,
    ResolvedQuestion,
    SourceRef,
)
from app.rag.indexer import index_history_turn
from app.rag.retriever import HybridRetriever, RetrievalPlan
from app.services.profile import project_facts

log = logging.getLogger(__name__)
Q = QuestionType

_CATEGORY_ORDER = ["programming_language", "ai_ml", "framework", "database", "cloud", "library", "devops", "concept", "tool", "other"]


# ----------------------------------------------------------------------------- planning


@dataclass
class AgentPlan:
    agents: list[str]
    retrieval: RetrievalPlan
    answer: bool = True


def plan_for(cls: Classification, resolved: ResolvedQuestion, has_job: bool) -> AgentPlan:
    t = cls.type
    if t in NON_ANSWERABLE_TYPES:
        return AgentPlan(agents=["conversation"], retrieval=RetrievalPlan(candidate=False), answer=False)
    if t == Q.GREETING:
        return AgentPlan(agents=["answer"], retrieval=RetrievalPlan(candidate=False))
    if t == Q.CLARIFICATION:
        return AgentPlan(agents=["conversation", "answer"], retrieval=RetrievalPlan(candidate=False, history=True))
    rp = RetrievalPlan(
        candidate=cls.requires_resume_context or t in (Q.INTRODUCTION, Q.RESUME, Q.PROJECT, Q.BEHAVIORAL, Q.HR, Q.FOLLOW_UP),
        technical=cls.requires_technical_context,
        history=cls.requires_previous_turn_context or t == Q.FOLLOW_UP,
        job=has_job and t in (Q.INTRODUCTION, Q.HR, Q.BEHAVIORAL, Q.PROJECT, Q.RESUME, Q.SYSTEM_DESIGN, Q.FOLLOW_UP,
                              Q.TECHNICAL, Q.SITUATIONAL),
        overview=t in (Q.INTRODUCTION, Q.HR, Q.BEHAVIORAL),
    )
    # textbook questions still check the candidate's own usage (practical relevance)
    if t.value in {"TECHNICAL", "RAG", "DATABASE", "CLOUD", "DEVOPS", "MACHINE_LEARNING", "DEEP_LEARNING",
                   "GENERATIVE_AI", "SECURITY"} and not rp.candidate:
        rp.candidate = True
        rp.k_candidate = 4
    agents = []
    if rp.candidate:
        agents.append("resume")
    if rp.technical:
        agents.append("technical")
    if rp.history:
        agents.append("conversation")
    if rp.job:
        agents.append("job_context")
    agents += ["answer", "validation", "evaluation"]
    return AgentPlan(agents=agents, retrieval=rp)


# ----------------------------------------------------------------------------- overview


def build_overview(profile: CandidateProfile | None) -> dict[str, Any]:
    if profile is None:
        return {}

    def order(rows):
        return sorted(rows, key=lambda r: (not r.verified, r.position))

    skills = order(profile.skills)
    skills_sorted = sorted(skills, key=lambda s: (not s.verified, _CATEGORY_ORDER.index(s.category) if s.category in _CATEGORY_ORDER else 99, s.position))
    return {
        "name": profile.name, "headline": profile.headline, "summary": profile.summary,
        "education": [{"title": i.title, "subtitle": i.subtitle, "date": i.date, "verified": i.verified}
                      for i in order([i for i in profile.items if i.kind == "education"])],
        "achievements": [{"title": i.title, "date": i.date} for i in order([i for i in profile.items if i.kind == "achievements"])],
        "projects": [{"id": p.id, "name": p.name, "description": p.description, "technologies": p.technologies or [],
                      "responsibilities": p.responsibilities or [], "challenges": p.challenges or [],
                      "solutions": p.solutions or [], "results": p.results or [], "verified": p.verified}
                     for p in order(profile.projects)],
        "experiences": [{"id": e.id, "title": e.title, "organization": e.organization, "kind": e.kind,
                         "highlights": e.highlights or [], "technologies": e.technologies or [], "verified": e.verified}
                        for e in order(profile.experiences)],
        "certifications": [{"name": c.name, "issuer": c.issuer, "date": c.date} for c in order(profile.certifications)],
        "top_skills": [s.name for s in skills_sorted if s.category not in ("concept", "tool")][:8],
        "all_skills": [s.name for s in profile.skills],
    }


def candidate_entities(profile: CandidateProfile | None) -> list[str]:
    if profile is None:
        return []
    ents = [p.name for p in profile.projects]
    ents += [e.organization for e in profile.experiences if e.organization]
    ents += [c.name for c in profile.certifications]
    return ents


# ----------------------------------------------------------------------------- agents


class ResumeAgent:
    name = "resume"

    def __init__(self, db: Session, profile: CandidateProfile | None) -> None:
        self.db = db
        self.profile = profile

    def focus_project(self, project_id: str | None):
        if not project_id or self.profile is None:
            return None
        p = self.db.get(Project, project_id)
        if p is None or p.profile_id != self.profile.id:
            return None
        return project_facts(p)


class TechnicalAgent:
    name = "technical"


class ConversationAgent:
    name = "conversation"


class AnswerAgent:
    """Streams a candidate answer from the LLM, or composes it offline."""

    name = "answer"

    def __init__(self) -> None:
        self.offline = OfflineComposer()

    async def stream(self, question: str, resolved: ResolvedQuestion, cls: Classification, bundle: ContextBundle,
                     overview: dict, recent: list[dict], summaries: list[dict], length: str, variant: str,
                     state: dict, budget: int | None = None) -> AsyncIterator[dict]:
        """Yields {"delta": str} / {"reset": reason}; fills `state` with
        text, key_points, star, insufficient, notes, model, degraded, first_token_ms."""
        llm = get_llm()
        start = time.perf_counter()
        if llm is not None:
            prompt = build_user_prompt(question, resolved, cls, bundle, recent, summaries, length, variant, budget)
            result = StreamResult()
            buf = ""
            emitted = 0
            try:
                async for piece in llm.stream(SYSTEM_PROMPT, prompt, max_tokens_for(length, variant), result):
                    buf += piece
                    if META_DELIMITER in buf:
                        visible = buf.split(META_DELIMITER, 1)[0]
                    else:
                        visible = buf[: max(0, len(buf) - len(META_DELIMITER))]
                    if len(visible) > emitted:
                        if "first_token_ms" not in state:
                            state["first_token_ms"] = round((time.perf_counter() - start) * 1000, 2)
                        yield {"delta": visible[emitted:]}
                        emitted = len(visible)
                text, meta = _split_meta(buf)
                if len(text) > emitted:
                    yield {"delta": text[emitted:]}
                state.update(text=text, key_points=meta.get("key_points") or [], star=meta.get("star") or {},
                             insufficient=bool(meta.get("insufficient_context")), notes=meta.get("notes") or [],
                             model=result.model or getattr(llm, "model", llm.name), tokens_in=result.tokens_in,
                             tokens_out=result.tokens_out, cache_read=result.cache_read)
                if not text.strip():
                    raise LLMError("empty response")
                return
            except LLMError as exc:
                log.warning("llm generation failed; using offline composer", extra={"error": str(exc)})
                state["degraded"] = True
                state["degraded_reason"] = str(exc)
                if emitted:
                    yield {"reset": "AI service interrupted - showing an answer assembled from your profile."}
        draft = self.offline.compose(question, resolved, cls, bundle, overview, recent, length, variant, budget)
        state.setdefault("first_token_ms", round((time.perf_counter() - start) * 1000, 2))
        words = draft.text.split(" ")
        for i in range(0, len(words), 6):
            yield {"delta": (" " if i else "") + " ".join(words[i: i + 6])}
            await asyncio.sleep(0)
        notes = list(draft.notes)
        if state.get("degraded"):
            notes.insert(0, "AI service temporarily unavailable - this answer was assembled directly from your profile.")
        state.update(text=draft.text, key_points=draft.key_points, star=draft.star, insufficient=draft.insufficient,
                     notes=notes, model=self.offline.name)


def _split_meta(raw: str) -> tuple[str, dict]:
    if META_DELIMITER not in raw:
        return raw.strip(), {}
    text, tail = raw.split(META_DELIMITER, 1)
    m = re.search(r"\{.*\}", tail, re.S)
    meta: dict = {}
    if m:
        try:
            meta = json.loads(m.group(0))
        except json.JSONDecodeError:
            meta = {}
    return text.strip(), meta


# ----------------------------------------------------------------------------- pipeline


@dataclass
class PipelineContext:
    db: Session
    user: User
    profile: CandidateProfile | None
    session: InterviewSession | None = None
    manager: ConversationManager | None = None
    job: JobDescription | None = None
    length: str = "45s"
    overview: dict = field(default_factory=dict)
    reference_words: int | None = None  # length of the answer a shorter/longer variant is relative to


def _sources(bundle: ContextBundle) -> list[SourceRef]:
    out = []
    for c in bundle.all_chunks():
        out.append(SourceRef(id=c.id, label=c.label, collection=c.collection, excerpt=c.text[:600],
                             verified=c.verified, meta={k: v for k, v in c.meta.items()
                                                        if k in ("section", "project", "field", "page", "source",
                                                                 "title", "experience", "job_id", "verification_status")}))
    return out


class InterviewPipeline:
    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.resume_agent = ResumeAgent(ctx.db, ctx.profile)
        self.answer_agent = AnswerAgent()
        self.validator = GroundingValidator(candidate_entities(ctx.profile))
        if not ctx.overview:
            ctx.overview = build_overview(ctx.profile)

    def _job_fields(self) -> tuple[str | None, list[str]]:
        job = self.ctx.job
        if job is None:
            return None, []
        parsed = job.parsed or {}
        kws = list(dict.fromkeys([*(parsed.get("required_skills") or []), *(parsed.get("technologies") or [])]))
        return job.title, kws[:25]

    async def gather_context(self, query: str, cls: Classification, resolved: ResolvedQuestion,
                             plan: AgentPlan) -> ContextBundle:
        ctx = self.ctx
        retriever = HybridRetriever(ctx.db, ctx.user.id)
        tech_query = resolved.original
        if resolved.focus.technology and resolved.focus.technology.lower() not in tech_query.lower():
            tech_query += f" {resolved.focus.technology}"
        bundle = await retriever.retrieve(
            query, plan.retrieval, focus=resolved.focus, aspect=resolved.aspect,
            job_id=ctx.job.id if ctx.job else None, session_id=ctx.session.id if ctx.session else None,
            technical_query=tech_query,
        )
        bundle.evidence_pool = retriever.evidence_pool(ctx.session.id if ctx.session else None)
        bundle.focus_project = self.resume_agent.focus_project(resolved.focus.project_id) if plan.retrieval.candidate else None
        if bundle.focus_project is None and resolved.focus.project_id and cls.type in (Q.FOLLOW_UP, Q.PROJECT):
            bundle.focus_project = self.resume_agent.focus_project(resolved.focus.project_id)
        ov = ctx.overview
        bundle.candidate_name = ov.get("name")
        bundle.headline = ov.get("headline")
        bundle.verified_skills = [s.name for s in (ctx.profile.skills if ctx.profile else []) if s.verified]
        bundle.job_title, bundle.job_keywords = self._job_fields()
        bundle.agents_used = plan.agents
        return bundle

    async def run(
        self,
        question_text: str,
        *,
        cls: Classification | None = None,
        resolved: ResolvedQuestion | None = None,
        question_row: Question | None = None,
        variant: str = "default",
        stt_ms: float | None = None,
        persist: bool = True,
    ) -> AsyncIterator[dict]:
        """Full question -> answer pipeline. Yields WebSocket-ready events."""
        ctx = self.ctx
        lat = LatencyBreakdown(stt_ms=stt_ms)
        t0 = time.perf_counter()
        mgr = ctx.manager

        if cls is None:
            with Timer() as t:
                cls = mgr.classify_utterance(question_text) if mgr else _classify_standalone(question_text, ctx.profile)
            lat.classification_ms = t.ms
        if resolved is None:
            with Timer() as t:
                if mgr:
                    resolved = mgr.update_context(question_text, cls)
                else:
                    # stateless request: still resolve which project / role the question is about
                    from app.agents.conversation import ProfileIndex, ReferenceResolver
                    from app.models.domain import FocusState

                    resolved = ReferenceResolver(ProfileIndex.from_profile(ctx.profile)).resolve(
                        question_text, cls, FocusState())
            lat.resolution_ms = t.ms

        if question_row is None and persist:
            question_row = Question(
                session_id=ctx.session.id if ctx.session else None, user_id=ctx.user.id, text=question_text,
                resolved_text=resolved.resolved, qtype=cls.type.value, topic=cls.topic, difficulty=cls.difficulty,
                classification=cls.model_dump(mode="json"), focus=resolved.focus.model_dump(),
            )
            ctx.db.add(question_row)
            ctx.db.flush()

        yield {"event": "question.classified", "question_id": question_row.id if question_row else None,
               "classification": cls.model_dump(mode="json"), "resolved": resolved.resolved,
               "is_follow_up": resolved.is_follow_up, "aspect": resolved.aspect, "references": resolved.references,
               "focus": {"project": resolved.focus.project_name, "experience": resolved.focus.experience_name,
                         "technology": resolved.focus.technology, "topic": resolved.focus.topic}}

        plan = plan_for(cls, resolved, has_job=ctx.job is not None)
        if not plan.answer:
            yield {"event": "answer.skipped", "question_id": question_row.id if question_row else None,
                   "reason": f"{cls.type.value.lower()} - no answer needed"}
            return

        yield {"event": "status", "state": "PROCESSING", "agents": plan.agents}
        bundle = await self.gather_context(resolved.resolved, cls, resolved, plan)
        lat.retrieval_ms = bundle.retrieval_ms
        sources = _sources(bundle)
        yield {"event": "context.ready", "question_id": question_row.id if question_row else None,
               "agents": plan.agents, "retrieval_ms": bundle.retrieval_ms,
               "sources": [s.model_dump() for s in sources],
               "focus_project": bundle.focus_project.name if bundle.focus_project else None}

        recent = mgr.short_term_memory() if mgr else []
        summaries = mgr.retrieve_relevant_history() if mgr else []
        state: dict[str, Any] = {}
        yield {"event": "answer.start", "question_id": question_row.id if question_row else None, "variant": variant}
        gen_start = time.perf_counter()
        budget = None
        if ctx.reference_words and variant == "shorter":
            budget = max(25, int(ctx.reference_words * 0.6))
        elif ctx.reference_words and variant == "longer":
            budget = int(ctx.reference_words * 1.6)
        async for item in self.answer_agent.stream(question_text, resolved, cls, bundle, ctx.overview, recent,
                                                   summaries, ctx.length, variant, state, budget):
            if "reset" in item:
                yield {"event": "answer.reset", "reason": item["reset"]}
            else:
                yield {"event": "answer.delta", "text": item["delta"]}
        lat.generation_ms = round((time.perf_counter() - gen_start) * 1000, 2)
        lat.first_token_ms = state.get("first_token_ms")

        with Timer() as t:
            final_text, grounding = self.validator.validate(state["text"], bundle)
        lat.grounding_ms = t.ms

        wc = word_count(final_text)
        secs = wc / 2.5
        result = AnswerResult(
            text=final_text, key_points=state.get("key_points", []), star=state.get("star", {}),
            structure=structure_for(cls), sources=sources, grounding=grounding, word_count=wc,
            speaking_seconds=round(secs, 1), speaking_range=(max(1, int(secs * 0.9)), int(round(secs * 1.1))),
            insufficient_context=bool(state.get("insufficient")) or final_text.startswith("Your "),
            variant=variant, length=ctx.length, model=state.get("model", ""), notes=state.get("notes", []),
            degraded=bool(state.get("degraded")),
        )
        if grounding.removed_claims:
            result.notes.append(f"{len(grounding.removed_claims)} unsupported statement(s) were removed because "
                                "your profile doesn't back them up.")

        with Timer() as t:
            scores = evaluate_answer(
                question_text, final_text, cls, grounding, job_keywords=bundle.job_keywords, length=ctx.length,
                insufficient_context=result.insufficient_context,
                technical_evidence=[c.text for c in bundle.technical],
            )
        lat.evaluation_ms = t.ms
        # a "your profile lacks this" coaching answer has no meaningful job relevance
        result.job_relevance = None if result.insufficient_context else scores.job_alignment
        lat.total_ms = round((time.perf_counter() - t0) * 1000 + (stt_ms or 0), 2)

        answer_row = None
        if persist and question_row is not None:
            answer_row = Answer(
                question_id=question_row.id, user_id=ctx.user.id, variant=variant, length_target=ctx.length,
                text=final_text, word_count=wc, speaking_seconds=result.speaking_seconds,
                key_points=result.key_points, star=result.star, sources=[s.model_dump() for s in sources],
                grounding=grounding.model_dump(mode="json"), latency=lat.model_dump(),
                insufficient_context=result.insufficient_context, model=result.model,
            )
            ctx.db.add(answer_row)
            ctx.db.flush()
            ctx.db.add(AnswerEvaluation(answer_id=answer_row.id, scores=scores.model_dump(mode="json"),
                                        overall=scores.overall, method=scores.method, notes=scores.notes))
            ctx.db.flush()

        log.info("answer generated", extra={
            "latency_ms": lat.total_ms, "model": result.model, "retrieval_count": len(sources),
            "grounding_score": grounding.grounding_score, "tokens_in": state.get("tokens_in"),
            "tokens_out": state.get("tokens_out"), "stage": cls.type.value,
        })
        yield {
            "event": "answer.complete",
            "question_id": question_row.id if question_row else None,
            "answer_id": answer_row.id if answer_row else None,
            "answer": result.model_dump(mode="json"),
            "grounding_score": grounding.grounding_score,
            "evaluation": scores.model_dump(mode="json"),
            "latency": lat.model_dump(),
        }


def _classify_standalone(text: str, profile: CandidateProfile | None) -> Classification:
    from app.agents.classifier import ClassifierContext, classify

    names = [p.name for p in profile.projects] if profile else []
    orgs = [e.organization for e in profile.experiences if e.organization] if profile else []
    techs = {p.name: list(p.technologies or []) for p in profile.projects} if profile else {}
    return classify(text, ClassifierContext(project_names=names, experience_orgs=orgs, project_technologies=techs))


def load_job(db: Session, user_id: str, job_id: str | None) -> JobDescription | None:
    if not job_id:
        return None
    job = db.get(JobDescription, job_id)
    return job if job and job.user_id == user_id else None


def load_profile(db: Session, user_id: str) -> CandidateProfile | None:
    return db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))


def after_answer(db: Session, mgr: ConversationManager, user_id: str, session_id: str, question: str,
                 answer_text: str, turn_id: str, topic: str | None, project: str | None,
                 weak_topics: list[str]) -> dict | None:
    """Post-answer bookkeeping: short-term memory, history index, summaries."""
    mgr.record_answer(answer_text)
    index_history_turn(db, user_id, session_id, turn_id, question, answer_text, topic, project)
    return mgr.maybe_summarize(weak_topics)
