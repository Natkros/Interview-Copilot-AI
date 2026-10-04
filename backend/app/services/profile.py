"""Candidate profile persistence, verification operations and the canonical
profile view (spec section 8)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import (
    CandidateProfile,
    Certification,
    Experience,
    ProfileItem,
    Project,
    Skill,
    User,
)
from app.models.domain import ProjectFacts
from app.services.resume_parser import ParsedResume
from app.services.taxonomy import canonicalise, category_of

PROJECT_LIST_FIELDS = ("architecture", "technologies", "responsibilities", "challenges", "solutions",
                       "results", "metrics", "limitations", "future_work", "testing")
PROJECT_TEXT_FIELDS = ("name", "description", "problem", "solution", "candidate_role", "url")
ITEM_KINDS = ("education", "achievements", "research", "publications", "leadership", "activities")

KIND_MODELS: dict[str, type] = {
    "skill": Skill, "project": Project, "experience": Experience,
    "certification": Certification, "item": ProfileItem,
}


def get_or_create_profile(db: Session, user: User) -> CandidateProfile:
    profile = db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    if profile is None:
        profile = CandidateProfile(user_id=user.id, email=user.email)
        db.add(profile)
        db.flush()
    return profile


def _clean_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    return [str(v).strip() for v in values if str(v).strip()]


def apply_parsed_resume(db: Session, profile: CandidateProfile, parsed: ParsedResume, document_id: str,
                        filename: str) -> None:
    """Merge a freshly parsed resume into the profile.

    Unverified items that came from a previous resume are replaced; anything the
    candidate verified or added manually is kept. New items whose name matches
    a kept item are skipped (no duplicates)."""
    src = {"document": filename, "document_id": document_id}

    def drop_unverified(rows: list) -> None:
        for row in list(rows):
            if not row.verified and row.origin == "resume":
                rows.remove(row)

    for coll in (profile.skills, profile.projects, profile.experiences, profile.certifications, profile.items):
        drop_unverified(coll)
    db.flush()

    personal = parsed.personal
    if not profile.personal_verified:
        profile.name = personal.get("name") or profile.name
        profile.headline = personal.get("headline") or profile.headline
        profile.email = personal.get("email") or profile.email
        profile.phone = personal.get("phone") or profile.phone
        profile.links = personal.get("links") or profile.links or []
        profile.summary = parsed.summary or profile.summary

    existing_skills = {s.name.lower() for s in profile.skills}
    for i, sk in enumerate(parsed.skills):
        if sk["name"].lower() in existing_skills:
            continue
        existing_skills.add(sk["name"].lower())
        profile.skills.append(Skill(name=sk["name"], category=sk["category"], verified=False, origin="resume",
                                    source={**src, **sk["source"]}, position=len(profile.skills) + i))

    existing_projects = {p.name.lower() for p in profile.projects}
    for pr in parsed.projects:
        if pr["name"].lower() in existing_projects:
            continue
        profile.projects.append(Project(
            name=pr["name"],
            **{k: pr.get(k) for k in ("description", "problem", "solution", "candidate_role", "url")},
            **{k: _clean_list(pr.get(k)) for k in PROJECT_LIST_FIELDS},
            verified=False, origin="resume", source={**src, **pr["source"]}, position=pr["position"],
        ))

    existing_exp = {((e.title or "") + "|" + (e.organization or "")).lower() for e in profile.experiences}
    for ex in parsed.experiences:
        key = ((ex["title"] or "") + "|" + (ex["organization"] or "")).lower()
        if key in existing_exp:
            continue
        profile.experiences.append(Experience(
            kind=ex["kind"], title=ex["title"], organization=ex["organization"], location=ex["location"],
            start_date=ex["start_date"], end_date=ex["end_date"], highlights=_clean_list(ex["highlights"]),
            technologies=_clean_list(ex["technologies"]), verified=False, origin="resume",
            source={**src, **ex["source"]}, position=ex["position"],
        ))

    existing_certs = {c.name.lower() for c in profile.certifications}
    for c in parsed.certifications:
        if c["name"].lower() in existing_certs:
            continue
        profile.certifications.append(Certification(
            name=c["name"], issuer=c["issuer"], date=c["date"], verified=False, origin="resume",
            source={**src, **c["source"]}, position=c["position"],
        ))

    existing_items = {(i.kind, i.title.lower()) for i in profile.items}
    for it in parsed.items:
        if (it["kind"], it["title"].lower()) in existing_items:
            continue
        profile.items.append(ProfileItem(
            kind=it["kind"], title=it["title"][:300], subtitle=it.get("subtitle"), date=it.get("date"),
            details=_clean_list(it.get("details")), verified=False, origin="resume",
            source={**src, **it["source"]}, position=it["position"],
        ))
    db.flush()


# ----------------------------------------------------------------------------- views


def serialize_row(row: Any) -> dict[str, Any]:
    data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    data.pop("profile_id", None)
    if data.get("created_at"):
        data["created_at"] = data["created_at"].isoformat()
    return data


def serialize_profile(profile: CandidateProfile) -> dict[str, Any]:
    total = verified = 0
    for coll in (profile.skills, profile.projects, profile.experiences, profile.certifications, profile.items):
        total += len(coll)
        verified += sum(1 for r in coll if r.verified)
    return {
        "id": profile.id,
        "personal": {
            "name": profile.name, "headline": profile.headline, "email": profile.email,
            "phone": profile.phone, "location": profile.location, "links": profile.links or [],
            "summary": profile.summary, "verified": profile.personal_verified,
        },
        "skills": [serialize_row(s) for s in profile.skills],
        "projects": [serialize_row(p) for p in profile.projects],
        "experiences": [serialize_row(e) for e in profile.experiences],
        "certifications": [serialize_row(c) for c in profile.certifications],
        "items": [serialize_row(i) for i in profile.items],
        "verification": {"total": total, "verified": verified,
                         "ratio": round(verified / total, 3) if total else 0.0},
        "kb": {"version": profile.kb_version,
               "indexed_at": profile.kb_indexed_at.isoformat() if profile.kb_indexed_at else None},
    }


def project_facts(p: Project) -> ProjectFacts:
    return ProjectFacts(
        id=p.id, name=p.name, verified=p.verified, description=p.description, problem=p.problem,
        solution=p.solution, candidate_role=p.candidate_role,
        **{f: list(getattr(p, f) or []) for f in PROJECT_LIST_FIELDS},
    )


def canonical_profile(profile: CandidateProfile) -> dict[str, Any]:
    """The normalised candidate profile from the spec, with facts split by
    verification status (only verified facts get top grounding priority)."""
    verified: list[str] = []
    unverified: list[str] = []

    def fact(text: str, ok: bool) -> None:
        (verified if ok else unverified).append(text)

    for s in profile.skills:
        fact(f"Skill: {s.name}", s.verified)
    for p in profile.projects:
        fact(f"Project: {p.name}" + (f" ({', '.join(p.technologies)})" if p.technologies else ""), p.verified)
    for e in profile.experiences:
        fact(f"{e.kind.title()}: {e.title or ''} at {e.organization or ''}".strip(), e.verified)
    for c in profile.certifications:
        fact(f"Certification: {c.name}", c.verified)
    for i in profile.items:
        fact(f"{i.kind.title()}: {i.title}", i.verified)

    return {
        "candidate_id": profile.user_id,
        "name": profile.name,
        "education": [serialize_row(i) for i in profile.items if i.kind == "education"],
        "skills": [{"name": s.name, "category": s.category, "verified": s.verified} for s in profile.skills],
        "experience": [serialize_row(e) for e in profile.experiences if e.kind == "job"],
        "internships": [serialize_row(e) for e in profile.experiences if e.kind == "internship"],
        "projects": [project_facts(p).model_dump() for p in profile.projects],
        "certifications": [serialize_row(c) for c in profile.certifications],
        "achievements": [serialize_row(i) for i in profile.items if i.kind == "achievements"],
        "research": [serialize_row(i) for i in profile.items if i.kind in ("research", "publications")],
        "verified_facts": verified,
        "unverified_facts": unverified,
    }


# ----------------------------------------------------------------------------- edits


class ProfileEditError(ValueError):
    pass


def _owned_row(profile: CandidateProfile, kind: str, item_id: str) -> tuple[list, Any]:
    coll = {
        "skill": profile.skills, "project": profile.projects, "experience": profile.experiences,
        "certification": profile.certifications, "item": profile.items,
    }.get(kind)
    if coll is None:
        raise ProfileEditError(f"unknown kind {kind!r}")
    for row in coll:
        if row.id == item_id:
            return coll, row
    raise ProfileEditError("item not found")  # also covers other users' ids


_EDITABLE: dict[str, set[str]] = {
    "skill": {"name", "category"},
    "project": set(PROJECT_TEXT_FIELDS) | set(PROJECT_LIST_FIELDS),
    "experience": {"kind", "title", "organization", "location", "start_date", "end_date", "highlights", "technologies"},
    "certification": {"name", "issuer", "date"},
    "item": {"kind", "title", "subtitle", "date", "details"},
}
_LIST_FIELDS = set(PROJECT_LIST_FIELDS) | {"highlights", "details"}


def _apply_fields(kind: str, row: Any, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if key not in _EDITABLE[kind]:
            raise ProfileEditError(f"field {key!r} is not editable on {kind}")
        if key in _LIST_FIELDS or (kind == "experience" and key == "technologies"):
            value = _clean_list(value)
            if key == "technologies":
                value = [canonicalise(v) for v in value]
        elif isinstance(value, str):
            value = value.strip() or None
        if kind == "item" and key == "kind" and value not in ITEM_KINDS:
            raise ProfileEditError(f"item kind must be one of {ITEM_KINDS}")
        if kind == "experience" and key == "kind" and value not in ("job", "internship"):
            raise ProfileEditError("experience kind must be job or internship")
        setattr(row, key, value)
    if kind == "skill" and "name" in data:
        row.name = canonicalise(row.name)
        if "category" not in data:
            row.category = category_of(row.name)


def apply_profile_operation(db: Session, profile: CandidateProfile, op: dict[str, Any]) -> dict[str, Any] | None:
    """op = {action: confirm|unconfirm|edit|remove|add|confirm_all, kind, id?, data?}

    Editing an item marks it verified: the candidate has reviewed it."""
    action = op.get("action")
    kind = op.get("kind")
    data = op.get("data") or {}
    if action == "personal":
        for key in ("name", "headline", "email", "phone", "location", "summary", "links"):
            if key in data:
                setattr(profile, key, data[key] if key == "links" else ((data[key] or "").strip() or None))
        profile.personal_verified = True
        return None
    if action == "confirm_all":
        for coll in (profile.skills, profile.projects, profile.experiences, profile.certifications, profile.items):
            for row in coll:
                row.verified = True
        profile.personal_verified = True
        return None
    if kind not in KIND_MODELS:
        raise ProfileEditError("kind must be one of skill, project, experience, certification, item")
    if action == "add":
        model = KIND_MODELS[kind]
        required = {"skill": "name", "project": "name", "certification": "name", "item": "title", "experience": "title"}[kind]
        if not str(data.get(required) or "").strip():
            raise ProfileEditError(f"{required} is required")
        if kind == "item" and data.get("kind") not in ITEM_KINDS:
            raise ProfileEditError(f"item kind must be one of {ITEM_KINDS}")
        row = model(profile_id=profile.id, verified=True, origin="user", source={"document": "manual entry"})
        if kind == "skill":
            name = canonicalise(data["name"])
            if any(s.name.lower() == name.lower() for s in profile.skills):
                raise ProfileEditError("skill already exists")
        _apply_fields(kind, row, data)
        db.add(row)
        db.flush()
        return serialize_row(row)
    coll, row = _owned_row(profile, kind, str(op.get("id")))
    if action == "confirm":
        row.verified = True
    elif action == "unconfirm":
        row.verified = False
    elif action == "edit":
        _apply_fields(kind, row, data)
        row.verified = True
    elif action == "remove":
        coll.remove(row)
        db.flush()
        return None
    else:
        raise ProfileEditError("unknown action")
    db.flush()
    return serialize_row(row)
