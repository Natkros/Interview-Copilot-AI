"""Post-interview reports, cross-session analytics and the adaptive
preparation plan."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (
    Answer,
    AnswerEvaluation,
    ConversationTurn,
    InterviewReport,
    InterviewSession,
    PracticeAttempt,
    Question,
    QuestionBankItem,
)
from app.models.domain import TECHNICAL_TYPES

TECH_TYPE_VALUES = {t.value for t in TECHNICAL_TYPES}

STUDY_TOPICS: dict[str, list[str]] = {
    "SYSTEM_DESIGN": ["Requirements and estimation", "Load balancing and caching", "Database scaling (replicas, sharding)",
                      "Queues and asynchronous processing", "Consistency vs availability trade-offs"],
    "DSA": ["Hash maps and two pointers", "Binary search on answer space", "BFS/DFS and shortest paths",
            "Dynamic programming patterns", "Stating time/space complexity"],
    "DATABASE": ["Indexes and query plans", "Transactions and isolation levels", "Normalization vs denormalization",
                 "SQL vs NoSQL trade-offs"],
    "RAG": ["Chunking strategies", "Hybrid retrieval and re-ranking", "Retrieval evaluation (Recall@K)",
            "Grounding and hallucination control"],
    "GENERATIVE_AI": ["Prompt design", "RAG vs fine-tuning", "LLM evaluation", "Latency and cost optimisation"],
    "MACHINE_LEARNING": ["Bias-variance and regularisation", "Metrics for imbalanced data", "Cross-validation",
                         "Debugging production models"],
    "DEEP_LEARNING": ["Backpropagation", "Attention and transformers", "Training stability"],
    "CLOUD": ["Core AWS services", "Highly available deployments", "Serverless vs servers"],
    "DEVOPS": ["Docker images and containers", "CI/CD pipeline stages", "Kubernetes basics"],
    "SECURITY": ["Authentication vs authorisation", "OWASP top risks", "Secrets management"],
    "BEHAVIORAL": ["Prepare 4-5 STAR stories from your projects", "Quantify results you actually measured",
                   "Practice conflict and failure stories"],
    "HR": ["Motivation for the role", "Strengths with evidence", "A genuine weakness and improvement plan"],
    "PROJECT": ["Explain each project in 60 seconds", "Record verified challenges/results in your profile",
                "Prepare 'why this technology' answers"],
    "RESUME": ["Be ready to expand on every resume bullet", "Know your exact contribution on team work"],
    "TECHNICAL": ["Core CS fundamentals (OOP, OS, networking)", "Explain concepts with a concrete example"],
}


def _rows(db: Session, session: InterviewSession) -> list[dict[str, Any]]:
    """Each question with its latest answer + evaluation."""
    qs = db.scalars(select(Question).where(Question.session_id == session.id).order_by(Question.created_at)).all()
    out = []
    for q in qs:
        ans = db.scalars(select(Answer).where(Answer.question_id == q.id).order_by(Answer.created_at.desc())).first()
        ev = db.scalars(select(AnswerEvaluation).where(AnswerEvaluation.answer_id == ans.id)).first() if ans else None
        out.append({"q": q, "a": ans, "e": ev})
    return out


def _aware(dt):
    """SQLite returns naive datetimes; everything is stored in UTC."""
    from datetime import UTC

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 3) if vals else None


def build_report(db: Session, session: InterviewSession) -> dict[str, Any]:
    rows = _rows(db, session)
    turns = db.scalars(select(ConversationTurn).where(ConversationTurn.session_id == session.id)
                       .order_by(ConversationTurn.seq)).all()
    mock = session.mode != "live_coaching"
    # In mock modes, score what the candidate actually said; in live coaching,
    # score the suggested answers (and any spoken candidate turns).
    spoken = [t for t in turns if t.role == "candidate" and t.kind == "spoken" and (t.meta or {}).get("evaluation")]
    spoken_scores = [t.meta["evaluation"] for t in spoken]

    by_topic: dict[str, list[float]] = defaultdict(list)
    tech, comm, resume_k, problem, conf, job, grounding = [], [], [], [], [], [], []
    missed, halluc = [], []
    for r in rows:
        q, a, e = r["q"], r["a"], r["e"]
        if q.qtype in ("STATEMENT", "FEEDBACK", "GREETING"):
            continue
        if a is None or e is None:
            missed.append({"question": q.text, "reason": "not answered"})
            continue
        s = e.scores
        topic = q.topic or q.qtype
        by_topic[topic].append(e.overall)
        if q.qtype in TECH_TYPE_VALUES:
            tech.append(s.get("correctness"))
        if q.qtype in TECH_TYPE_VALUES or q.qtype == "SYSTEM_DESIGN":
            problem.append(s.get("completeness"))
        comm.append(mean([s.get("clarity", 0), s.get("naturalness", 0), s.get("conciseness", 0)]))
        if q.qtype in ("PROJECT", "RESUME", "INTRODUCTION", "FOLLOW_UP", "BEHAVIORAL"):
            resume_k.append(s.get("grounding"))
        conf.append(s.get("confidence"))
        if s.get("job_alignment") is not None:
            job.append(s.get("job_alignment"))
        if (a.grounding or {}).get("grounding_score") is not None:
            grounding.append(a.grounding["grounding_score"])
        if a.insufficient_context or e.overall < 0.5:
            missed.append({"question": q.text, "reason": "profile lacked information" if a.insufficient_context else "low score",
                           "score": e.overall})
        g = a.grounding or {}
        if g.get("unsupported_claims") or g.get("removed_claims"):
            halluc.append({"question": q.text, "removed_claims": g.get("removed_claims", []),
                           "unsupported": g.get("unsupported_claims", 0)})

    if spoken_scores:
        overall = _avg([s["overall"] for s in spoken_scores])
        comm = [mean([s["clarity"], s["naturalness"], s["conciseness"]]) for s in spoken_scores]
        conf = [s["confidence"] for s in spoken_scores]
        for t, s in zip(spoken, spoken_scores, strict=True):
            topic = (t.meta or {}).get("topic") or "GENERAL"
            by_topic[topic].append(s["overall"])
            if s["overall"] < 0.5:
                missed.append({"question": (t.meta or {}).get("question", ""), "reason": "weak answer", "score": s["overall"]})
    else:
        overall = _avg([r["e"].overall for r in rows if r["e"]])

    topic_avgs = {k: round(mean(v), 3) for k, v in by_topic.items()}
    strong = [k for k, v in sorted(topic_avgs.items(), key=lambda kv: -kv[1]) if v >= 0.75][:5]
    weak = [k for k, v in sorted(topic_avgs.items(), key=lambda kv: kv[1]) if v < 0.6][:5]
    study = []
    for w in weak:
        study += [{"topic": w, "item": it} for it in STUDY_TOPICS.get(w, [])[:3]]
    if not study and missed:
        study = [{"topic": "PROJECT", "item": it} for it in STUDY_TOPICS["PROJECT"][:2]]
    duration = None
    if session.started_at and session.ended_at:
        duration = round((_aware(session.ended_at) - _aware(session.started_at)).total_seconds())
    latencies = [r["a"].latency.get("total_ms") for r in rows if r["a"] and r["a"].latency]
    return {
        "session_id": session.id, "title": session.title, "mode": session.mode,
        "scored_on": "candidate_answers" if spoken_scores else "suggested_answers",
        "summary": {
            "duration_seconds": duration,
            "questions": sum(1 for r in rows if r["q"].qtype not in ("STATEMENT", "FEEDBACK")),
            "turns": len(turns), "topics": topic_avgs,
            "avg_latency_ms": _avg([x for x in latencies if x is not None]),
        },
        "scores": {
            "overall": overall, "technical": _avg(tech), "communication": _avg(comm), "resume_knowledge": _avg(resume_k),
            "problem_solving": _avg(problem), "confidence": _avg(conf), "job_alignment": _avg(job),
            "grounding": _avg(grounding),
        },
        "strong_areas": strong, "weak_areas": weak, "questions_missed": missed[:15],
        "hallucination_events": halluc, "recommended_study": study[:10],
        "notes": ["Scores are produced by the heuristic-v1 evaluator (see docs) - use them to compare sessions, not as absolute grades."]
        + (["Mock-interview scores reflect your own spoken answers."] if mock and spoken_scores else []),
    }


def save_report(db: Session, session: InterviewSession) -> InterviewReport:
    data = build_report(db, session)
    rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == session.id))
    if rep is None:
        rep = InterviewReport(session_id=session.id, user_id=session.user_id, data=data)
        db.add(rep)
    else:
        rep.data = data
    db.flush()
    return rep


def analytics(db: Session, user_id: str) -> dict[str, Any]:
    sessions = db.scalars(select(InterviewSession).where(InterviewSession.user_id == user_id)
                          .order_by(InterviewSession.created_at)).all()
    qs = db.scalars(select(Question).where(Question.user_id == user_id)).all()
    q_by_id = {q.id: q for q in qs}
    answers = db.scalars(select(Answer).where(Answer.user_id == user_id)).all()
    latest: dict[str, Answer] = {}
    for a in answers:
        if a.question_id not in latest or a.created_at > latest[a.question_id].created_at:
            latest[a.question_id] = a
    evals = {e.answer_id: e for e in db.scalars(select(AnswerEvaluation).where(
        AnswerEvaluation.answer_id.in_([a.id for a in latest.values()]))).all()} if latest else {}

    topic_dist: dict[str, int] = defaultdict(int)
    diff_dist: dict[str, int] = defaultdict(int)
    topic_scores: dict[str, list[float]] = defaultdict(list)
    tech_acc, grounding, conf, lengths, lat = [], [], [], [], []
    for qid, a in latest.items():
        q = q_by_id.get(qid)
        if q is None or q.qtype in ("STATEMENT", "FEEDBACK", "GREETING"):
            continue
        topic_dist[q.topic or q.qtype] += 1
        diff_dist[q.difficulty] += 1
        e = evals.get(a.id)
        lengths.append(a.word_count)
        if a.latency and a.latency.get("total_ms") is not None:
            lat.append(a.latency["total_ms"])
        if a.grounding and a.grounding.get("grounding_score") is not None:
            grounding.append(a.grounding["grounding_score"])
        if e:
            topic_scores[q.topic or q.qtype].append(e.overall)
            conf.append(e.scores.get("confidence"))
            if q.qtype in TECH_TYPE_VALUES:
                tech_acc.append(e.scores.get("correctness"))

    attempts = db.scalars(select(PracticeAttempt).where(PracticeAttempt.user_id == user_id)
                          .order_by(PracticeAttempt.created_at)).all()
    bank = {b.id: b for b in db.scalars(select(QuestionBankItem).where(QuestionBankItem.user_id == user_id))}
    for at in attempts:
        item = bank.get(at.question_bank_id)
        if item:
            topic_scores[item.category.upper().replace(" ", "_")].append(at.overall)

    progress = []
    for s in sessions:
        rep = db.scalar(select(InterviewReport).where(InterviewReport.session_id == s.id))
        if rep and rep.data.get("scores", {}).get("overall") is not None:
            progress.append({"session_id": s.id, "title": s.title, "mode": s.mode,
                             "date": s.created_at.isoformat(), "overall": rep.data["scores"]["overall"],
                             "technical": rep.data["scores"].get("technical"),
                             "communication": rep.data["scores"].get("communication")})
    weak = sorted([(k, mean(v)) for k, v in topic_scores.items() if len(v) >= 2 and mean(v) < 0.6], key=lambda kv: kv[1])
    return {
        "questions_answered": len(lengths) + len(attempts),
        "sessions": len(sessions),
        "practice_attempts": len(attempts),
        "avg_response_ms": _avg(lat),
        "avg_answer_words": _avg([float(x) for x in lengths]),
        "technical_accuracy": _avg(tech_acc),
        "grounding": _avg(grounding),
        "confidence": _avg(conf),
        "difficulty_distribution": dict(diff_dist),
        "topic_distribution": dict(topic_dist),
        "topic_scores": {k: round(mean(v), 3) for k, v in topic_scores.items()},
        "weak_topics": [{"topic": k, "avg": round(v, 3)} for k, v in weak[:6]],
        "progress": progress,
        "preparation_plan": preparation_plan([k for k, _ in weak[:3]]),
    }


def preparation_plan(weak_topics: list[str]) -> list[dict[str, Any]]:
    plan = []
    for t in weak_topics:
        key = t.upper().replace(" ", "_")
        key = {"DATABASES": "DATABASE", "ML": "MACHINE_LEARNING", "DL": "DEEP_LEARNING", "GENAI": "GENERATIVE_AI",
               "PROJECTS": "PROJECT"}.get(key, key)
        plan.append({
            "topic": t,
            "steps": [
                {"step": "Study", "detail": ", ".join(STUDY_TOPICS.get(key, ["Review fundamentals"])[:3])},
                {"step": "Practice", "detail": f"Answer 3 {t.replace('_', ' ').title()} questions in Practice mode"},
                {"step": "Evaluate", "detail": "Check your scores and the feedback on each attempt"},
                {"step": "Increase difficulty", "detail": "Move to harder questions once you average 75%+"},
            ],
        })
    return plan
