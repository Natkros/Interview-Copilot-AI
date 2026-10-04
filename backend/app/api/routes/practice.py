from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, owned
from app.api.routes.intelligence import evaluate_candidate_answer
from app.database.models import (
    InterviewReport,
    InterviewSession,
    JobDescription,
    PracticeAttempt,
    QuestionBankItem,
)
from app.services.profile import get_or_create_profile, serialize_profile
from app.services.question_bank import CATEGORIES, rebuild_question_bank, record_practice
from app.services.reports import analytics

router = APIRouter(tags=["practice"])


def _item(i: QuestionBankItem) -> dict[str, Any]:
    return {"id": i.id, "category": i.category, "question": i.question, "difficulty": i.difficulty,
            "expected_concepts": i.expected_concepts, "resume_relevance": i.resume_relevance,
            "job_relevance": i.job_relevance, "source": i.source,
            "last_practiced": i.last_practiced_at.isoformat() if i.last_practiced_at else None,
            "attempts": i.attempts, "performance": i.avg_score}


@router.get("/question-bank")
def question_bank(db: DB, user: CurrentUser, category: str | None = None,
                  difficulty: Literal["easy", "medium", "hard"] | None = None,
                  sort: Literal["relevance", "weakest", "recent", "difficulty"] = "relevance",
                  limit: int = Query(200, le=500)) -> dict[str, Any]:
    q = select(QuestionBankItem).where(QuestionBankItem.user_id == user.id)
    items = list(db.scalars(q))
    if not items:
        profile = get_or_create_profile(db, user)
        job = db.scalars(select(JobDescription).where(JobDescription.user_id == user.id)
                         .order_by(JobDescription.created_at.desc())).first()
        rebuild_question_bank(db, user.id, profile, job)
        db.commit()
        items = list(db.scalars(q))
    if category:
        items = [i for i in items if i.category == category]
    if difficulty:
        items = [i for i in items if i.difficulty == difficulty]
    order = {"easy": 0, "medium": 1, "hard": 2}
    if sort == "relevance":
        items.sort(key=lambda i: -(i.resume_relevance + i.job_relevance))
    elif sort == "weakest":
        items.sort(key=lambda i: (i.avg_score if i.avg_score is not None else 2.0))
    elif sort == "recent":
        items.sort(key=lambda i: i.last_practiced_at.timestamp() if i.last_practiced_at else 0, reverse=True)
    else:
        items.sort(key=lambda i: order.get(i.difficulty, 1))
    counts: dict[str, int] = {}
    for i in db.scalars(q):
        counts[i.category] = counts.get(i.category, 0) + 1
    return {"categories": [c for c in CATEGORIES if c in counts], "counts": counts, "items": [_item(i) for i in items[:limit]]}


@router.post("/question-bank/rebuild")
def rebuild(db: DB, user: CurrentUser, job_id: str | None = None) -> dict[str, Any]:
    job = owned(db, JobDescription, job_id, user) if job_id else None
    n = rebuild_question_bank(db, user.id, get_or_create_profile(db, user), job)
    db.commit()
    return {"count": n}


class PracticeBody(BaseModel):
    question_bank_id: str
    answer: str = Field(min_length=1, max_length=12000)
    job_id: str | None = None
    answer_length: Literal["20s", "45s", "90s", "detailed"] = "45s"


@router.post("/practice", status_code=status.HTTP_201_CREATED)
async def practice(body: PracticeBody, db: DB, user: CurrentUser) -> dict[str, Any]:
    item = owned(db, QuestionBankItem, body.question_bank_id, user)
    result = await evaluate_candidate_answer(db, user, item.question, body.answer, body.job_id, body.answer_length)
    # concept coverage against the item's expected concepts
    low = body.answer.lower()
    covered = [c for c in item.expected_concepts or [] if c.lower() in low]
    missing = [c for c in item.expected_concepts or [] if c.lower() not in low]
    if missing:
        result["feedback"].append("Consider mentioning: " + ", ".join(missing[:4]))
    overall = result["scores"]["overall"]
    attempt = PracticeAttempt(user_id=user.id, question_bank_id=item.id, answer_text=body.answer,
                              scores=result["scores"], overall=overall, feedback=result["feedback"])
    db.add(attempt)
    record_practice(item, overall)
    db.commit()
    return {"attempt_id": attempt.id, **result, "concepts_covered": covered, "concepts_missing": missing,
            "item": _item(item)}


@router.get("/practice/history")
def practice_history(db: DB, user: CurrentUser, limit: int = Query(50, le=200)) -> list[dict[str, Any]]:
    rows = db.scalars(select(PracticeAttempt).where(PracticeAttempt.user_id == user.id)
                      .order_by(PracticeAttempt.created_at.desc()).limit(limit)).all()
    bank = {b.id: b for b in db.scalars(select(QuestionBankItem).where(
        QuestionBankItem.id.in_([r.question_bank_id for r in rows])))} if rows else {}
    return [{"id": r.id, "question": bank[r.question_bank_id].question if r.question_bank_id in bank else None,
             "category": bank[r.question_bank_id].category if r.question_bank_id in bank else None,
             "overall": r.overall, "created_at": r.created_at.isoformat(), "feedback": r.feedback} for r in rows]


@router.get("/analytics")
def get_analytics(db: DB, user: CurrentUser) -> dict[str, Any]:
    return analytics(db, user.id)


@router.get("/dashboard")
def dashboard(db: DB, user: CurrentUser) -> dict[str, Any]:
    profile = get_or_create_profile(db, user)
    a = analytics(db, user.id)
    jobs = db.scalars(select(JobDescription).where(JobDescription.user_id == user.id)
                      .order_by(JobDescription.created_at.desc())).all()
    sessions = db.scalars(select(InterviewSession).where(InterviewSession.user_id == user.id)
                          .order_by(InterviewSession.created_at.desc()).limit(5)).all()
    recent = []
    for s in sessions:
        rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
        recent.append({"id": s.id, "title": s.title, "mode": s.mode, "status": s.status,
                       "created_at": s.created_at.isoformat(),
                       "overall": rep.data.get("scores", {}).get("overall") if rep else None})
    prof = serialize_profile(profile)
    topic_scores = a["topic_scores"]
    tech_keys = [k for k in topic_scores if k not in ("BEHAVIORAL", "HR", "INTRODUCTION", "GREETING", "PROJECT", "RESUME", "FOLLOW_UP")]
    tech = [topic_scores[k] for k in tech_keys]
    beh = [topic_scores[k] for k in ("BEHAVIORAL", "HR") if k in topic_scores]
    verification = prof["verification"]["ratio"]
    match = jobs[0].match.get("score") if jobs and jobs[0].match else None
    practiced = a["questions_answered"]
    # readiness: documented blend of profile verification, job match and measured performance
    parts = [(verification, 0.25)]
    if match is not None:
        parts.append((match, 0.25))
    perf = [x for x in [sum(tech) / len(tech) if tech else None, sum(beh) / len(beh) if beh else None] if x is not None]
    if perf:
        parts.append((sum(perf) / len(perf), 0.5))
    readiness = round(sum(v * w for v, w in parts) / sum(w for _, w in parts), 3) if parts else None
    recommended = []
    for w in a["weak_topics"][:3]:
        recommended.append({"topic": w["topic"], "reason": f"average score {round(w['avg'] * 100)}%"})
    if not recommended:
        if not profile.projects:
            recommended.append({"topic": "Profile", "reason": "add your projects so answers can be grounded"})
        elif verification < 0.6:
            recommended.append({"topic": "Profile", "reason": "verify extracted resume facts for stronger grounding"})
        else:
            recommended.append({"topic": "Projects", "reason": "practise explaining each project in under a minute"})
    return {
        "profile": {"name": profile.name, "verification": prof["verification"], "projects": len(profile.projects),
                    "skills": len(profile.skills), "kb_version": profile.kb_version},
        "resume_match": {"job_id": jobs[0].id, "title": jobs[0].title, "score": match} if jobs else None,
        "interview_readiness": readiness,
        "readiness_method": "25% profile verification + 25% latest job match + 50% measured answer performance (available parts only)",
        "technical_score": round(sum(tech) / len(tech), 3) if tech else None,
        "behavioral_score": round(sum(beh) / len(beh), 3) if beh else None,
        "questions_practiced": practiced,
        "recent_sessions": recent,
        "weak_topics": a["weak_topics"],
        "recommended_practice": recommended,
        "preparation_plan": a["preparation_plan"],
    }
