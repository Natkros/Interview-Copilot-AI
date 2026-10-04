"""Settings, privacy (export / delete) and system status."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.api.deps import DB, CurrentUser, audit
from app.core.config import get_settings
from app.database.models import (
    Answer,
    AnswerEvaluation,
    AuditLog,
    ConversationTurn,
    Document,
    InterviewReport,
    InterviewSession,
    JobDescription,
    PracticeAttempt,
    Question,
    QuestionBankItem,
    User,
)
from app.rag.vectorstore import get_vector_store, vector_store_status
from app.services.profile import canonical_profile, get_or_create_profile, serialize_profile
from app.speech.stt import stt_config
from app.websocket.live import _runners

router = APIRouter(tags=["account"])


class Preferences(BaseModel):
    answer_length: Literal["20s", "45s", "90s", "detailed"] | None = None
    store_recordings: bool | None = None
    font_scale: float | None = None
    high_contrast: bool | None = None
    reduced_motion: bool | None = None
    interviewer_voice: bool | None = None
    default_job_id: str | None = None


@router.get("/settings")
def get_settings_route(user: CurrentUser) -> dict[str, Any]:
    return {"preferences": user.preferences or {}}


@router.patch("/settings")
def patch_settings(body: Preferences, request: Request, db: DB, user: CurrentUser) -> dict[str, Any]:
    prefs = dict(user.preferences or {})
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "font_scale" and v is not None:
            v = max(0.85, min(1.5, v))
        prefs[k] = v
    user.preferences = prefs
    audit(db, request, user.id, "settings.update", None, keys=list(body.model_dump(exclude_unset=True)))
    db.commit()
    return {"preferences": prefs}


@router.get("/system/status")
def system_status() -> dict[str, Any]:
    s = get_settings()
    return {
        "llm": {"provider": s.resolved_llm_provider, "model": s.llm_model if s.resolved_llm_provider == "anthropic" else "offline-extractive-v1"},
        "stt": stt_config(),
        "embeddings": {"provider": s.embedding_provider if (s.embedding_provider == "hash" or s.embedding_api_key) else "hash"},
        "vector_store": vector_store_status(),
        "store_raw_audio": s.store_raw_audio,
    }


@router.get("/privacy/export")
def export_data(request: Request, db: DB, user: CurrentUser) -> Response:
    """Everything stored about the user, as JSON."""
    import json

    profile = get_or_create_profile(db, user)
    sessions = []
    for s in db.scalars(select(InterviewSession).where(InterviewSession.user_id == user.id)):
        turns = db.scalars(select(ConversationTurn).where(ConversationTurn.session_id == s.id).order_by(ConversationTurn.seq)).all()
        rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
        sessions.append({"id": s.id, "mode": s.mode, "title": s.title, "created_at": s.created_at.isoformat(),
                         "turns": [{"role": t.role, "kind": t.kind, "text": t.text, "at": t.created_at.isoformat()} for t in turns],
                         "report": rep.data if rep else None})
    data = {
        "account": {"id": user.id, "email": user.email, "created_at": user.created_at.isoformat(), "preferences": user.preferences},
        "profile": serialize_profile(profile),
        "canonical_profile": canonical_profile(profile),
        "documents": [{"id": d.id, "kind": d.kind, "filename": d.filename, "text": d.text, "uploaded_at": d.created_at.isoformat()}
                      for d in db.scalars(select(Document).where(Document.user_id == user.id))],
        "jobs": [{"id": j.id, "title": j.title, "company": j.company, "text": j.raw_text, "parsed": j.parsed, "match": j.match}
                 for j in db.scalars(select(JobDescription).where(JobDescription.user_id == user.id))],
        "interviews": sessions,
        "practice": [{"question_bank_id": p.question_bank_id, "answer": p.answer_text, "scores": p.scores,
                      "at": p.created_at.isoformat()}
                     for p in db.scalars(select(PracticeAttempt).where(PracticeAttempt.user_id == user.id))],
        "recordings": "Raw audio is not stored." if not get_settings().store_raw_audio else "See recordings directory.",
    }
    audit(db, request, user.id, "privacy.export")
    db.commit()
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="interviewos-export.json"'})


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(request: Request, response: Response, db: DB, user: CurrentUser) -> Response:
    """Permanently delete the account and all associated data, including vectors."""
    uid = user.id
    for sid, runner in list(_runners.items()):
        if runner.user_id == uid:
            await runner.stop()
            _runners.pop(sid, None)
    store = get_vector_store()
    if store is not None:
        store.delete_candidate(uid)
    from app.speech.recording import delete_recording

    for sess in db.scalars(select(InterviewSession).where(InterviewSession.user_id == uid)):
        delete_recording(sess.id)
    # explicit order for tables whose FKs don't all cascade through users
    q_ids = [q.id for q in db.scalars(select(Question).where(Question.user_id == uid))]
    s_ids = [s.id for s in db.scalars(select(InterviewSession).where(InterviewSession.user_id == uid))]
    if s_ids:
        db.execute(delete(ConversationTurn).where(ConversationTurn.session_id.in_(s_ids)))
    if q_ids:
        a_ids = [a.id for a in db.scalars(select(Answer).where(Answer.question_id.in_(q_ids)))]
        if a_ids:
            db.execute(delete(AnswerEvaluation).where(AnswerEvaluation.answer_id.in_(a_ids)))
            db.execute(delete(Answer).where(Answer.id.in_(a_ids)))
        db.execute(delete(Question).where(Question.id.in_(q_ids)))
    db.execute(delete(PracticeAttempt).where(PracticeAttempt.user_id == uid))
    db.execute(delete(QuestionBankItem).where(QuestionBankItem.user_id == uid))
    db.execute(delete(InterviewReport).where(InterviewReport.user_id == uid))
    db.execute(delete(InterviewSession).where(InterviewSession.user_id == uid))
    db.execute(delete(JobDescription).where(JobDescription.user_id == uid))
    db.add(AuditLog(user_id=None, action="account.delete", target=uid, meta={}))
    db.delete(db.get(User, uid))
    db.commit()
    s = get_settings()
    response.delete_cookie(s.cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
