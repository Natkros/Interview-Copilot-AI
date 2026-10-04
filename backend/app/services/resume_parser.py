"""Deterministic resume parser.

Turns extracted resume text into a structured, *unverified* candidate profile.
It only copies text that exists in the document - it never paraphrases or
infers facts - and every item records where it came from (section + page) so
the verification screen can show provenance. Everything produced here starts
with `verified=False`; the candidate confirms, edits or removes it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.documents import page_of
from app.services.taxonomy import canonicalise, category_of, find_technologies

# ----------------------------------------------------------------------------- headings

SECTION_SYNONYMS: dict[str, list[str]] = {
    "summary": ["summary", "profile", "professional summary", "about me", "about", "objective",
                "career objective", "professional profile", "career summary"],
    "education": ["education", "academic background", "academics", "academic qualifications",
                  "qualifications", "education and training"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tech stack",
               "skills and tools", "skills & tools", "key skills", "technical proficiency",
               "skills & technologies", "skills and technologies", "tools and technologies"],
    "experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "relevant experience"],
    "internships": ["internships", "internship experience", "internship", "internships and training"],
    "projects": ["projects", "academic projects", "personal projects", "key projects",
                 "selected projects", "project experience", "technical projects", "major projects"],
    "certifications": ["certifications", "certificates", "licenses and certifications",
                       "licenses & certifications", "courses and certifications",
                       "courses & certifications", "courses", "certification"],
    "achievements": ["achievements", "awards", "honors", "honours", "accomplishments",
                     "awards and achievements", "awards & achievements", "honors and awards"],
    "research": ["research", "research experience", "research work"],
    "publications": ["publications", "papers", "research papers"],
    "leadership": ["leadership", "positions of responsibility", "leadership experience",
                   "leadership and activities", "por"],
    "activities": ["activities", "extracurricular activities", "extra-curricular activities",
                   "extracurriculars", "volunteering", "volunteer experience", "co-curricular activities"],
    "languages": ["languages", "spoken languages"],
    "interests": ["interests", "hobbies", "hobbies and interests"],
}
_HEADING_INDEX = {syn: canon for canon, syns in SECTION_SYNONYMS.items() for syn in syns}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{8,}\d)")
_URL = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[\w-]+\.(?:dev|io|me|ai|com|in))/?[\w./?=#%-]*", re.I)
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_DATE_RANGE = re.compile(rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE}|present|current|now|ongoing)", re.I)
_SINGLE_DATE = re.compile(rf"\b{_DATE}\b", re.I)
_METRIC = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s?(?:%|x\b|ms\b|s\b|sec|seconds|minutes|hours|k\b|K\b|M\b|users|requests|qps|rps|documents|docs|queries|percent|points|GB|MB|TB)"
    r"|top\s?\d+|\brank(?:ed)?\s+\d+|\b\d+(?:\.\d+)?\s?(?:accuracy|f1|precision|recall)|(?:accuracy|f1|precision|recall|latency|throughput)\s+(?:of\s+)?\d+(?:\.\d+)?%?)",
    re.I,
)

_FIELD_LABELS: dict[str, str] = {
    "problem": "problem", "problem statement": "problem", "motivation": "problem", "objective": "problem",
    "goal": "problem", "overview": "description", "description": "description", "summary": "description",
    "solution": "solution", "approach": "solution",
    "role": "candidate_role", "my role": "candidate_role", "contribution": "candidate_role",
    "my contribution": "candidate_role", "responsibilities": "responsibilities",
    "architecture": "architecture", "design": "architecture", "pipeline": "architecture",
    "tech stack": "technologies", "technologies": "technologies", "tech": "technologies",
    "stack": "technologies", "tools": "technologies", "built with": "technologies",
    "challenge": "challenges", "challenges": "challenges", "key challenge": "challenges",
    "difficulty": "challenges", "obstacles": "challenges",
    "solutions": "solutions", "how i solved it": "solutions", "fix": "solutions",
    "result": "results", "results": "results", "impact": "results", "outcome": "results",
    "outcomes": "results", "metrics": "metrics",
    "limitation": "limitations", "limitations": "limitations",
    "future work": "future_work", "next steps": "future_work", "improvements": "future_work",
    "future improvements": "future_work",
    "testing": "testing", "evaluation": "testing", "validation": "testing",
    "link": "url", "url": "url", "github": "url", "demo": "url",
}
_LABEL_LINE = re.compile(
    r"^\s*(?:-\s*)?(?P<label>" + "|".join(sorted(map(re.escape, _FIELD_LABELS), key=len, reverse=True)) + r")\s*[:\-–—]\s*(?P<value>.+)$",
    re.I,
)

_ACTION_VERBS = (
    "built", "developed", "designed", "implemented", "created", "engineered", "architected",
    "integrated", "deployed", "wrote", "added", "optimized", "optimised", "automated", "trained",
    "fine-tuned", "led", "managed", "migrated", "refactored", "set up", "configured", "launched",
    "collaborated", "contributed", "owned", "maintained", "researched", "analyzed", "analysed",
    "evaluated", "tested", "reduced", "improved", "increased", "achieved", "delivered", "used",
    "utilized", "leveraged", "applied", "published", "presented", "won", "mentored", "coordinated",
)
_CHALLENGE_HINTS = ("challenge", "difficult", "struggl", "bottleneck", "hurdle", "obstacle", "the hardest",
                    "issue with", "problem with", "tricky")
_ROLE_HINTS = ("my role", "as the", "as a ", "team lead", "led a team", "solo", "individually", "sole developer",
               "i was responsible", "responsible for")
_ARCH_HINTS = ("architecture", "pipeline", "microservice", "component", "end-to-end", "layer", "module",
               "workflow", "orchestrat", "integrated", "backend", "frontend", "api")
_TEST_HINTS = ("unit test", "tested", "testing", "evaluated", "evaluation", "benchmark", "validated", "a/b")
_FUTURE_HINTS = ("future", "plan to", "planning to", "next step", "would like to")
_LIMIT_HINTS = ("limitation", "limited", "does not support", "doesn't support", "trade-off", "tradeoff")
_PROBLEM_HINTS = ("to address", "to solve", "to help", "problem of", "aimed at", "aims to", "enables",
                  "allows users", "designed to", "so that")


@dataclass
class ParsedSection:
    name: str
    heading: str
    text: str
    page: int
    position: int


@dataclass
class ParsedResume:
    personal: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None
    sections: list[ParsedSection] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    experiences: list[dict[str, Any]] = field(default_factory=list)
    certifications: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)  # education / achievements / ...
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "sections_found": [s.name for s in self.sections],
            "counts": {
                "skills": len(self.skills),
                "projects": len(self.projects),
                "experiences": len(self.experiences),
                "certifications": len(self.certifications),
                "education": sum(1 for i in self.items if i["kind"] == "education"),
                "other_items": sum(1 for i in self.items if i["kind"] != "education"),
            },
            "warnings": self.warnings,
        }


# ----------------------------------------------------------------------------- helpers


def _heading_name(line: str) -> str | None:
    raw = line.strip().strip(":").strip()
    if not raw or len(raw) > 48 or raw.startswith("-"):
        return None
    key = re.sub(r"[^a-z&\s-]", "", raw.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if key in _HEADING_INDEX:
        return _HEADING_INDEX[key]
    return None


def _is_bullet(line: str) -> bool:
    return line.lstrip().startswith("- ")


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*-\s+", "", line).strip()


def _split_list(value: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\||/(?!\w*\.)|•|·)\s*", value)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text) if s.strip()]


def _dates(line: str) -> tuple[str | None, str | None, str]:
    m = _DATE_RANGE.search(line)
    if m:
        rest = (line[: m.start()] + line[m.end():]).strip(" ,|-–—()")
        return m.group(1), m.group(2), rest
    m2 = _SINGLE_DATE.search(line)
    if m2 and re.search(r"\b(19|20)\d{2}\b", m2.group(0)):
        rest = (line[: m2.start()] + line[m2.end():]).strip(" ,|-–—()")
        return None, m2.group(0), rest
    return None, None, line


def _looks_like_entry_header(line: str) -> bool:
    if _is_bullet(line) or _LABEL_LINE.match(line):
        return False
    words = line.split()
    if "|" in line or _DATE_RANGE.search(line):
        return True
    return len(words) <= 12 and not line.rstrip().endswith(".")


# ----------------------------------------------------------------------------- sectioning


def split_sections(text: str, pages: list[str]) -> tuple[list[str], list[ParsedSection]]:
    """Return (header_lines_before_first_section, sections)."""
    lines = text.split("\n")
    header: list[str] = []
    sections: list[ParsedSection] = []
    current: tuple[str, str, list[str]] | None = None
    for line in lines:
        name = _heading_name(line)
        if name:
            if current:
                sections.append(_mk_section(current, pages, len(sections)))
            current = (name, line.strip().strip(":"), [])
            continue
        if current is None:
            header.append(line)
        else:
            current[2].append(line)
    if current:
        sections.append(_mk_section(current, pages, len(sections)))
    # merge duplicate canonical names (e.g. two "Projects" headings)
    merged: dict[str, ParsedSection] = {}
    for s in sections:
        if s.name in merged:
            merged[s.name].text += "\n\n" + s.text
        else:
            merged[s.name] = s
    return header, list(merged.values())


def _mk_section(cur: tuple[str, str, list[str]], pages: list[str], pos: int) -> ParsedSection:
    name, heading, body = cur
    text = "\n".join(body).strip("\n")
    return ParsedSection(name=name, heading=heading, text=text, page=page_of(pages, text or heading), position=pos)


# ----------------------------------------------------------------------------- personal


def parse_personal(header_lines: list[str], full_text: str) -> dict[str, Any]:
    header = "\n".join(header_lines[:12])
    info: dict[str, Any] = {"links": []}
    email = _EMAIL.search(header) or _EMAIL.search(full_text[:2000])
    if email:
        info["email"] = email.group(0)
    phone = _PHONE.search(header)
    if phone and sum(c.isdigit() for c in phone.group(0)) >= 10:
        info["phone"] = phone.group(0).strip()
    for m in _URL.finditer(_EMAIL.sub(" ", header)):
        url = m.group(0).strip(" .,|")
        if "@" in url or len(url) < 8:
            continue
        if any(k in url.lower() for k in ("linkedin", "github", "gitlab", "http", "www", ".dev", ".io", ".me")):
            info["links"].append(url)
    for line in header_lines:
        cand = line.strip()
        if not cand or _EMAIL.search(cand) or _PHONE.search(cand) or "http" in cand.lower():
            continue
        cand_clean = re.split(r"\s*[|•·]\s*", cand)[0].strip()
        words = cand_clean.split()
        if 1 < len(words) <= 5 and all(re.match(r"^[A-Za-z][A-Za-z.'-]*$", w) for w in words):
            info["name"] = cand_clean.title() if cand_clean.isupper() else cand_clean
            break
    # headline = first descriptive line after the name, if short
    if info.get("name"):
        after = False
        for line in header_lines:
            s = line.strip()
            if not s:
                continue
            if info["name"].lower() in s.lower():
                after = True
                continue
            if after and not _EMAIL.search(s) and not _PHONE.search(s) and 2 <= len(s.split()) <= 14 and "http" not in s:
                info["headline"] = s
                break
    return info


# ----------------------------------------------------------------------------- skills


_SKILL_LABEL_CATEGORY = {
    "language": "programming_language", "programming": "programming_language",
    "framework": "framework", "librar": "library", "database": "database", "db": "database",
    "cloud": "cloud", "devops": "devops", "tool": "tool", "ml": "ai_ml", "ai": "ai_ml",
    "machine learning": "ai_ml", "concept": "concept", "core": "concept",
}


def parse_skills(section: ParsedSection | None, full_text: str, pages: list[str],
                 other_sections: list[ParsedSection]) -> list[dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}

    def add(name: str, category: str | None, sec: str, page: int) -> None:
        name = canonicalise(name.strip(" -:.()"))
        if not name or len(name) > 48 or len(name.split()) > 5 or name.lower() in {"and", "etc", "others"}:
            return
        key = name.lower()
        if key in skills:
            return
        cat = category_of(name)
        if cat == "other" and category:
            cat = category
        skills[key] = {"name": name, "category": cat, "source": {"section": sec, "page": page}}

    if section:
        for raw in section.text.split("\n"):
            line = _strip_bullet(raw)
            if not line:
                continue
            label_cat = None
            if ":" in line:
                label, value = line.split(":", 1)
                low = label.lower()
                for k, v in _SKILL_LABEL_CATEGORY.items():
                    if k in low:
                        label_cat = v
                        break
                line = value
            for item in _split_list(line):
                add(item, label_cat, "Skills", section.page)
    # technologies mentioned elsewhere (projects / experience) are also skills,
    # tagged with the section they were found in
    for sec in other_sections:
        if sec.name in ("projects", "experience", "internships", "research", "certifications", "summary"):
            for tech in find_technologies(sec.text):
                add(tech, None, sec.heading or sec.name.title(), sec.page)
    return list(skills.values())


# ----------------------------------------------------------------------------- entries


@dataclass
class _Entry:
    header: str
    lines: list[str] = field(default_factory=list)  # non-bullet continuation lines
    bullets: list[str] = field(default_factory=list)
    labeled: list[tuple[str, str]] = field(default_factory=list)


def split_entries(text: str) -> list[_Entry]:
    entries: list[_Entry] = []
    cur: _Entry | None = None
    prev_blank = True
    prev_bullet = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            prev_blank = True
            continue
        label = _LABEL_LINE.match(line)
        if label and cur is not None:
            cur.labeled.append((_FIELD_LABELS[label.group("label").lower()], label.group("value").strip()))
            prev_bullet, prev_blank = True, False
            continue
        if _is_bullet(line):
            if cur is None:
                cur = _Entry(header="")
                entries.append(cur)
            cur.bullets.append(_strip_bullet(line))
            prev_bullet, prev_blank = True, False
            continue
        starts_new = cur is None or (
            _looks_like_entry_header(line) and (prev_blank or prev_bullet or "|" in line or bool(_DATE_RANGE.search(line)))
        )
        if starts_new:
            cur = _Entry(header=line)
            entries.append(cur)
        else:
            assert cur is not None
            # bullets that wrapped onto a new line continue the previous bullet
            if prev_bullet and cur.bullets and line[:1].islower():
                cur.bullets[-1] += " " + line
            else:
                cur.lines.append(line)
        prev_bullet, prev_blank = False, False
    return [e for e in entries if e.header or e.bullets or e.lines]


def _split_header(header: str) -> tuple[str, str | None]:
    """'AI Bug Analyzer | Python, Flask' -> ('AI Bug Analyzer', 'Python, Flask')."""
    for sep in (" | ", "|", " – ", " — ", " - ", ": "):
        if sep in header:
            left, right = header.split(sep, 1)
            if left.strip():
                return left.strip(), right.strip()
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", header)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).strip()
    return header.strip(), None


def _bucket_bullet(text: str) -> str:
    low = text.lower()
    if any(h in low for h in _CHALLENGE_HINTS):
        return "challenges"
    if any(h in low for h in _LIMIT_HINTS):
        return "limitations"
    if any(h in low for h in _FUTURE_HINTS):
        return "future_work"
    if any(h in low for h in _ROLE_HINTS):
        return "candidate_role"
    if _METRIC.search(text) and any(w in low for w in ("improv", "reduc", "increas", "achiev", "accuracy",
                                                       "faster", "latency", "boost", "cut ", "saved", "%")):
        return "results"
    if any(h in low for h in _TEST_HINTS):
        return "testing"
    return "responsibilities"


def parse_project(entry: _Entry, page: int, position: int) -> dict[str, Any]:
    name, tail = _split_header(entry.header or (entry.bullets[0][:60] if entry.bullets else "Project"))
    _start, date, name_wo_date = _dates(name)
    name = name_wo_date or name
    proj: dict[str, Any] = {
        "name": name.strip(" -|:"), "description": None, "problem": None, "solution": None,
        "candidate_role": None, "architecture": [], "technologies": [], "responsibilities": [],
        "challenges": [], "solutions": [], "results": [], "metrics": [], "limitations": [],
        "future_work": [], "testing": [], "url": None,
        "source": {"section": "Projects", "page": page, "date": date}, "position": position,
    }
    if entry.lines:
        proj["description"] = " ".join(entry.lines)
    techs: list[str] = []
    if tail:
        tail_techs = find_technologies(tail)
        techs += tail_techs or [t for t in _split_list(tail) if len(t) < 30]
    for fname, value in entry.labeled:
        if fname == "technologies":
            techs += find_technologies(value) or _split_list(value)
        elif fname in ("problem", "solution", "candidate_role", "description"):
            proj[fname] = (proj[fname] + " " + value) if proj.get(fname) else value
        elif fname == "url":
            proj["url"] = value
        else:
            proj[fname].append(value)
    for bullet in entry.bullets:
        bucket = _bucket_bullet(bullet)
        if bucket == "candidate_role":
            proj["candidate_role"] = (proj["candidate_role"] + " " + bullet) if proj["candidate_role"] else bullet
            proj["responsibilities"].append(bullet)
        else:
            proj[bucket].append(bullet)
        if any(h in bullet.lower() for h in _ARCH_HINTS) and bucket == "responsibilities":
            proj["architecture"].append(bullet)
    # problem statement: only when the text explicitly states purpose
    if not proj["problem"]:
        for sent in _sentences(proj["description"] or ""):
            if any(h in sent.lower() for h in _PROBLEM_HINTS):
                proj["problem"] = sent
                break
    if not proj["solution"] and proj["description"]:
        first = _sentences(proj["description"])[0]
        if first != proj["problem"]:
            proj["solution"] = first
    # "Challenge: X; solved it by Y" -> challenge X + solution Y
    split_challenges = []
    for ch in proj["challenges"]:
        m = re.split(r"[;,.]?\s*(?:and\s+)?(?:solved (?:it|this)? ?by|fixed (?:it|this)? ?by|resolved (?:it|this)? ?by|addressed (?:it|this)? ?by|overcame (?:it|this)? ?by)\s+", ch, maxsplit=1, flags=re.I)
        split_challenges.append(m[0].strip(" ;,."))
        if len(m) > 1 and m[1].strip():
            proj["solutions"].append(m[1].strip(" ;,."))
    proj["challenges"] = [c for c in split_challenges if c]
    entry_text = "\n".join([entry.header, *entry.lines, *entry.bullets, *(v for _, v in entry.labeled)])
    techs += find_technologies(entry_text)
    proj["technologies"] = list(dict.fromkeys(canonicalise(t) for t in techs if t))
    for chunk in [*proj["results"], *proj["responsibilities"], *(proj["description"] or "").split(". ")]:
        if _METRIC.search(chunk):
            proj["metrics"].append(chunk.strip())
    proj["metrics"] = list(dict.fromkeys(proj["metrics"]))
    return proj


def parse_experience(entry: _Entry, kind_default: str, page: int, position: int) -> dict[str, Any]:
    header_lines = [entry.header, *entry.lines[:2]]
    start = end = None
    title = org = location = None
    joined = " | ".join(h for h in header_lines if h)
    s, e, rest = _dates(joined)
    start, end = s, e
    parts = [p.strip() for p in re.split(r"\s*(?:\||,| at | @ | – | — | - )\s*", rest) if p.strip()]
    if parts:
        title = parts[0]
    if len(parts) > 1:
        org = parts[1]
    if len(parts) > 2:
        location = parts[2]
    # Heuristic: if the first part looks like an organisation (Inc, Ltd, Labs...) swap.
    if title and org and re.search(r"\b(inc|ltd|llc|labs|technologies|solutions|corp|pvt|university|institute)\b", title, re.I):
        title, org = org, title
    kind = "internship" if (kind_default == "internship" or (title and "intern" in title.lower())) else "job"
    highlights = [*entry.bullets, *(v for _, v in entry.labeled)]
    if not entry.bullets and len(entry.lines) > 2:
        highlights += entry.lines[2:]
    techs = find_technologies("\n".join([joined, *highlights]))
    return {
        "kind": kind, "title": title, "organization": org, "location": location,
        "start_date": start, "end_date": end, "highlights": highlights, "technologies": techs,
        "source": {"section": "Internships" if kind_default == "internship" else "Experience", "page": page},
        "position": position,
    }


def parse_certifications(section: ParsedSection) -> list[dict[str, Any]]:
    certs = []
    for i, raw in enumerate(l for l in section.text.split("\n") if l.strip()):
        line = _strip_bullet(raw)
        _s, date, rest = _dates(line)
        name, issuer = rest, None
        m = re.match(r"^(.*?)\s*(?:[-–—|,]\s*|\bby\b\s*|\()\s*([^()]+?)\)?\s*$", rest)
        if m and m.group(1) and len(m.group(1)) > 3:
            name, issuer = m.group(1).strip(), m.group(2).strip()
        certs.append({"name": name.strip(" -|,"), "issuer": issuer, "date": date,
                      "source": {"section": section.heading, "page": section.page}, "position": i})
    return [c for c in certs if c["name"]]


def parse_education(section: ParsedSection) -> list[dict[str, Any]]:
    items = []
    for i, entry in enumerate(split_entries(section.text)):
        lines = [entry.header, *entry.lines]
        _s, date, first = _dates(lines[0] if lines else "")
        if _s and date:
            date = f"{_s} - {date}"
        title = first.strip(" ,|-") or (lines[0] if lines else "")
        subtitle = None
        rest = lines[1:]
        if rest:
            _s2, d2, sub = _dates(rest[0])
            subtitle = sub.strip(" ,|-") or None
            date = date or d2
            rest = rest[1:]
        items.append({"kind": "education", "title": title, "subtitle": subtitle, "date": date,
                      "details": [*rest, *entry.bullets, *(f"{k}: {v}" for k, v in entry.labeled)],
                      "source": {"section": section.heading, "page": section.page}, "position": i})
    return [it for it in items if it["title"]]


def parse_simple_items(section: ParsedSection, kind: str) -> list[dict[str, Any]]:
    items = []
    for entry in split_entries(section.text):
        if entry.header and not entry.bullets:
            title = entry.header
            details = entry.lines
        elif entry.header:
            title, details = entry.header, [*entry.lines, *entry.bullets]
        else:
            for b in entry.bullets:
                _s, date, rest = _dates(b)
                items.append({"kind": kind, "title": rest or b, "subtitle": None, "date": date, "details": [],
                              "source": {"section": section.heading, "page": section.page}})
            continue
        _s, date, rest = _dates(title)
        items.append({"kind": kind, "title": rest or title, "subtitle": None, "date": date, "details": details,
                      "source": {"section": section.heading, "page": section.page}})
    for i, it in enumerate(items):
        it["position"] = i
    return items


# ----------------------------------------------------------------------------- entrypoint


def parse_resume(text: str, pages: list[str] | None = None) -> ParsedResume:
    pages = pages or [text]
    header, sections = split_sections(text, pages)
    result = ParsedResume(sections=sections)
    result.personal = parse_personal(header, text)
    by_name = {s.name: s for s in sections}

    if not sections:
        result.warnings.append(
            "No standard resume section headings were detected. Review the extracted details and add missing items manually."
        )
    if "summary" in by_name:
        result.summary = " ".join(l.strip() for l in by_name["summary"].text.split("\n") if l.strip())
    elif header:
        # Unlabelled summary paragraph in the header block (long sentence-like lines)
        para = [l.strip() for l in header if len(l.split()) > 12]
        if para:
            result.summary = " ".join(para)

    result.skills = parse_skills(by_name.get("skills"), text, pages, sections)
    if "projects" in by_name:
        sec = by_name["projects"]
        result.projects = [parse_project(e, sec.page, i) for i, e in enumerate(split_entries(sec.text))]
        result.projects = [p for p in result.projects if p["name"]]
    for name, kind in (("experience", "job"), ("internships", "internship")):
        if name in by_name:
            sec = by_name[name]
            offset = len(result.experiences)
            result.experiences += [
                parse_experience(e, kind, sec.page, offset + i) for i, e in enumerate(split_entries(sec.text))
            ]
    if "certifications" in by_name:
        result.certifications = parse_certifications(by_name["certifications"])
    if "education" in by_name:
        result.items += parse_education(by_name["education"])
    for kind in ("achievements", "research", "publications", "leadership", "activities"):
        if kind in by_name:
            result.items += parse_simple_items(by_name[kind], kind)

    if not result.personal.get("name"):
        result.warnings.append("Candidate name could not be detected - please enter it.")
    if not result.projects:
        result.warnings.append("No projects were detected. Project questions are common; consider adding them.")
    for p in result.projects:
        if not p["challenges"]:
            p.setdefault("_missing", []).append("challenges")
        if not p["candidate_role"]:
            p.setdefault("_missing", []).append("candidate_role")
    return result
