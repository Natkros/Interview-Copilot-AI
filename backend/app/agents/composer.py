"""Deterministic, extractive answer composer.

Used when no LLM is configured and as the degraded-mode fallback when the LLM
fails mid-interview. Every candidate-specific sentence is assembled from
profile fields (re-voiced into first person, never paraphrased into new
facts); general statements come from the curated technical knowledge base.
When the profile lacks what the question asks for, it says so and suggests a
safe way to answer, instead of inventing content.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agents.prompts import target_words
from app.models.domain import (
    Classification,
    ContextBundle,
    ProjectFacts,
    QuestionType,
    ResolvedQuestion,
)
from app.services.taxonomy import category_of, find_technologies

Q = QuestionType

_PAST_VERBS = {
    "built", "developed", "designed", "implemented", "created", "engineered", "architected", "integrated",
    "deployed", "wrote", "added", "optimized", "optimised", "automated", "trained", "fine-tuned", "led",
    "managed", "migrated", "refactored", "configured", "launched", "collaborated", "contributed", "owned",
    "maintained", "researched", "analyzed", "analysed", "evaluated", "tested", "reduced", "improved",
    "increased", "achieved", "delivered", "used", "utilized", "leveraged", "applied", "published",
    "presented", "won", "mentored", "coordinated", "stored", "set", "containerized",
    "containerised", "streamed", "scraped", "cleaned", "visualized", "visualised", "benchmarked",
    "debugged", "documented", "prototyped", "shipped", "worked", "handled", "extracted", "embedded",
}
_GERUND = {
    "built": "building", "developed": "developing", "designed": "designing", "implemented": "implementing",
    "created": "creating", "engineered": "engineering", "integrated": "integrating", "deployed": "deploying",
    "wrote": "writing", "added": "adding", "optimized": "optimizing", "automated": "automating",
    "trained": "training", "stored": "storing", "configured": "configuring", "migrated": "migrating",
    "extracted": "extracting", "embedded": "embedding", "streamed": "streaming", "tested": "testing",
    "evaluated": "evaluating", "led": "leading", "set": "setting", "used": "using", "applied": "applying",
}
_VECTOR_DBS = {"qdrant", "pinecone", "weaviate", "milvus", "chroma", "faiss", "pgvector"}


@dataclass
class Draft:
    text: str
    key_points: list[str] = field(default_factory=list)
    star: dict[str, str] = field(default_factory=dict)
    insufficient: bool = False
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- text helpers


def _strip(s: str) -> str:
    return s.strip().rstrip(".;:, ")


def lc_first(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    words = s.split()
    first = words[0]
    # "Computer Science student" - a capitalised phrase is a proper noun
    if len(words) > 1 and first[:1].isupper() and words[1][:1].isupper():
        return s
    # keep acronyms / proper names / technologies
    if (len(first) > 1 and first[:2].isupper()) or find_technologies(first) or first in ("I", "I'm", "I've"):
        return s
    return s[0].lower() + s[1:]


def join_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def first_person(bullet: str) -> str:
    """'Built an ingestion pipeline' -> 'I built an ingestion pipeline'."""
    b = _strip(bullet)
    if not b:
        return b
    if re.match(r"^(I|I'm|I've|My|We)\b", b):
        return b
    first = b.split()[0].lower()
    if first in _PAST_VERBS:
        return "I " + b[0].lower() + b[1:]
    return "I worked on " + lc_first(b)


def short_gerund(bullet: str) -> str:
    """'Built an ingestion pipeline that extracts ...' -> 'building the ingestion pipeline'."""
    g = gerund_phrase(bullet)
    g = re.split(r"\s+(?:that|which|to|with|using|for|so|by)\s+|,", g, maxsplit=1)[0]
    g = re.sub(r"^(\w+ing) (?:an?|my) ", r"\1 the ", g)
    return g


def gerund_phrase(bullet: str) -> str:
    """'Built an ingestion pipeline ...' -> 'building an ingestion pipeline ...'."""
    b = _strip(bullet)
    words = b.split()
    if not words:
        return b
    g = _GERUND.get(words[0].lower())
    if g:
        return " ".join([g, *words[1:]])
    return "working on " + lc_first(b)


def describe(desc: str) -> str:
    d = _strip(desc)
    return lc_first(d) if re.match(r"^(A|An|The)\b", d) else d


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip()) if s.strip()]


def article(word: str) -> str:
    w = word.strip().split()[0] if word.strip() else ""
    if len(w) > 1 and w.isupper():  # acronyms are read letter by letter: "an ML model", "a URL"
        return "an" if w[0] in "AEFHILMNORSX" else "a"
    return "an" if w[:1].lower() in "aeiou" else "a"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'+#.-]+\b", text))


class _Builder:
    """Collects sentences in priority order and fills up to a word budget."""

    def __init__(self, budget: int, strict: bool = False) -> None:
        self.budget = budget
        self.strict = strict  # "shorter": required sentences may be dropped to honour the budget
        self.required: list[str] = []
        self.optional: list[str] = []

    def must(self, s: str | None) -> None:
        if s:
            self.required.append(_finish(s))

    def may(self, s: str | None) -> None:
        if s:
            self.optional.append(_finish(s))

    def build(self) -> str:
        out = list(dict.fromkeys(self.required))
        if self.strict:
            kept, total = [], 0
            for i, s in enumerate(out):
                w = word_count(s)
                if i >= 1 and total + w > self.budget:
                    break
                kept.append(s)
                total += w
            return " ".join(kept)
        words = sum(word_count(s) for s in out)
        for s in dict.fromkeys(self.optional):
            if s in out:
                continue
            w = word_count(s)
            if words + w > self.budget * 1.15 and words >= self.budget * 0.6:
                break
            out.append(s)
            words += w
        return " ".join(out)


def _finish(s: str) -> str:
    s = s.strip()
    s = s[0].upper() + s[1:] if s else s
    return s if s.endswith((".", "?", "!")) else s + "."


# ----------------------------------------------------------------------------- composer


class OfflineComposer:
    name = "offline-extractive-v1"

    def compose(
        self,
        question: str,
        resolved: ResolvedQuestion,
        cls: Classification,
        bundle: ContextBundle,
        overview: dict[str, Any],
        recent: list[dict],
        length: str = "45s",
        variant: str = "default",
        budget: int | None = None,
    ) -> Draft:
        budget = budget or target_words(length, variant)
        self.variant = variant
        self.seed = int(hashlib.md5(f"{question}|{variant}".encode()).hexdigest(), 16)
        b = _Builder(budget, strict=variant == "shorter")
        t = cls.type
        aspect = resolved.aspect
        fp = bundle.focus_project
        draft_extra: dict[str, Any] = {}

        if t == Q.GREETING:
            b.must("Hi, thanks for having me. I'm doing well and I'm looking forward to the conversation")
        elif t == Q.CLARIFICATION:
            self._clarify(b, recent)
        elif t == Q.INTRODUCTION:
            self._introduction(b, bundle, overview)
        elif t == Q.BEHAVIORAL or t == Q.SITUATIONAL:
            draft_extra = self._behavioral(b, question, bundle, overview)
        elif t == Q.HR:
            draft_extra = self._hr(b, question, bundle, overview)
        elif re.search(r"certif|\bdegree\b|\bgpa\b|\bcgpa\b", question, re.I) and t in (Q.RESUME, Q.FOLLOW_UP, Q.PROJECT):
            draft_extra = self._resume(b, question, bundle, overview, resolved)
        elif fp is not None and (t in (Q.PROJECT, Q.FOLLOW_UP, Q.RESUME) or (cls.requires_resume_context and resolved.focus.project_id)):
            draft_extra = self._project(b, question, aspect, fp, bundle, resolved)
        elif t in (Q.PROJECT,):
            draft_extra = self._unknown_project(b, question, overview)
        elif t == Q.RESUME or (t == Q.FOLLOW_UP and resolved.focus.experience_id):
            draft_extra = self._resume(b, question, bundle, overview, resolved)
        elif t == Q.SYSTEM_DESIGN:
            self._system_design(b, question, bundle)
        elif t in (Q.CODING, Q.DSA):
            draft_extra = self._coding(b, question, bundle)
        elif t == Q.FOLLOW_UP:
            self._clarify(b, recent, elaborate=True)
        else:
            draft_extra = self._technical(b, question, cls, bundle, overview)

        text = b.build()
        return Draft(
            text=text,
            key_points=_key_points(text, fp),
            star=draft_extra.get("star", {}),
            insufficient=bool(draft_extra.get("insufficient")),
            notes=draft_extra.get("notes", []),
        )

    # ------------------------------------------------------------------ intro

    def _introduction(self, b: _Builder, bundle: ContextBundle, ov: dict[str, Any]) -> None:
        name = ov.get("name")
        summary = ov.get("summary") or ov.get("headline")
        if summary:
            s = sentences(summary)[0]
            if re.match(r"^(I|I'm|I am)\b", s):
                b.must(s)
            else:
                lead = f"I'm {name}, " if name else "I'm "
                body = lc_first(_strip(s))
                b.must(lead + (f"{article(body)} {body}" if not re.match(r"^(a|an|the)\b", body) else body))
        elif name:
            b.must(f"I'm {name}")
        edu = next((e for e in ov.get("education", [])), None)
        if edu:
            title = _strip(edu["title"])
            ongoing = _is_ongoing(edu.get("date"))
            (b.must if b.budget >= 80 else b.may)(("Right now I'm studying " if ongoing else "I studied ") + _degree_phrase(title))
        projects = ov.get("projects", [])[:2]
        if projects and b.budget < 80:
            b.must("I've built projects like " + " and ".join(f"the {p['name']}" for p in projects))
        elif projects:
            parts = []
            for p in projects:
                d = p.get("description")
                parts.append(f"the {p['name']}" + (f", {describe(sentences(d)[0])}" if d else ""))
            b.must("I've built projects like " + (" and ".join(parts) if len(parts) <= 2 else join_list(parts)))
        exp = next(iter(ov.get("experiences", [])), None)
        if exp and (exp.get("title") or exp.get("organization")):
            role = exp.get("title") or "a role"
            org = exp.get("organization")
            hl = exp.get("highlights") or []
            s = f"I also worked as {article(role)} {role}" + (f" at {org}" if org else "")
            if hl:
                s += ", where " + lc_first(first_person(hl[0]))
            b.may(s)
        skills = ov.get("top_skills", [])[:5]
        if skills:
            b.may(f"My core skills are {join_list(skills)}")
        if bundle.job_title:
            overlap = [k for k in bundle.job_keywords if k.lower() in {s.lower() for s in ov.get("all_skills", [])}][:3]
            if overlap:
                b.may(f"That's why this {bundle.job_title} role is a great fit for me, especially the focus on {join_list(overlap)}")

    # ------------------------------------------------------------------ projects

    def _project(self, b: _Builder, question: str, aspect: str | None, fp: ProjectFacts,
                 bundle: ContextBundle, resolved: ResolvedQuestion) -> dict[str, Any]:
        q_techs = find_technologies(question)
        tech = q_techs[0] if q_techs else None
        if aspect == "why_choice" and not tech:
            tech = resolved.focus.technology
        if aspect == "why_choice" and tech:
            return self._why_choice(b, fp, tech, bundle)
        if aspect == "challenge":
            return self._challenge(b, fp)
        if aspect == "role":
            return self._role(b, fp)
        if aspect in ("improvement", "limitation"):
            return self._improvement(b, fp, aspect)
        if aspect == "testing":
            return self._listed(b, fp, "testing", "how you tested it",
                                "explain how you checked it worked, for example the manual or automated checks you actually ran")
        if aspect in ("result", "metric"):
            return self._results(b, fp)
        if tech and tech.lower() not in {t.lower() for t in fp.technologies}:
            return self._tech_not_in_project(b, fp, tech, bundle)
        doc_sents = _project_doc_sentences(bundle, fp, question)
        if doc_sents and aspect in (None, "architecture", "overview", "technology"):
            # the candidate's own project documentation answers the specific question
            b.must(f"In my {fp.name}, " + lc_first(doc_sents[0]))
            for s in doc_sents[1:3]:
                b.must(s)
            for r in fp.responsibilities[:2]:
                b.may(first_person(r))
            return {}
        if aspect == "architecture":
            self._architecture(b, fp, question)
            return {}
        self._overview(b, fp)
        return {}

    def _overview(self, b: _Builder, fp: ProjectFacts) -> None:
        if fp.description:
            b.must(f"My {fp.name} is {describe(sentences(fp.description)[0])}")
        elif fp.solution:
            b.must(f"My {fp.name} is {describe(fp.solution)}")
        else:
            b.must(f"My {fp.name} is a project I built" + (f" with {join_list(fp.technologies[:4])}" if fp.technologies else ""))
        if fp.problem and fp.problem not in (fp.description or ""):
            b.must(f"The problem it addresses: {lc_first(_strip(fp.problem))}")
        for r in fp.responsibilities[:3]:
            b.must(first_person(r))
        mentioned = " ".join(b.required).lower()
        unmentioned = [t for t in fp.technologies if t.lower() not in mentioned][:5]
        if unmentioned:
            b.may(f"The stack included {join_list(unmentioned)}")
        for r in fp.results[:2]:
            b.may(first_person(r))
        if fp.challenges:
            b.may(f"The main challenge was {lc_first(_strip(fp.challenges[0]))}")
        for r in fp.responsibilities[3:]:
            b.may(first_person(r))

    def _architecture(self, b: _Builder, fp: ProjectFacts, question: str) -> None:
        if "deploy" in question.lower() or "host" in question.lower():
            deploy = [r for r in [*fp.responsibilities, *fp.architecture] if re.search(r"deploy|host|docker|aws|ec2|cloud|kubernetes", r, re.I)]
            if deploy:
                for d in deploy[:2]:
                    b.must(first_person(d))
                return
            b.must(f"Your project information doesn't describe how the {fp.name} was deployed")
            b.must("A safe way to answer is to explain exactly how you ran it, even if that was locally, and what you would use to deploy it properly")
            return
        if fp.description:
            b.must(f"The {fp.name} is {describe(sentences(fp.description)[0])}")
        arch = list(dict.fromkeys([*fp.architecture, *fp.responsibilities]))
        for a in arch[:4]:
            b.must(first_person(a))
        if fp.technologies:
            b.may(f"The main pieces were {join_list(fp.technologies[:6])}")

    def _why_choice(self, b: _Builder, fp: ProjectFacts, tech: str, bundle: ContextBundle) -> dict[str, Any]:
        used = tech.lower() in {t.lower() for t in fp.technologies}
        evidence = [r for r in [*fp.responsibilities, *fp.architecture, *(fp.description and [fp.description] or [])]
                    if tech.lower() in r.lower()]
        if not used and not evidence:
            return self._tech_not_in_project(b, fp, tech, bundle)
        role_noun = _role_noun(tech)
        b.must(f"I chose {tech} as the {role_noun} for my {fp.name}")
        if evidence:
            b.must("In the project, " + lc_first(first_person(evidence[0])))
        kb = _kb_for(bundle, tech)
        notes = [f"Your profile doesn't record why you picked {tech} over alternatives. The reasons in this answer are "
                 f"{tech}'s general strengths - only say them if they match your real reasoning, and mention any "
                 f"alternatives you actually compared."]
        if kb:
            kb_sents = sentences(_kb_body(kb.text))[1:4]
            if kb_sents:
                b.must("It fit that design well because " + lc_first(_it(kb_sents[0])))
            for s in kb_sents[1:]:
                b.may(_it(s))
        else:
            b.may(f"It suited what the {fp.name} needed")
        return {"notes": notes}

    def _tech_not_in_project(self, b: _Builder, fp: ProjectFacts, tech: str, bundle: ContextBundle) -> dict[str, Any]:
        stack = join_list(fp.technologies[:5])
        b.must(f"Your project information doesn't mention using {tech} in the {fp.name}")
        if stack:
            b.must(f"A safe way to answer is to be upfront that the project used {stack}, and explain what you know about {tech} in general")
        kb = _kb_for(bundle, tech)
        if kb:
            b.may("In general, " + lc_first(sentences(_kb_body(kb.text))[0]))
        return {"insufficient": True}

    def _challenge(self, b: _Builder, fp: ProjectFacts) -> dict[str, Any]:
        if fp.challenges:
            b.must(f"The biggest challenge in my {fp.name} was {lc_first(_strip(fp.challenges[0]))}")
            if fp.solutions:
                sol = _strip(fp.solutions[0])
                first = sol.split()[0].lower() if sol else ""
                if first.endswith("ing"):
                    b.must(f"I solved it by {lc_first(sol)}")
                elif first in _PAST_VERBS:
                    b.must("To fix it, " + lc_first(first_person(sol)))
                else:
                    b.must(f"The way I handled it was {lc_first(sol)}")
            else:
                b.must("Your project information doesn't say how you solved it, so describe the fix you actually made")
            for r in fp.results[:1]:
                b.may(first_person(r))
            for c in fp.challenges[1:2]:
                b.may(f"Another challenge was {lc_first(_strip(c))}")
            return {}
        suggestions = list(dict.fromkeys(short_gerund(r) for r in [*fp.architecture, *fp.responsibilities]))[:2]
        b.must("Your project information doesn't specify a particular challenge")
        s = "A safe way to answer this would be to explain a challenge you personally encountered"
        if suggestions:
            s += ", such as a problem you hit while " + " or while ".join(suggestions)
        b.must(s)
        b.must("Then explain what you did about it and what you learned")
        return {"insufficient": True, "notes": [f"Add a verified challenge to the {fp.name} in your profile so future answers can use it."]}

    def _role(self, b: _Builder, fp: ProjectFacts) -> dict[str, Any]:
        notes = []
        if fp.candidate_role:
            b.must(f"On the {fp.name}, " + lc_first(first_person(fp.candidate_role)))
        else:
            notes.append("Your resume doesn't say whether this was a solo or team project, or your team size - state that yourself.")
        resp = [first_person(r) for r in fp.responsibilities[:3]]
        if resp:
            lead = "My main contributions were these. " if fp.candidate_role else f"On the {fp.name}, "
            b.must(lead + lc_first(resp[0]) if not fp.candidate_role else resp[0])
            for r in resp[1:]:
                b.must(r)
        elif not fp.candidate_role:
            b.must(f"Your project information doesn't describe your specific role in the {fp.name}")
            b.must("A safe way to answer is to describe exactly which parts you built yourself")
            return {"insufficient": True, "notes": notes}
        return {"notes": notes}

    def _improvement(self, b: _Builder, fp: ProjectFacts, aspect: str) -> dict[str, Any]:
        primary = fp.limitations if aspect == "limitation" else fp.future_work
        secondary = fp.future_work if aspect == "limitation" else fp.limitations
        if primary or secondary:
            for f in primary[:2]:
                b.must(("One limitation is " if aspect == "limitation" else "If I took it further, I'd focus on ") + lc_first(_strip(f)))
            for f in secondary[:1]:
                b.may(("I'd also like to " if aspect == "limitation" else "One current limitation is ") + lc_first(_strip(f)))
            return {}
        b.must(f"Your project information doesn't list limitations or planned improvements for the {fp.name}")
        comp = next(iter([*fp.architecture, *fp.responsibilities]), None)
        s = "A safe way to answer is to pick a part you know well"
        if comp:
            s += f", like {short_gerund(comp)}"
        b.must(s + ", and explain one realistic improvement and its trade-off")
        return {"insufficient": True}

    def _listed(self, b: _Builder, fp: ProjectFacts, field_name: str, what: str, safe: str) -> dict[str, Any]:
        values = getattr(fp, field_name)
        if values:
            for v in values[:3]:
                b.must(first_person(v))
            return {}
        b.must(f"Your project information doesn't describe {what} for the {fp.name}")
        b.must(f"A safe way to answer is to {safe}")
        return {"insufficient": True}

    def _results(self, b: _Builder, fp: ProjectFacts) -> dict[str, Any]:
        values = list(dict.fromkeys([*fp.results, *fp.metrics]))
        if values:
            for v in values[:3]:
                b.must(first_person(v))
            return {}
        b.must(f"Your resume doesn't list measured results or metrics for the {fp.name}")
        b.must("A safe way to answer is to describe what worked and how you checked it, without quoting numbers you didn't measure")
        return {"insufficient": True}

    def _unknown_project(self, b: _Builder, question: str, ov: dict[str, Any]) -> dict[str, Any]:
        m = re.search(r"\byour ((?:[\w+#.-]+ ){0,4}?)(project|app|application|system|tool|platform)\b", question, re.I)
        thing = (m.group(1).strip() + " " + m.group(2)) if m and m.group(1).strip() else "that project"
        names = [p["name"] for p in ov.get("projects", [])][:3]
        b.must(f"Your resume doesn't mention {article(thing) if m and m.group(1).strip() else ''} {thing}".replace("  ", " "))
        if names:
            b.must(f"Don't describe a project you didn't build. A safe way to answer is to say so and talk about {join_list(names)} instead")
        else:
            b.must("Don't describe a project you didn't build; add your real projects to your profile first")
        return {"insufficient": True}

    # ------------------------------------------------------------------ resume

    def _resume(self, b: _Builder, question: str, bundle: ContextBundle, ov: dict[str, Any],
                resolved: ResolvedQuestion) -> dict[str, Any]:
        low = question.lower()
        if "certif" in low:
            certs = ov.get("certifications", [])
            asked = _asked_name(question, r"(?:your|the)\s+(.+?)\s+certif")
            if asked and not any(asked.lower() in c["name"].lower() or c["name"].lower() in asked.lower() for c in certs):
                b.must(f"Your resume doesn't list {article(asked)} {asked} certification")
                if certs:
                    b.must("Don't claim it. You could mention the certifications you do have, like " + join_list([c["name"] for c in certs[:3]]))
                return {"insufficient": True}
            if certs:
                for c in certs[:3]:
                    b.must(f"I hold the {c['name']}" + (f" from {c['issuer']}" if c.get("issuer") else "") + (f", earned in {c['date']}" if c.get("date") else ""))
                return {}
            b.must("Your resume doesn't list any certifications")
            return {"insufficient": True}
        if re.search(r"\b(educat\w*|degree|college|university|gpa|cgpa|study|studied|major|academic)\b", low):
            edu = ov.get("education", [])
            if edu:
                e = edu[0]
                b.must(("I'm studying " if _is_ongoing(e.get("date")) else "I studied ") + _degree_phrase(_strip(e["title"])))
                if e.get("subtitle"):
                    b.must(f"My {_strip(e['subtitle'])}" if re.match(r"^(cgpa|gpa|grade)", e["subtitle"], re.I) else _strip(e["subtitle"]))
                return {}
        if re.search(r"\b(achievement|award|hackathon|competition|proud)\b", low):
            ach = ov.get("achievements", [])
            if ach:
                for a in ach[:3]:
                    b.must(f"One achievement I'm proud of: {lc_first(_strip(a['title']))}" + (f" in {a['date']}" if a.get("date") else ""))
                return {}
        exps = ov.get("experiences", [])
        # an employer named in the question that isn't in the profile: never substitute another one
        m = re.search(r"\b(?:at|for|with)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})", question)
        if m and not find_technologies(m.group(1)) and not resolved.focus.experience_id:
            org = _strip(m.group(1))
            from app.agents.classifier import name_mentioned

            if not any(e.get("organization") and (name_mentioned(org, e["organization"]) or
                                                  name_mentioned(e["organization"], org)) for e in exps):
                b.must(f"Your resume doesn't mention working at {org}")
                if exps:
                    names = [e.get("organization") or e.get("title") for e in exps[:2]]
                    b.must(f"Don't claim experience you don't have. You could talk about your time at {join_list([n for n in names if n])} instead")
                return {"insufficient": True}
        exp = None
        if resolved.focus.experience_id:
            exp = next((e for e in exps if e["id"] == resolved.focus.experience_id), None)
        exp = exp or (exps[0] if exps and re.search(r"\b(intern|experience|work|job|role|company)\b", low) else None)
        if exp:
            org = exp.get("organization")
            title = exp.get("title")
            b.must("At " + (org or "that company") + (f", I worked as {article(title)} {title}" if title else ""))
            for h in exp.get("highlights", [])[:3]:
                b.must(first_person(h))
            if exp.get("technologies"):
                b.may(f"I worked with {join_list(exp['technologies'][:5])} there")
            return {}
        if bundle.candidate:
            top = bundle.candidate[0]
            b.must("From my background: " + lc_first(_strip(top.text)))
            return {}
        b.must("Your resume doesn't provide enough information to answer this specifically")
        return {"insufficient": True}

    # ------------------------------------------------------------------ behavioural / HR

    def _behavioral(self, b: _Builder, question: str, bundle: ContextBundle, ov: dict[str, Any]) -> dict[str, Any]:
        low = question.lower()
        interpersonal = re.search(r"\b(conflict|disagree|teammate|colleague|team member|leader|led a team|manager|"
                                  r"failure|failed|mistake|feedback|criticism|persuade|convince)\w*", low)
        story_projects = [p for p in ov.get("projects", []) if p.get("challenges")]
        if interpersonal or not story_projects:
            kind = interpersonal.group(0) if interpersonal else "situation like this"
            b.must(f"Your resume doesn't describe a {kind} situation you can draw on")
            anchors = [p["name"] for p in ov.get("projects", [])[:2]] + [e.get("organization") for e in ov.get("experiences", [])[:1] if e.get("organization")]
            s = "A safe way to answer is to use a real experience"
            if anchors:
                s += f", for example from your work on {join_list([a for a in anchors if a])}"
            b.must(s + ", and walk through the situation, your task, what you did and the outcome")
            return {"insufficient": True, "star": {}}
        p = story_projects[0]
        situation = f"I was working on my {p['name']}" + (f", which is {describe(sentences(p['description'])[0])}" if p.get("description") else "")
        challenge = lc_first(_strip(p["challenges"][0]))
        task = f"I ran into a problem: {challenge}, and I needed to fix it"
        action = None
        if p.get("solutions"):
            sol = _strip(p["solutions"][0])
            action = ("I solved it by " + lc_first(sol)) if sol.split()[0].lower().endswith("ing") else first_person(sol)
        result = first_person(p["results"][0]) if p.get("results") else None
        b.must(situation)
        b.must(task)
        notes = []
        if action:
            b.must(action)
        else:
            notes.append("Your resume doesn't say how you resolved this - add the action you actually took.")
        if result:
            b.must(result)
        else:
            notes.append("Your resume doesn't record the outcome - add the real result when you tell this story.")
        star = {"situation": _finish(situation), "task": _finish(task), "action": _finish(action) if action else "",
                "result": _finish(result) if result else ""}
        return {"star": star, "notes": notes}

    def _hr(self, b: _Builder, question: str, bundle: ContextBundle, ov: dict[str, Any]) -> dict[str, Any]:
        low = question.lower()
        projects = ov.get("projects", [])
        skills = ov.get("top_skills", [])
        if "strength" in low:
            if skills and projects:
                p = projects[0]
                b.must(f"One of my strengths is building practical systems with {join_list(skills[:3])}")
                if p.get("responsibilities"):
                    b.must(f"For example, in my {p['name']}, " + lc_first(first_person(p["responsibilities"][0])))
                return {}
        if "weakness" in low:
            b.must("Your resume doesn't say anything about weaknesses, so this has to come from you")
            b.must("A safe way to answer is to name a real but non-critical weakness and the concrete steps you're taking to improve it")
            return {"insufficient": True}
        if re.search(r"\b(hire|why (this|our)|why do you want|fit)\b", low):
            if bundle.job_title:
                overlap = [k for k in bundle.job_keywords if k.lower() in {s.lower() for s in ov.get("all_skills", [])}][:4]
                b.must(f"I'm interested in this {bundle.job_title} role because it matches what I've been building")
                if overlap:
                    b.must(f"The role asks for {join_list(overlap)}, and those are things I've worked with directly")
            if projects:
                p = projects[0]
                b.must(f"For example, my {p['name']} is {describe(sentences(p['description'])[0])}" if p.get("description")
                       else f"For example, I built the {p['name']}")
            if not bundle.job_title:
                b.may("Add the job description in InterviewOS to tailor this answer to the role")
            if b.required:
                return {"notes": ["Add one sentence in your own words about why this specific company appeals to you."]}
        if re.search(r"\b(career goal|where do you see|five years|future)\b", low):
            b.must("Your resume doesn't state your career goals")
            if ov.get("summary"):
                b.must("A safe way to answer is to build on what your profile already says you focus on - " + lc_first(_strip(sentences(ov["summary"])[0])) + " - and where you want to grow")
            return {"insufficient": True}
        b.must("Your resume doesn't provide enough information to answer this specifically")
        b.must("A safe way to answer is to be honest and concrete, and connect it to your real experience")
        return {"insufficient": True}

    # ------------------------------------------------------------------ technical

    def _technical(self, b: _Builder, question: str, cls: Classification, bundle: ContextBundle,
                   ov: dict[str, Any]) -> dict[str, Any]:
        q_techs = find_technologies(question)
        personal = re.search(r"\b(your|you've|have you|did you|you used)\b", question, re.I)
        all_skills = {s.lower() for s in ov.get("all_skills", [])}
        if personal and q_techs and not any(t.lower() in all_skills for t in q_techs):
            b.must(f"Your resume doesn't mention hands-on experience with {join_list(q_techs)}")
            b.must("Be upfront about that, and explain what you understand about it conceptually")
            kb = bundle.technical[0] if bundle.technical else None
            if kb:
                b.may("From what I understand, " + lc_first(sentences(_kb_body(kb.text))[0]))
            return {"insufficient": True}
        kb_chunks = [c for c in bundle.technical if c.score >= 0.5] or bundle.technical[:1]
        if not kb_chunks:
            b.must("Your knowledge base doesn't cover this topic, so there is no reference answer to draft from")
            b.must("A safe way to answer is to define the concept, give a small example and mention one trade-off")
            return {"insufficient": True, "notes": ["Offline mode only drafts technical answers from the built-in reference set. Configure LLM_API_KEY for full technical answers."]}
        main = sentences(_kb_body(kb_chunks[0].text))
        b.must(main[0])
        for s in main[1:3]:
            b.must(s)
        for s in main[3:]:
            b.may(s)
        # practical relevance: tie to the candidate's own verified usage
        for t in q_techs:
            for p in ov.get("projects", []):
                if t.lower() in {x.lower() for x in p.get("technologies", [])}:
                    ev = next((r for r in p.get("responsibilities", []) if t.lower() in r.lower()), None)
                    b.must(f"I've used {t} in my {p['name']}" + (", where " + lc_first(first_person(ev)) if ev else ""))
                    break
            else:
                continue
            break
        for c in kb_chunks[1:2]:
            b.may(sentences(_kb_body(c.text))[0])
        return {}

    def _system_design(self, b: _Builder, question: str, bundle: ContextBundle) -> None:
        m = re.search(r"design (?:a|an|the)\s+(.+?)[?.]*$", question, re.I)
        subject = _strip(m.group(1)) if m else "this system"
        b.must(f"For {article(subject)} {subject}, I'd start by clarifying requirements: the core features, expected traffic, latency targets and how strong consistency needs to be")
        b.must("At a high level I'd put stateless API servers behind a load balancer, with a primary database for durable data and a cache like Redis for hot reads")
        b.must("For the data flow, a request hits the load balancer, an API server validates it, checks the cache and falls back to the database on a miss")
        scaling = next((c for c in bundle.technical if "scal" in c.text.lower()), None)
        b.must(sentences(_kb_body(scaling.text))[1] if scaling and len(sentences(_kb_body(scaling.text))) > 1 else
               "To scale, I'd add instances horizontally, use read replicas and caching, and shard the data once a single database becomes the bottleneck")
        b.may("For reliability I'd replicate the database, add health checks and retries, and move slow work onto a queue so it runs asynchronously")
        b.may("On security, I'd add authentication, rate limiting and input validation at the edge")
        b.may("The main trade-off is consistency versus availability and cost, so I'd decide that per feature based on the requirements")

    def _coding(self, b: _Builder, question: str, bundle: ContextBundle) -> dict[str, Any]:
        b.must("I'd start by confirming the input format, constraints and edge cases, like empty input or very large input")
        kb = bundle.technical[0] if bundle.technical else None
        if kb and kb.score >= 0.4:
            for s in sentences(_kb_body(kb.text))[:2]:
                b.must(s)
        b.must("Then I'd explain the approach before writing code, walk through a small example, and state the time and space complexity")
        b.may("Finally I'd test it against the edge cases out loud")
        return {"notes": ["Offline mode gives you an answer structure for coding questions. Configure LLM_API_KEY for worked solutions."]}

    # ------------------------------------------------------------------ misc

    def _clarify(self, b: _Builder, recent: list[dict], elaborate: bool = False) -> None:
        last = next((r for r in reversed(recent) if r["role"] == "candidate"), None)
        if not last:
            b.must("Sure, could you tell me which part you'd like me to go into?")
            return
        sents = sentences(last["text"])
        if elaborate and len(sents) > 1:
            b.must("Sure. To expand on that, " + lc_first(sents[1]))
            for s in sents[2:4]:
                b.may(s)
        else:
            b.must("Sure. To put it more simply, " + lc_first(sents[0]))


# ----------------------------------------------------------------------------- helpers


def _project_doc_sentences(bundle: ContextBundle, fp: ProjectFacts, question: str) -> list[str]:
    """Sentences from the candidate's uploaded documentation for this project
    that overlap the question (at least two content terms)."""
    from app.rag.embeddings import tokenize

    q = {t for t in tokenize(question) if len(t) > 2} - {t.lower() for t in tokenize(fp.name)}
    scored: list[tuple[int, int, str]] = []
    order = 0
    for c in bundle.candidate:
        if c.source_type != "project_doc" or c.meta.get("project_id") != fp.id:
            continue
        body = re.sub(rf"^{re.escape(fp.name)} documentation:\s*", "", c.text)
        for s in sentences(body):
            overlap = len(q & set(tokenize(s)))
            if overlap >= 2 and len(s.split()) >= 5:
                scored.append((overlap, -order, s))
            order += 1
    best = sorted(scored, reverse=True)[:3]
    # present the chosen sentences in document order
    return [s for _, _, s in sorted(best, key=lambda x: -x[1])]


def _kb_body(text: str) -> str:
    """Strip the 'Title: ' prefix of technical KB chunks."""
    return re.sub(r"^[^:]{2,60}:\s+", "", text, count=1)


def _kb_for(bundle: ContextBundle, tech: str):
    for c in bundle.technical:
        if tech.lower() in (c.meta.get("title") or "").lower():
            return c
    for c in bundle.technical:
        if tech.lower() in c.text.lower()[:80]:
            return c
    return None


def _it(sentence: str) -> str:
    return re.sub(r"^(It|This)\b", "it", sentence) if sentence.startswith(("It ", "This ")) else sentence


def _role_noun(tech: str) -> str:
    low = tech.lower()
    if low in _VECTOR_DBS:
        return "vector database"
    return {
        "database": "database", "framework": "framework", "cloud": "cloud platform", "library": "library",
        "programming_language": "language", "devops": "tooling", "ai_ml": "approach",
    }.get(category_of(tech), "tool")


def _is_ongoing(date: str | None) -> bool:
    if not date:
        return False
    if re.search(r"present|current|ongoing|now", date, re.I):
        return True
    years = [int(y) for y in re.findall(r"\b(20\d{2}|19\d{2})\b", date)]
    return bool(years) and max(years) >= datetime.now(UTC).year


def _degree_phrase(title: str) -> str:
    t = _strip(title)
    if re.match(r"^(b\.?\s?tech|b\.?e|b\.?sc|bachelor|m\.?\s?tech|m\.?sc|master|ph\.?d|mba|bs|ms|ba|ma)\b", t, re.I):
        t = re.sub(r",\s+(?=[A-Z][\w ]*(University|Institute|College|School))", " at ", t)
        return f"{article(t)} {t}" if not t.lower().startswith(("a ", "an ")) else t
    return t


def _asked_name(question: str, pattern: str) -> str | None:
    m = re.search(pattern, question, re.I)
    return _strip(m.group(1)) if m else None


def _key_points(text: str, fp: ProjectFacts | None) -> list[str]:
    points: list[str] = []
    for s in sentences(text):
        s2 = re.sub(r"^(I'm|I've|I'd|I|My|In the project,|In general,|For example,|Sure\.)\s*", "", s).strip()
        words = s2.split()
        if len(words) < 3 or s.startswith(("Your ", "A safe way")):
            continue
        points.append(" ".join(words[:9]).rstrip(",.;:") + ("..." if len(words) > 9 else ""))
        if len(points) == 5:
            break
    return points
