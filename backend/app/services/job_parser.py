"""Job-description parsing and resume <-> job matching."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.database.models import CandidateProfile
from app.rag.embeddings import _STOP
from app.services.taxonomy import canonicalise, find_technologies

_SECTIONS: dict[str, list[str]] = {
    "about": ["about us", "about the company", "who we are", "company overview", "about the team"],
    "role": ["about the role", "the role", "role overview", "position summary", "job summary", "overview", "summary"],
    "responsibilities": ["responsibilities", "key responsibilities", "what you'll do", "what you will do",
                         "your responsibilities", "duties", "job responsibilities", "day to day", "in this role you will"],
    "required": ["requirements", "required qualifications", "minimum qualifications", "qualifications",
                 "required skills", "must have", "must-have", "what we're looking for", "what we are looking for",
                 "skills required", "you have", "basic qualifications", "who you are"],
    "preferred": ["preferred qualifications", "preferred skills", "nice to have", "nice-to-have", "bonus points",
                  "good to have", "pluses", "preferred", "bonus"],
    "education": ["education", "educational qualifications"],
    "benefits": ["benefits", "perks", "what we offer", "compensation"],
}
_HEAD_INDEX = {syn: canon for canon, syns in _SECTIONS.items() for syn in syns}
_DOMAINS = {
    "fintech": ["fintech", "payments", "banking", "trading", "financial"],
    "healthcare": ["healthcare", "clinical", "patients", "medical", "health"],
    "e-commerce": ["e-commerce", "ecommerce", "retail", "marketplace", "shopping"],
    "edtech": ["edtech", "education", "learning platform", "students"],
    "SaaS": ["saas", "b2b", "enterprise software"],
    "AI/ML": ["machine learning", "artificial intelligence", "llm", "generative ai", "data science"],
    "gaming": ["gaming", "game"],
    "cybersecurity": ["security", "threat", "cyber"],
    "logistics": ["logistics", "supply chain", "delivery"],
}


def _heading(line: str) -> str | None:
    raw = line.strip().strip(":").strip("#* ").strip()
    if not raw or len(raw) > 60:
        return None
    key = re.sub(r"[^a-z'\- ]", "", raw.lower()).strip()
    return _HEAD_INDEX.get(key)


def _items(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        s = re.sub(r"^\s*(?:[-*•●▪]|\d+[.)])\s*", "", line).strip()
        if len(s) > 3:
            out.append(s.rstrip(";"))
    return out


def parse_job(text: str, title_hint: str | None = None, company_hint: str | None = None) -> dict[str, Any]:
    lines = text.replace("\r", "").split("\n")
    sections: dict[str, list[str]] = {}
    current = "intro"
    for line in lines:
        h = _heading(line)
        if h:
            current = h
            continue
        sections.setdefault(current, []).append(line)
    sec_text = {k: "\n".join(v).strip() for k, v in sections.items()}

    title = title_hint
    if not title:
        m = re.search(r"(?:job title|position|role)\s*[:\-]\s*(.+)", text, re.I)
        if m:
            title = m.group(1).strip()
        else:
            first = next((l.strip() for l in lines if l.strip()), "")
            title = first[:120] if len(first.split()) <= 10 else "Untitled role"
    company = company_hint
    if not company:
        m = re.search(r"(?:company|organization|employer)\s*[:\-]\s*(.+)", text, re.I) or \
            re.search(r"\babout ([A-Z][\w&.\- ]{1,40}?)(?:\n|:|$)", text)
        if m and m.group(1).strip().lower() not in ("us", "the role", "the company", "the team"):
            company = m.group(1).strip()

    responsibilities = _items(sec_text.get("responsibilities", ""))
    req_items = _items(sec_text.get("required", ""))
    pref_items = _items(sec_text.get("preferred", ""))
    if not req_items and not responsibilities:
        # unstructured JD: treat bullet-like lines as requirements
        req_items = [l for l in _items(text) if len(l.split()) > 3][:20]

    required_skills = list(dict.fromkeys(t for item in req_items for t in find_technologies(item)))
    preferred_skills = [t for t in dict.fromkeys(t for item in pref_items for t in find_technologies(item))
                        if t not in required_skills]
    technologies = find_technologies(text)
    years = [int(y) for y in re.findall(r"(\d{1,2})\+?\s*(?:-\s*\d+\s*)?years?", text, re.I) if int(y) < 30]
    experience = [l for l in req_items if re.search(r"\byears?\b|\bexperience\b", l, re.I)][:4]
    education = _items(sec_text.get("education", "")) or [l for l in req_items if re.search(
        r"\b(degree|bachelor|master|b\.?tech|b\.?e\b|computer science|phd|graduate)\b", l, re.I)][:3]
    low = text.lower()
    domain = next((d for d, kws in _DOMAINS.items() if sum(low.count(k) for k in kws) >= 2), None)
    words = [w for w in re.findall(r"[a-z][a-z+#.\-]{2,}", low) if w not in _STOP]
    common = [w for w, _ in Counter(words).most_common(40) if w not in {"will", "work", "team", "experience",
                                                                       "including", "strong", "ability", "role", "years"}]
    keywords = list(dict.fromkeys([*required_skills, *technologies, *common[:12]]))[:30]
    return {
        "role": title, "company": company, "responsibilities": responsibilities[:20],
        "required_skills": required_skills, "preferred_skills": preferred_skills,
        "required_items": req_items[:25], "preferred_items": pref_items[:15],
        "technologies": technologies, "experience": experience,
        "min_years": min(years) if years else None, "education": education, "domain": domain,
        "keywords": keywords,
    }


def match_profile(parsed: dict[str, Any], profile: CandidateProfile | None) -> dict[str, Any]:
    """Resume <-> job match. Verified skills count fully, unverified skills
    count half; evidence lists where in the profile each skill appears."""
    if profile is None:
        return {"score": 0.0, "matched": [], "missing": parsed.get("required_skills", []), "preferred_matched": [],
                "preferred_missing": parsed.get("preferred_skills", []), "evidence": {}}
    skills: dict[str, bool] = {}
    for s in profile.skills:
        skills[s.name.lower()] = skills.get(s.name.lower(), False) or s.verified
    evidence: dict[str, list[str]] = {}
    for p in profile.projects:
        for t in p.technologies or []:
            evidence.setdefault(t.lower(), []).append(f"Project: {p.name}")
            skills.setdefault(t.lower(), p.verified)
    for e in profile.experiences:
        for t in e.technologies or []:
            evidence.setdefault(t.lower(), []).append(f"{e.kind.title()}: {e.organization or e.title}")
            skills.setdefault(t.lower(), e.verified)

    def score_list(items: list[str]) -> tuple[float, list[str], list[str]]:
        if not items:
            return 1.0, [], []
        got, matched, missing = 0.0, [], []
        for it in items:
            v = skills.get(canonicalise(it).lower())
            if v is None:
                missing.append(it)
            else:
                matched.append(it)
                got += 1.0 if v else 0.5
        return got / len(items), matched, missing

    req_score, matched, missing = score_list(parsed.get("required_skills", []))
    pref_score, pmatched, pmissing = score_list(parsed.get("preferred_skills", []))
    tech_score, _, _ = score_list(parsed.get("technologies", []))
    score = round(0.6 * req_score + 0.2 * pref_score + 0.2 * tech_score, 3)
    return {
        "score": score, "required_coverage": round(req_score, 3), "preferred_coverage": round(pref_score, 3),
        "matched": matched, "missing": missing, "preferred_matched": pmatched, "preferred_missing": pmissing,
        "evidence": {k: v[:3] for k, v in evidence.items() if k in {m.lower() for m in matched + pmatched}},
        "method": "skill-coverage-v1 (verified=1.0, unverified=0.5; required 60%, preferred 20%, all technologies 20%)",
    }
