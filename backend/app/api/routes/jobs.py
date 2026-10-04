from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, audit, owned
from app.api.routes.resume import _read_upload
from app.database.models import Document, InterviewSession, JobDescription
from app.rag.indexer import index_job, remove_job
from app.services.documents import DocumentError, extract_document
from app.services.job_parser import match_profile, parse_job
from app.services.profile import get_or_create_profile
from app.services.question_bank import rebuild_question_bank

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _out(job: JobDescription, full: bool = False) -> dict[str, Any]:
    data = {"id": job.id, "title": job.title, "company": job.company, "created_at": job.created_at.isoformat(),
            "match": job.match, "domain": (job.parsed or {}).get("domain")}
    if full:
        data.update(parsed=job.parsed, raw_text=job.raw_text)
    return data


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, db: DB, user: CurrentUser, text: str | None = Form(default=None),
                     title: str | None = Form(default=None), company: str | None = Form(default=None),
                     file: UploadFile | None = File(default=None)) -> dict[str, Any]:
    document = None
    if file is not None and file.filename:
        data = await _read_upload(file)
        try:
            doc = extract_document(data, file.filename)
        except DocumentError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": exc.code, "message": str(exc)}) from exc
        document = Document(user_id=user.id, kind="job_description", filename=file.filename[:255],
                            content_type=doc.content_type, size_bytes=doc.size_bytes, sha256=doc.sha256,
                            page_count=doc.page_count, text=doc.text, pages=doc.pages, warnings=doc.warnings)
        db.add(document)
        db.flush()
        raw = doc.text
    elif text and len(text.strip()) >= 40:
        raw = text.strip()[:60_000]
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide the job description text (40+ characters) or a file")
    parsed = parse_job(raw, (title or "").strip() or None, (company or "").strip() or None)
    profile = get_or_create_profile(db, user)
    job = JobDescription(user_id=user.id, document_id=document.id if document else None,
                         title=(parsed["role"] or "Untitled role")[:200], company=(parsed.get("company") or None),
                         raw_text=raw, parsed=parsed, match=match_profile(parsed, profile))
    db.add(job)
    db.flush()
    index_job(db, job)
    rebuild_question_bank(db, user.id, profile, job)
    audit(db, request, user.id, "job.create", job.id)
    db.commit()
    return _out(job, full=True)


@router.get("")
def list_jobs(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    jobs = db.scalars(select(JobDescription).where(JobDescription.user_id == user.id)
                      .order_by(JobDescription.created_at.desc())).all()
    return [_out(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    return _out(owned(db, JobDescription, job_id, user), full=True)


@router.post("/{job_id}/rematch")
def rematch(job_id: str, db: DB, user: CurrentUser) -> dict[str, Any]:
    job = owned(db, JobDescription, job_id, user)
    job.match = match_profile(job.parsed or {}, get_or_create_profile(db, user))
    db.commit()
    return _out(job, full=True)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, request: Request, db: DB, user: CurrentUser) -> None:
    job = owned(db, JobDescription, job_id, user)
    remove_job(db, job)
    for s in db.scalars(select(InterviewSession).where(InterviewSession.job_id == job.id)):
        s.job_id = None
    if job.document_id:
        d = db.get(Document, job.document_id)
        job.document_id = None
        db.flush()
        if d:
            db.delete(d)
    db.delete(job)
    audit(db, request, user.id, "job.delete", job_id)
    db.commit()
