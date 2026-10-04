"""Create a demo account with the sample resume (all facts verified) and a
sample job description, for local development and demos.

    cd backend && python scripts/seed_demo.py

Credentials come from DEMO_EMAIL / DEMO_PASSWORD (defaults below are for
local development only - never use them on a deployed instance).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database.models import Base, Document, JobDescription, Resume, ResumeSection, User  # noqa: E402
from app.database.session import db_session, get_engine  # noqa: E402
from app.rag.indexer import index_job, reindex_profile, seed_technical_knowledge  # noqa: E402
from app.services.documents import extract_document  # noqa: E402
from app.services.job_parser import match_profile, parse_job  # noqa: E402
from app.services.profile import apply_parsed_resume, apply_profile_operation, get_or_create_profile  # noqa: E402
from app.services.question_bank import rebuild_question_bank  # noqa: E402
from app.services.resume_parser import parse_resume  # noqa: E402

DEMO_EMAIL = os.environ.get("DEMO_EMAIL", "demo@example.com")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "DemoPass2026")  # local development only

SAMPLE_JD = """Machine Learning Engineer
Company: Acme AI
Responsibilities
- Build retrieval-augmented generation (RAG) services in Python
- Deploy ML models on AWS
Requirements
- Python, Flask or FastAPI
- Experience with vector databases such as Qdrant
- Machine Learning fundamentals
Nice to have
- Docker, Kubernetes
"""


def main() -> None:
    if get_settings().environment == "production":
        sys.exit("Refusing to seed demo data in production.")
    if get_settings().database_url.startswith("sqlite"):
        Base.metadata.create_all(get_engine())
    seed_technical_knowledge()
    with db_session() as db:
        if db.scalar(select(User).where(User.email == DEMO_EMAIL)):
            print(f"Demo user {DEMO_EMAIL} already exists.")
            return
        user = User(email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD),
                    preferences={"answer_length": "45s"})
        db.add(user)
        db.flush()
        raw = (Path(__file__).resolve().parents[1] / "app/evaluation/fixtures/sample_resume.txt").read_bytes()
        doc = extract_document(raw, "sample_resume.txt")
        parsed = parse_resume(doc.text, doc.pages)
        d = Document(user_id=user.id, kind="resume", filename="sample_resume.txt", content_type=doc.content_type,
                     size_bytes=doc.size_bytes, sha256=doc.sha256, text=doc.text, pages=doc.pages)
        db.add(d)
        db.flush()
        resume = Resume(user_id=user.id, document_id=d.id, parse_report=parsed.report())
        db.add(resume)
        db.flush()
        for s in parsed.sections:
            db.add(ResumeSection(resume_id=resume.id, name=s.name, heading=s.heading, text=s.text, page=s.page,
                                 position=s.position))
        profile = get_or_create_profile(db, user)
        apply_parsed_resume(db, profile, parsed, d.id, d.filename)
        apply_profile_operation(db, profile, {"action": "confirm_all"})
        reindex_profile(db, profile)
        pj = parse_job(SAMPLE_JD)
        job = JobDescription(user_id=user.id, title=pj["role"], company=pj["company"], raw_text=SAMPLE_JD, parsed=pj,
                             match=match_profile(pj, profile))
        db.add(job)
        db.flush()
        index_job(db, job)
        rebuild_question_bank(db, user.id, profile, job)
    print(f"Seeded demo user {DEMO_EMAIL} (password from DEMO_PASSWORD or the script default).")


if __name__ == "__main__":
    main()
