from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import DB, CurrentUser, audit, owned
from app.core.config import get_settings
from app.database.models import CandidateProfile, Document, DocumentChunk, Resume, ResumeSection
from app.rag.chunking import sentence_windows
from app.rag.indexer import reindex_profile
from app.rag.keyword import invalidate_keyword_cache
from app.rag.vectorstore import get_vector_store
from app.services.documents import DocumentError, extract_document
from app.services.profile import (
    ProfileEditError,
    apply_parsed_resume,
    apply_profile_operation,
    canonical_profile,
    get_or_create_profile,
    serialize_profile,
)
from app.services.question_bank import rebuild_question_bank
from app.services.resume_parser import parse_resume

router = APIRouter(tags=["resume"])


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds the {get_settings().max_upload_mb} MB limit")
    return data


def _profile(db, user) -> CandidateProfile:
    return get_or_create_profile(db, user)


@router.post("/resume/upload", status_code=status.HTTP_201_CREATED)
async def upload_resume(request: Request, db: DB, user: CurrentUser, file: UploadFile = File(...)) -> dict[str, Any]:
    data = await _read_upload(file)
    try:
        doc = extract_document(data, file.filename or "resume")
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": exc.code, "message": str(exc)}) from exc
    parsed = parse_resume(doc.text, doc.pages)
    document = Document(user_id=user.id, kind="resume", filename=(file.filename or "resume")[:255],
                        content_type=doc.content_type, size_bytes=doc.size_bytes, sha256=doc.sha256,
                        page_count=doc.page_count, text=doc.text, pages=doc.pages, warnings=doc.warnings)
    db.add(document)
    db.flush()
    for old in db.scalars(select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))):
        old.is_active = False
    resume = Resume(user_id=user.id, document_id=document.id, parse_report=parsed.report())
    db.add(resume)
    db.flush()
    for s in parsed.sections:
        db.add(ResumeSection(resume_id=resume.id, name=s.name, heading=s.heading, text=s.text, page=s.page, position=s.position))
    profile = _profile(db, user)
    apply_parsed_resume(db, profile, parsed, document.id, document.filename)
    kb = reindex_profile(db, profile)
    audit(db, request, user.id, "resume.upload", document.id, size=doc.size_bytes, kind=doc.kind)
    db.commit()
    db.refresh(profile)
    return {"resume_id": resume.id, "document_id": document.id, "parse_report": resume.parse_report,
            "warnings": [*doc.warnings, *parsed.warnings], "knowledge_base": kb, "profile": serialize_profile(profile)}


@router.get("/resume")
def get_resume(db: DB, user: CurrentUser) -> dict[str, Any]:
    profile = _profile(db, user)
    resume = db.scalar(select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True)))
    sections = []
    document = None
    if resume:
        sections = [{"name": s.name, "heading": s.heading, "text": s.text, "page": s.page} for s in resume.sections]
        d = db.get(Document, resume.document_id)
        if d:
            document = {"id": d.id, "filename": d.filename, "pages": d.page_count, "uploaded_at": d.created_at.isoformat(),
                        "warnings": d.warnings}
    db.commit()
    return {"profile": serialize_profile(profile), "canonical": canonical_profile(profile), "document": document,
            "sections": sections, "parse_report": resume.parse_report if resume else None}


class ProfileOperation(BaseModel):
    action: Literal["confirm", "unconfirm", "edit", "remove", "add", "confirm_all", "personal"]
    kind: Literal["skill", "project", "experience", "certification", "item"] | None = None
    id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ProfilePatch(BaseModel):
    operations: list[ProfileOperation] = Field(min_length=1, max_length=200)
    reindex: bool = True


@router.patch("/resume")
def patch_resume(body: ProfilePatch, request: Request, db: DB, user: CurrentUser) -> dict[str, Any]:
    profile = _profile(db, user)
    results = []
    try:
        for op in body.operations:
            results.append(apply_profile_operation(db, profile, op.model_dump()))
    except ProfileEditError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.flush()
    db.refresh(profile)
    kb = reindex_profile(db, profile) if body.reindex else None
    audit(db, request, user.id, "profile.update", None, operations=len(body.operations))
    db.commit()
    db.refresh(profile)
    return {"profile": serialize_profile(profile), "results": results, "knowledge_base": kb}


@router.post("/resume/reindex")
def reindex(db: DB, user: CurrentUser) -> dict[str, Any]:
    profile = _profile(db, user)
    kb = reindex_profile(db, profile)
    n = rebuild_question_bank(db, user.id, profile)
    db.commit()
    return {"knowledge_base": kb, "question_bank": n}


@router.delete("/resume", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume(request: Request, db: DB, user: CurrentUser) -> None:
    """Delete uploaded resumes and every profile fact extracted from them
    (manually added facts are kept)."""
    for r in db.scalars(select(Resume).where(Resume.user_id == user.id)).all():
        doc_id = r.document_id
        db.delete(r)
        db.flush()
        d = db.get(Document, doc_id)
        if d:
            db.delete(d)
    profile = _profile(db, user)
    for coll in (profile.skills, profile.projects, profile.experiences, profile.certifications, profile.items):
        for row in list(coll):
            if row.origin == "resume":
                coll.remove(row)
    if not profile.personal_verified:
        profile.name = profile.headline = profile.summary = profile.phone = None
        profile.links = []
    db.flush()
    reindex_profile(db, profile)
    audit(db, request, user.id, "resume.delete")
    db.commit()


# ----------------------------------------------------------------------------- documents


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(request: Request, db: DB, user: CurrentUser, file: UploadFile = File(...),
                          project_id: str | None = Form(default=None),
                          kind: Literal["project_doc", "other"] = Form(default="project_doc")) -> dict[str, Any]:
    """Supplementary documents (project write-ups, design docs, papers). Chunked
    and indexed for retrieval; optionally linked to a profile project."""
    data = await _read_upload(file)
    try:
        doc = extract_document(data, file.filename or "document")
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {"code": exc.code, "message": str(exc)}) from exc
    profile = _profile(db, user)
    project = None
    if project_id:
        project = next((p for p in profile.projects if p.id == project_id), None)
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    document = Document(user_id=user.id, kind=kind, filename=(file.filename or "document")[:255],
                        content_type=doc.content_type, size_bytes=doc.size_bytes, sha256=doc.sha256,
                        page_count=doc.page_count, text=doc.text, pages=doc.pages, warnings=doc.warnings)
    db.add(document)
    db.flush()
    items = []
    for page_no, page in enumerate(doc.pages, start=1):
        for i, window in enumerate(sentence_windows(page)):
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document.id}:{page_no}:{i}"))
            meta = {"candidate_id": user.id, "document_id": document.id, "document_type": kind,
                    "section": "Project documentation" if project else "Document", "source": document.filename,
                    "page": page_no, "verification_status": "document", "field": f"doc_{page_no}_{i}"}
            if project:
                meta.update(project=project.name, project_id=project.id)
            text = (f"{project.name} documentation: " if project else "") + window
            db.add(DocumentChunk(id=pid, user_id=user.id, document_id=document.id,
                                 collection="projects" if project else "candidate_documents", source_type=kind,
                                 source_ref=project.id if project else document.id, text=text, meta=meta, verified=False))
            items.append(("projects" if project else "candidate_documents", pid, text, meta))
    store = get_vector_store()
    if store is not None:
        for coll in ("projects", "candidate_documents"):
            batch = [(pid, text, meta) for c, pid, text, meta in items if c == coll]
            if batch:
                store.upsert(coll, batch)
    invalidate_keyword_cache(user.id)
    audit(db, request, user.id, "document.upload", document.id)
    db.commit()
    return {"id": document.id, "filename": document.filename, "chunks": len(items), "warnings": doc.warnings,
            "project": project.name if project else None}


@router.get("/documents")
def list_documents(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    docs = db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())).all()
    out = []
    for d in docs:
        chunk = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == d.id, DocumentChunk.source_type == d.kind)
                           .limit(1)).first() if d.kind in ("project_doc", "other") else None
        meta = (chunk.meta or {}) if chunk else {}
        out.append({"id": d.id, "kind": d.kind, "filename": d.filename, "pages": d.page_count, "size_bytes": d.size_bytes,
                    "created_at": d.created_at.isoformat(), "warnings": d.warnings,
                    "project_id": meta.get("project_id"), "project": meta.get("project")})
    return out


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: str, request: Request, db: DB, user: CurrentUser) -> None:
    d = owned(db, Document, doc_id, user)
    if d.kind == "resume":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use DELETE /resume to delete resumes")
    store = get_vector_store()
    if store is not None:
        for coll in ("projects", "candidate_documents"):
            store.delete_where(coll, user.id, document_id=d.id)
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == d.id, DocumentChunk.user_id == user.id))
    db.delete(d)
    invalidate_keyword_cache(user.id)
    audit(db, request, user.id, "document.delete", doc_id)
    db.commit()
