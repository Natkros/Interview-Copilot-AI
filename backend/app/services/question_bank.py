"""Personalised question bank: generated from the verified profile, the target
job description and a curated seed set; tracks practice performance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import CandidateProfile, JobDescription, QuestionBankItem
from app.services.taxonomy import find_technologies

CATEGORIES = ["Resume", "Projects", "Python", "C++", "Java", "DSA", "ML", "DL", "GenAI", "RAG", "Databases",
              "Cloud", "DevOps", "System Design", "Behavioral", "HR", "Job"]

_TECH_CATEGORY = {
    "Python": "Python", "C++": "C++", "Java": "Java", "Machine Learning": "ML", "scikit-learn": "ML",
    "XGBoost": "ML", "Deep Learning": "DL", "PyTorch": "DL", "TensorFlow": "DL", "Large Language Models": "GenAI",
    "Generative AI": "GenAI", "LangChain": "GenAI", "RAG": "RAG", "Qdrant": "RAG", "Embeddings": "RAG",
    "Vector Search": "RAG", "PostgreSQL": "Databases", "MySQL": "Databases", "MongoDB": "Databases",
    "SQL": "Databases", "Redis": "Databases", "AWS": "Cloud", "Azure": "Cloud", "GCP": "Cloud", "EC2": "Cloud",
    "Docker": "DevOps", "Kubernetes": "DevOps", "CI/CD": "DevOps",
}


def load_seed_questions() -> list[dict[str, Any]]:
    raw = resources.files("app.evaluation").joinpath("seed_questions.json").read_text(encoding="utf-8")
    return json.loads(raw)


def _upsert(db: Session, user_id: str, existing: dict[str, QuestionBankItem], **kw: Any) -> None:
    key = kw["question"].strip().lower()
    item = existing.get(key)
    if item is None:
        item = QuestionBankItem(user_id=user_id, **kw)
        db.add(item)
        existing[key] = item
    else:
        for k in ("category", "difficulty", "expected_concepts", "resume_relevance", "job_relevance", "source", "source_ref", "job_id"):
            if k in kw:
                setattr(item, k, kw[k])


def rebuild_question_bank(db: Session, user_id: str, profile: CandidateProfile | None,
                          job: JobDescription | None = None) -> int:
    existing = {i.question.strip().lower(): i for i in db.scalars(select(QuestionBankItem).where(QuestionBankItem.user_id == user_id))}
    job_terms = set()
    if job:
        parsed = job.parsed or {}
        job_terms = {t.lower() for t in [*(parsed.get("required_skills") or []), *(parsed.get("technologies") or []),
                                         *(parsed.get("preferred_skills") or [])]}

    def job_rel(concepts: list[str]) -> float:
        if not job_terms:
            return 0.0
        hits = sum(1 for c in concepts if c.lower() in job_terms)
        return round(min(1.0, hits / max(1, min(3, len(concepts)))), 2)

    verified_skills = {s.name for s in profile.skills} if profile else set()
    if profile:
        for p in profile.projects:
            techs = list(p.technologies or [])
            base = dict(category="Projects", source="resume", source_ref=p.id, resume_relevance=1.0)
            _upsert(db, user_id, existing, question=f"Tell me about your {p.name}.", difficulty="easy",
                    expected_concepts=techs[:5], job_relevance=job_rel(techs), **base)
            _upsert(db, user_id, existing, question=f"What was your role in the {p.name}?", difficulty="easy",
                    expected_concepts=["ownership", "contribution"], job_relevance=job_rel(techs), **base)
            for t in techs[:2]:
                _upsert(db, user_id, existing, question=f"Why did you choose {t} for the {p.name}?", difficulty="medium",
                        expected_concepts=[t, "trade-offs", "alternatives"], job_relevance=job_rel([t]), **base)
            _upsert(db, user_id, existing, question=f"What was the biggest challenge in the {p.name}?", difficulty="medium",
                    expected_concepts=["problem", "solution", "result"], job_relevance=job_rel(techs), **base)
            _upsert(db, user_id, existing, question=f"How would you improve or scale the {p.name}?", difficulty="hard",
                    expected_concepts=["scalability", "limitations", "trade-offs"], job_relevance=job_rel(techs), **base)
            _upsert(db, user_id, existing, question=f"How did you test the {p.name}?", difficulty="medium",
                    expected_concepts=["testing", "evaluation", "metrics"], job_relevance=job_rel(techs), **base)
        for e in profile.experiences:
            if not e.organization:
                continue
            techs = list(e.technologies or [])
            base = dict(category="Resume", source="resume", source_ref=e.id, resume_relevance=1.0)
            _upsert(db, user_id, existing, question=f"What did you work on at {e.organization}?", difficulty="easy",
                    expected_concepts=techs[:5], job_relevance=job_rel(techs), **base)
            _upsert(db, user_id, existing, question=f"What was the most valuable thing you learned at {e.organization}?",
                    difficulty="easy", expected_concepts=["learning", "impact"], job_relevance=job_rel(techs), **base)
        for c in profile.certifications:
            _upsert(db, user_id, existing, question=f"What did you learn from the {c.name}?", category="Resume",
                    difficulty="easy", expected_concepts=find_technologies(c.name), resume_relevance=1.0,
                    job_relevance=job_rel(find_technologies(c.name)), source="resume", source_ref=c.id)
    for seed in load_seed_questions():
        concepts = seed.get("expected_concepts", [])
        rel_terms = {*concepts, *seed.get("technologies", [])}
        resume_rel = 1.0 if rel_terms & verified_skills else (0.5 if seed["category"] in ("Behavioral", "HR", "DSA", "System Design") else 0.2)
        _upsert(db, user_id, existing, question=seed["question"], category=seed["category"],
                difficulty=seed["difficulty"], expected_concepts=concepts, resume_relevance=resume_rel,
                job_relevance=job_rel([*concepts, *seed.get("technologies", [])]), source="seed")
    if job:
        parsed = job.parsed or {}
        for skill in (parsed.get("required_skills") or [])[:8]:
            _upsert(db, user_id, existing, question=f"This role needs {skill}. How have you used it, and what would you do differently next time?",
                    category="Job", difficulty="medium", expected_concepts=[skill],
                    resume_relevance=1.0 if skill in verified_skills else 0.3, job_relevance=1.0, source="job",
                    source_ref=job.id, job_id=job.id)
        for resp in (parsed.get("responsibilities") or [])[:5]:
            _upsert(db, user_id, existing, question=f"The role involves: \"{resp.rstrip('.')}\". How would you approach that?",
                    category="Job", difficulty="medium", expected_concepts=find_technologies(resp)[:4],
                    resume_relevance=0.5, job_relevance=1.0, source="job", source_ref=job.id, job_id=job.id)
    db.flush()
    return len(existing)


def record_practice(item: QuestionBankItem, score: float) -> None:
    n = item.attempts or 0
    item.avg_score = round(((item.avg_score or 0.0) * n + score) / (n + 1), 3)
    item.attempts = n + 1
    item.last_practiced_at = datetime.now(UTC)
