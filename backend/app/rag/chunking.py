"""Structure-aware chunking.

Resumes are short and highly structured, so instead of fixed-size windows we
chunk by meaning: one chunk per profile fact group (a project's challenges, an
internship's highlights, a skill category, ...). Each chunk carries the
metadata required by the spec (candidate_id, document_id, document_type,
section, project, technology, verification_status, source, created_at).
Job descriptions are chunked by section; free text uses sentence windows.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.database.models import CandidateProfile, JobDescription
from app.services.taxonomy import CATEGORY_LABELS, find_technologies

_NS = uuid.UUID("6f1c7a52-3b1e-4c0a-9a55-1f2f6c0f9e10")


@dataclass
class ChunkSpec:
    collection: str
    source_type: str
    source_ref: str | None
    text: str
    verified: bool
    document_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def point_id(self, candidate_id: str, ordinal: int) -> str:
        key = f"{candidate_id}:{self.collection}:{self.source_type}:{self.source_ref}:{self.meta.get('field')}:{ordinal}"
        return str(uuid.uuid5(_NS, key))


def _meta(candidate_id: str, doc_type: str, section: str, verified: bool, source: dict | None,
          **extra: Any) -> dict[str, Any]:
    source = source or {}
    return {
        "candidate_id": candidate_id,
        "document_id": source.get("document_id"),
        "document_type": doc_type,
        "section": section,
        "verification_status": "verified" if verified else "unverified",
        "source": source.get("document") or "profile",
        "page": source.get("page"),
        "created_at": datetime.now(UTC).isoformat(),
        **{k: v for k, v in extra.items() if v is not None},
    }


def _join(items: list[str]) -> str:
    return "; ".join(i.strip().rstrip(".") for i in items if i and i.strip())


_PROJECT_FIELDS = (
    ("architecture", "Architecture"),
    ("responsibilities", "What I built / responsibilities"),
    ("challenges", "Challenges"),
    ("solutions", "How challenges were solved"),
    ("results", "Results"),
    ("metrics", "Metrics"),
    ("limitations", "Limitations"),
    ("future_work", "Future work"),
    ("testing", "Testing / evaluation"),
)


def profile_chunks(profile: CandidateProfile) -> list[ChunkSpec]:
    cid = profile.user_id
    chunks: list[ChunkSpec] = []

    # --- personal / summary
    if profile.name or profile.summary or profile.headline:
        text = " ".join(x for x in [
            f"Candidate: {profile.name}." if profile.name else "",
            f"Headline: {profile.headline}." if profile.headline else "",
            f"Summary: {profile.summary}" if profile.summary else "",
        ] if x)
        chunks.append(ChunkSpec("candidate_documents", "summary", "personal", text, profile.personal_verified,
                                meta=_meta(cid, "resume", "Summary", profile.personal_verified, {})))

    # --- skills, grouped by category and verification status
    groups: dict[tuple[str, bool], list[str]] = {}
    for s in profile.skills:
        groups.setdefault((s.category, s.verified), []).append(s.name)
    for (cat, ver), names in groups.items():
        label = CATEGORY_LABELS.get(cat, cat.title())
        chunks.append(ChunkSpec(
            "candidate_documents", "skills", f"skills:{cat}:{int(ver)}",
            f"Skills - {label}: {', '.join(names)}.", ver,
            meta=_meta(cid, "resume", "Skills", ver, {}, category=cat, technology=names[:20]),
        ))

    # --- projects
    for p in profile.projects:
        src = p.source or {}
        base = _meta(cid, "resume", "Projects", p.verified, src, project=p.name, project_id=p.id,
                     technology=p.technologies[:20] if p.technologies else None)
        overview = [f"Project: {p.name}."]
        if p.description:
            overview.append(p.description)
        if p.problem and p.problem != p.description:
            overview.append(f"Problem: {p.problem}")
        if p.solution and p.solution not in (p.description, p.problem):
            overview.append(f"Solution: {p.solution}")
        if p.technologies:
            overview.append(f"Technologies used: {', '.join(p.technologies)}.")
        if p.candidate_role:
            overview.append(f"My role: {p.candidate_role}")
        chunks.append(ChunkSpec("projects", "project", p.id, " ".join(overview), p.verified,
                                document_id=src.get("document_id"), meta={**base, "field": "overview"}))
        for fname, label in _PROJECT_FIELDS:
            values = getattr(p, fname) or []
            if values:
                chunks.append(ChunkSpec(
                    "projects", "project", p.id, f"Project {p.name} - {label}: {_join(values)}.", p.verified,
                    document_id=src.get("document_id"), meta={**base, "field": fname},
                ))

    # --- experience / internships
    for e in profile.experiences:
        src = e.source or {}
        label = "Internship" if e.kind == "internship" else "Experience"
        name = " at ".join(x for x in [e.title, e.organization] if x) or label
        dates = " - ".join(x for x in [e.start_date, e.end_date] if x)
        base = _meta(cid, "resume", label, e.verified, src, experience=name, experience_id=e.id,
                     technology=e.technologies[:20] if e.technologies else None)
        header = f"{label}: {name}" + (f" ({dates})" if dates else "") + (f", {e.location}" if e.location else "") + "."
        if e.technologies:
            header += f" Technologies: {', '.join(e.technologies)}."
        chunks.append(ChunkSpec("experience", e.kind, e.id, header, e.verified,
                                document_id=src.get("document_id"), meta={**base, "field": "overview"}))
        for i, h in enumerate(e.highlights or []):
            chunks.append(ChunkSpec("experience", e.kind, e.id, f"{label} {name}: {h}", e.verified,
                                    document_id=src.get("document_id"), meta={**base, "field": f"highlight_{i}"}))

    # --- certifications
    for c in profile.certifications:
        text = f"Certification: {c.name}" + (f" from {c.issuer}" if c.issuer else "") + (f" ({c.date})" if c.date else "") + "."
        chunks.append(ChunkSpec("candidate_documents", "certification", c.id, text, c.verified,
                                document_id=(c.source or {}).get("document_id"),
                                meta=_meta(cid, "resume", "Certifications", c.verified, c.source)))

    # --- education, achievements, research, leadership, activities
    for it in profile.items:
        label = it.kind.title()
        parts = [f"{label}: {it.title}"]
        if it.subtitle:
            parts.append(it.subtitle)
        if it.date:
            parts.append(f"({it.date})")
        if it.details:
            parts.append("- " + _join(it.details))
        chunks.append(ChunkSpec("candidate_documents", it.kind, it.id, " ".join(parts) + ".", it.verified,
                                document_id=(it.source or {}).get("document_id"),
                                meta=_meta(cid, "resume", label, it.verified, it.source)))
    return chunks


def job_chunks(job: JobDescription) -> list[ChunkSpec]:
    cid = job.user_id
    parsed = job.parsed or {}
    base = {"candidate_id": cid, "document_id": job.document_id, "document_type": "job_description",
            "verification_status": "verified", "source": f"Job: {job.title}", "job_id": job.id,
            "created_at": datetime.now(UTC).isoformat()}
    chunks: list[ChunkSpec] = []
    head = f"Target role: {job.title}" + (f" at {job.company}" if job.company else "") + "."
    if parsed.get("domain"):
        head += f" Domain: {parsed['domain']}."
    chunks.append(ChunkSpec("job_descriptions", "job", job.id, head, True, meta={**base, "section": "Role", "field": "role"}))
    for key, label in (("responsibilities", "Responsibilities"), ("required_skills", "Required"),
                       ("preferred_skills", "Preferred"), ("technologies", "Technologies"),
                       ("experience", "Experience"), ("education", "Education")):
        values = parsed.get(key) or []
        if isinstance(values, str):
            values = [values]
        for i in range(0, len(values), 4):
            group = values[i: i + 4]
            chunks.append(ChunkSpec(
                "job_descriptions", "job", job.id, f"Job {label}: {_join(group)}.", True,
                meta={**base, "section": label, "field": f"{key}_{i // 4}", "technology": find_technologies(" ".join(group))[:10]},
            ))
    return chunks


def sentence_windows(text: str, max_words: int = 120, overlap: int = 1) -> list[str]:
    """Generic fallback chunker for free-form documents."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]
    windows: list[str] = []
    cur: list[str] = []
    for s in sents:
        if cur and sum(len(x.split()) for x in cur) + len(s.split()) > max_words:
            windows.append(" ".join(cur))
            cur = cur[-overlap:] if overlap else []
        cur.append(s)
    if cur:
        windows.append(" ".join(cur))
    return windows
