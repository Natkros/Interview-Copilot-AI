from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from app.agents.mock_interviewer import MOCK_MODES
from app.api.deps import DB, CurrentUser, audit, owned
from app.core.security import ws_tickets
from app.database.models import (
    Answer,
    AnswerEvaluation,
    ConversationTurn,
    InterviewReport,
    InterviewSession,
    JobDescription,
    Question,
    SessionSummary,
)
from app.rag.indexer import remove_session_history
from app.services.reports import save_report
from app.speech.recording import delete_recording, recording_path
from app.speech.stt import stt_config
from app.websocket.live import _runners, turn_payload

router = APIRouter(prefix="/interviews", tags=["interviews"])

Mode = Literal["live_coaching", "mock", "resume_drill", "technical", "dsa", "system_design", "behavioral", "hr",
               "job_specific"]
MODE_LABELS = {
    "live_coaching": "Live Coaching", "mock": "Mock Interview", "resume_drill": "Resume Drill",
    "technical": "Technical Interview", "dsa": "DSA Interview", "system_design": "System Design",
    "behavioral": "Behavioral", "hr": "HR", "job_specific": "Job-Specific",
}


class CreateInterview(BaseModel):
    mode: Mode = "live_coaching"
    title: str | None = Field(default=None, max_length=200)
    job_id: str | None = None
    answer_length: Literal["20s", "45s", "90s", "detailed"] = "45s"
    consent_acknowledged: bool = Field(
        default=False, description="Live coaching: the user confirms AI assistance is permitted in this setting.")


def _session_out(s: InterviewSession, db=None) -> dict[str, Any]:
    out = {"id": s.id, "mode": s.mode, "mode_label": MODE_LABELS.get(s.mode, s.mode), "title": s.title,
           "status": s.status, "job_id": s.job_id, "answer_length": s.answer_length,
           "created_at": s.created_at.isoformat(),
           "started_at": s.started_at.isoformat() if s.started_at else None,
           "ended_at": s.ended_at.isoformat() if s.ended_at else None}
    if db is not None:
        out["turn_count"] = db.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.session_id == s.id))
        rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
        out["overall"] = rep.data.get("scores", {}).get("overall") if rep else None
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def create_interview(body: CreateInterview, request: Request, db: DB, user: CurrentUser) -> dict[str, Any]:
    if body.mode == "live_coaching" and not body.consent_acknowledged:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Live coaching requires confirming that AI assistance is permitted in this interview "
                            "(practice, coaching, accessibility or an explicitly AI-permitted interview).")
    if body.job_id:
        owned(db, JobDescription, body.job_id, user)
    s = InterviewSession(user_id=user.id, mode=body.mode, title=(body.title or MODE_LABELS[body.mode])[:200],
                         job_id=body.job_id, answer_length=body.answer_length,
                         state={"consent": body.consent_acknowledged})
    db.add(s)
    audit(db, request, user.id, "interview.create", None, mode=body.mode)
    db.commit()
    return _session_out(s)


@router.get("")
def list_interviews(db: DB, user: CurrentUser, limit: int = Query(50, le=200), offset: int = 0) -> list[dict[str, Any]]:
    rows = db.scalars(select(InterviewSession).where(InterviewSession.user_id == user.id)
                      .order_by(InterviewSession.created_at.desc()).limit(limit).offset(offset)).all()
    return [_session_out(s, db) for s in rows]


@router.get("/{session_id}")
def get_interview(session_id: str, db: DB, user: CurrentUser, before_seq: int | None = None,
                  limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    """Session with a page of turns (newest page first; pass before_seq to page back)."""
    s = owned(db, InterviewSession, session_id, user)
    q = select(ConversationTurn).where(ConversationTurn.session_id == s.id)
    if before_seq:
        q = q.where(ConversationTurn.seq < before_seq)
    turns = db.scalars(q.order_by(ConversationTurn.seq.desc()).limit(limit)).all()
    answers = {}
    ids = [t.answer_id for t in turns if t.answer_id]
    if ids:
        for a in db.scalars(select(Answer).where(Answer.id.in_(ids))):
            ev = db.scalar(select(AnswerEvaluation).where(AnswerEvaluation.answer_id == a.id))
            answers[a.id] = _answer_out(a, ev)
    focus = (s.state or {}).get("focus", {})
    return {
        **_session_out(s, db),
        "turns": [turn_payload(t) for t in reversed(turns)],
        "has_more": len(turns) == limit,
        "answers": answers,
        "focus": {"project": focus.get("project_name"), "topic": focus.get("topic"), "technology": focus.get("technology")},
        "stt": stt_config(),
        "live": session_id in _runners,
        "has_recording": recording_path(s.id) is not None,
    }


def _answer_out(a: Answer, ev: AnswerEvaluation | None) -> dict[str, Any]:
    return {"id": a.id, "question_id": a.question_id, "variant": a.variant, "text": a.text, "word_count": a.word_count,
            "speaking_seconds": a.speaking_seconds, "key_points": a.key_points, "star": a.star, "sources": a.sources,
            "grounding": a.grounding, "latency": a.latency, "insufficient_context": a.insufficient_context,
            "model": a.model, "created_at": a.created_at.isoformat(),
            "evaluation": ev.scores if ev else None}


@router.post("/{session_id}/ticket")
def ws_ticket(session_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    """Short-lived single-use ticket for opening the live WebSocket."""
    s = owned(db, InterviewSession, session_id, user)
    if s.status == "ended":
        raise HTTPException(status.HTTP_409_CONFLICT, "This interview has ended")
    return {"ticket": ws_tickets.issue(user.id, s.id), "path": f"/interviews/{s.id}/live",
            "mock": s.mode in MOCK_MODES}


@router.get("/{session_id}/questions/{question_id}")
def question_detail(session_id: str, question_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    s = owned(db, InterviewSession, session_id, user)
    q = owned(db, Question, question_id, user)
    if q.session_id != s.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    answers = db.scalars(select(Answer).where(Answer.question_id == q.id).order_by(Answer.created_at)).all()
    return {"id": q.id, "text": q.text, "resolved_text": q.resolved_text, "classification": q.classification,
            "focus": q.focus,
            "answers": [_answer_out(a, db.scalar(select(AnswerEvaluation).where(AnswerEvaluation.answer_id == a.id))) for a in answers]}


@router.get("/{session_id}/search")
def search_transcript(session_id: str, db: DB, user: CurrentUser, q: str = Query(min_length=1, max_length=200)) -> dict[str, Any]:
    """Search questions, answers, projects, technologies and topics in a transcript."""
    s = owned(db, InterviewSession, session_id, user)
    term = q.strip()
    like = f"%{term.lower()}%"
    rows = db.scalars(select(ConversationTurn).where(ConversationTurn.session_id == s.id, or_(
        func.lower(ConversationTurn.text).like(like),
    )).order_by(ConversationTurn.seq)).all()
    # topic / classification matches
    topic_turn_ids = {t.id for t in db.scalars(select(ConversationTurn).where(ConversationTurn.session_id == s.id))
                      if term.upper() in str(((t.meta or {}).get("classification") or {}).get("topic") or "")
                      or term.upper() == str(((t.meta or {}).get("classification") or {}).get("type") or "")}
    extra = db.scalars(select(ConversationTurn).where(ConversationTurn.id.in_(topic_turn_ids))).all() if topic_turn_ids else []
    seen, hits = set(), []
    pattern = re.compile(re.escape(term), re.I)
    for t in sorted([*rows, *extra], key=lambda x: x.seq):
        if t.id in seen:
            continue
        seen.add(t.id)
        spans = [[m.start(), m.end()] for m in pattern.finditer(t.text)]
        hits.append({"turn_id": t.id, "seq": t.seq, "role": t.role, "text": t.text, "matches": spans})
    return {"query": term, "count": len(hits), "results": hits}


@router.post("/{session_id}/end")
async def end_interview(session_id: str, request: Request, db: DB, user: CurrentUser) -> dict[str, Any]:
    s = owned(db, InterviewSession, session_id, user)
    runner = _runners.get(session_id)
    if runner is not None:
        await runner.end()
        db.expire_all()
        rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
    else:
        from datetime import UTC, datetime

        s.status = "ended"
        s.ended_at = s.ended_at or datetime.now(UTC)
        rep = save_report(db, s)
    audit(db, request, user.id, "interview.end", session_id)
    db.commit()
    return {"report_id": rep.id if rep else None, "status": "ended"}


@router.get("/{session_id}/report")
def get_report(session_id: str, db: DB, user: CurrentUser, refresh: bool = False) -> dict[str, Any]:
    s = owned(db, InterviewSession, session_id, user)
    rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
    if rep is None or refresh:
        rep = save_report(db, s)
        db.commit()
    return {"id": rep.id, "created_at": rep.created_at.isoformat(), **rep.data}


@router.get("/{session_id}/recording")
def get_recording(session_id: str, db: DB, user: CurrentUser):
    from fastapi.responses import FileResponse

    s = owned(db, InterviewSession, session_id, user)
    path = recording_path(s.id)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recording stored for this interview")
    return FileResponse(path, media_type="audio/wav", filename=f"interview-{s.id}.wav")


@router.delete("/{session_id}/recording", status_code=status.HTTP_204_NO_CONTENT)
def remove_recording(session_id: str, request: Request, db: DB, user: CurrentUser) -> None:
    s = owned(db, InterviewSession, session_id, user)
    delete_recording(s.id)
    audit(db, request, user.id, "interview.recording_delete", session_id)
    db.commit()


@router.delete("/{session_id}/transcript", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcript(session_id: str, request: Request, db: DB, user: CurrentUser) -> None:
    s = owned(db, InterviewSession, session_id, user)
    db.execute(delete(ConversationTurn).where(ConversationTurn.session_id == s.id))
    db.execute(delete(SessionSummary).where(SessionSummary.session_id == s.id))
    remove_session_history(db, user.id, s.id)
    s.state = {k: v for k, v in (s.state or {}).items() if k == "consent"}
    audit(db, request, user.id, "interview.transcript_delete", session_id)
    db.commit()


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interview(session_id: str, request: Request, db: DB, user: CurrentUser) -> None:
    s = owned(db, InterviewSession, session_id, user)
    runner = _runners.pop(session_id, None)
    if runner:
        await runner.stop()
    remove_session_history(db, user.id, s.id)
    delete_recording(s.id)
    # questions reference the session; delete answers -> questions -> session explicitly
    q_ids = [q.id for q in db.scalars(select(Question).where(Question.session_id == s.id))]
    if q_ids:
        db.execute(delete(ConversationTurn).where(ConversationTurn.session_id == s.id))
        a_ids = [a.id for a in db.scalars(select(Answer).where(Answer.question_id.in_(q_ids)))]
        if a_ids:
            db.execute(delete(AnswerEvaluation).where(AnswerEvaluation.answer_id.in_(a_ids)))
            db.execute(delete(Answer).where(Answer.id.in_(a_ids)))
        db.execute(delete(Question).where(Question.id.in_(q_ids)))
    db.delete(s)
    audit(db, request, user.id, "interview.delete", session_id)
    db.commit()
