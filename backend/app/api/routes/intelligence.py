"""Stateless intelligence endpoints: classify, generate, evaluate."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agents.classifier import ClassifierContext, classify
from app.agents.evaluation import evaluate_answer, llm_judge
from app.agents.supervisor import (
    InterviewPipeline,
    PipelineContext,
    build_overview,
    candidate_entities,
    load_job,
    load_profile,
)
from app.agents.validation import GroundingValidator
from app.api.deps import DB, CurrentUser
from app.models.domain import Classification, ContextBundle
from app.rag.retriever import HybridRetriever

router = APIRouter(tags=["intelligence"])


class ClassifyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/questions/classify", response_model=Classification)
def classify_question(body: ClassifyBody, db: DB, user: CurrentUser) -> Classification:
    profile = load_profile(db, user.id)
    ctx = ClassifierContext(
        project_names=[p.name for p in profile.projects] if profile else [],
        experience_orgs=[e.organization for e in profile.experiences if e.organization] if profile else [],
        project_technologies={p.name: list(p.technologies or []) for p in profile.projects} if profile else {},
    )
    return classify(body.text, ctx)


class GenerateBody(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    job_id: str | None = None
    answer_length: Literal["20s", "45s", "90s", "detailed"] = "45s"
    variant: Literal["default", "regenerate", "shorter", "longer", "technical", "natural"] = "default"


@router.post("/answers/generate")
async def generate_answer(body: GenerateBody, db: DB, user: CurrentUser) -> dict[str, Any]:
    """One-shot grounded answer (no session context). Used by practice mode's
    'show model answer' and by API clients. Live sessions stream over the WebSocket."""
    profile = load_profile(db, user.id)
    ctx = PipelineContext(db=db, user=user, profile=profile, job=load_job(db, user.id, body.job_id),
                          length=body.answer_length, overview=build_overview(profile))
    result: dict[str, Any] = {}
    async for ev in InterviewPipeline(ctx).run(body.question, variant=body.variant, persist=False):
        if ev["event"] == "question.classified":
            result["classification"] = ev["classification"]
            result["resolved"] = ev["resolved"]
        elif ev["event"] == "answer.complete":
            result.update(answer=ev["answer"], evaluation=ev["evaluation"], latency=ev["latency"])
        elif ev["event"] == "answer.skipped":
            result["skipped"] = ev["reason"]
    db.rollback()
    return result


class EvaluateBody(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    answer: str = Field(min_length=1, max_length=12000)
    job_id: str | None = None
    answer_length: Literal["20s", "45s", "90s", "detailed"] = "45s"


async def evaluate_candidate_answer(db, user, question: str, answer: str, job_id: str | None,
                                    length: str) -> dict[str, Any]:
    profile = load_profile(db, user.id)
    names = [p.name for p in profile.projects] if profile else []
    techs = {p.name: list(p.technologies or []) for p in profile.projects} if profile else {}
    cls = classify(question, ClassifierContext(project_names=names, project_technologies=techs))
    retriever = HybridRetriever(db, user.id)
    bundle = ContextBundle(evidence_pool=retriever.evidence_pool(None))
    _, grounding = GroundingValidator(candidate_entities(profile)).validate(answer, bundle, rewrite=False)
    job = load_job(db, user.id, job_id)
    kws = list((job.parsed or {}).get("required_skills", [])) if job else []
    from app.rag.keyword import global_technical_index

    tech_refs = [d.text for d, _ in global_technical_index().search(question, limit=3)]
    scores = evaluate_answer(question, answer, cls, grounding, job_keywords=kws, length=length,
                             technical_evidence=tech_refs, is_candidate_spoken=True)
    feedback = list(scores.notes)
    judge = await llm_judge(question, answer)
    if judge:
        feedback += [str(f) for f in judge.get("feedback", [])][:5]
        scores.method = "heuristic-v1+llm-judge"
        for k in ("relevance", "correctness", "completeness"):
            if isinstance(judge.get(k), int | float):
                setattr(scores, k, round((getattr(scores, k) + float(judge[k])) / 2, 3))
    unsupported = [c.text for c in grounding.claims if c.kind == "candidate" and not c.supported]
    if unsupported:
        feedback.append("Some statements about your own experience aren't in your verified profile - make sure they're accurate, "
                        "or add them to your profile.")
    if scores.completeness < 0.5:
        feedback.append("Cover the expected structure for this question type (for example STAR for behavioural questions).")
    if scores.conciseness < 0.5:
        feedback.append("Adjust the length closer to the target speaking time.")
    return {"classification": cls.model_dump(mode="json"), "scores": scores.model_dump(mode="json"),
            "grounding": grounding.model_dump(mode="json"), "claims_not_in_profile": unsupported[:5],
            "feedback": list(dict.fromkeys(feedback))}


@router.post("/answers/evaluate")
async def evaluate(body: EvaluateBody, db: DB, user: CurrentUser) -> dict[str, Any]:
    return await evaluate_candidate_answer(db, user, body.question, body.answer, body.job_id, body.answer_length)
