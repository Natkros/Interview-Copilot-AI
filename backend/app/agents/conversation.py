"""Conversation Manager: turn storage, short/long-term memory, follow-up
detection and reference resolution ("why did you choose that?" -> which
technology, in which project).

State is persisted on `interview_sessions.state` after every turn so that a
WebSocket reconnect or server restart resumes with the same context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.classifier import (
    ClassifierContext,
    classify,
    name_mentioned,
    topic_for_technologies,
    topic_for_technology,
)
from app.database.models import (
    CandidateProfile,
    ConversationTurn,
    InterviewSession,
    SessionSummary,
)
from app.models.domain import Classification, FocusState, QuestionType, ResolvedQuestion
from app.services.taxonomy import find_technologies

Q = QuestionType

SHORT_TERM_TURNS = 6
SUMMARY_EVERY = 6  # interviewer questions between long-term summaries

_ASPECTS: list[tuple[str, re.Pattern[str]]] = [
    ("challenge", re.compile(r"\b(challeng|difficult|hard(est)? part|problem(s)? (did you|you) (face|hit)|obstacle|struggle|tricky|issue(s)? did you)", re.I)),
    ("role", re.compile(r"\b(your (role|contribution|part|responsibilit)|what did you (do|own|build) (on|in|for)|who did what|team size)\b", re.I)),
    ("why_choice", re.compile(r"\bwhy (did|would) you (choose|chose|pick|use|go with|select|prefer|decide)|why (not|this|that)\b|\bwhy \w+ (over|instead)\b", re.I)),
    ("improvement", re.compile(r"\b(improve|do differently|change (it|anything)|next steps?|future|if you (had|were to) (more time|redo))\b", re.I)),
    ("limitation", re.compile(r"\b(limitation|weakness(es)? of|drawback|shortcoming|what doesn'?t)\b", re.I)),
    ("testing", re.compile(r"\b(test(ed|ing)?|evaluat|validat|measure (quality|accuracy)|how do you know it works)\b", re.I)),
    ("metric", re.compile(r"\b(metric|numbers?|accuracy|latency|how (fast|accurate|many)|performance figures?)\b", re.I)),
    ("result", re.compile(r"\bwhat (were|was|are|is) (the|your) (results?|outcomes?|impact)\b|\bresults? (did|were|have) you\b|"
                          r"\b(outcome|impact) of\b|\bwhat came of\b|\bwhat did (it|you) achieve\b|\bwhat results\b", re.I)),
    ("architecture", re.compile(r"\b(deploy(ed|ment)?|host(ed|ing)?|architecture|design(ed)? (it|the system)|how (does|did) it work|components|data flow|pipeline|walk (me|us) through)\b", re.I)),
    ("overview", re.compile(r"\b(tell (me|us) about|describe|explain|what is|overview|summar)\w*\b", re.I)),
]

_REF_WORD = re.compile(r"\b(that|it|this)\b", re.I)


def detect_aspect(text: str) -> str | None:
    for name, pat in _ASPECTS:
        if pat.search(text):
            return name
    return None


@dataclass
class ProfileIndex:
    projects: list[dict[str, Any]] = field(default_factory=list)  # {id, name, technologies}
    experiences: list[dict[str, Any]] = field(default_factory=list)  # {id, name, org, technologies}

    @classmethod
    def from_profile(cls, profile: CandidateProfile | None) -> ProfileIndex:
        if profile is None:
            return cls()
        return cls(
            projects=[{"id": p.id, "name": p.name, "technologies": list(p.technologies or [])} for p in profile.projects],
            experiences=[{"id": e.id, "name": " at ".join(x for x in [e.title, e.organization] if x) or "Experience",
                          "org": e.organization or "", "title": e.title or "",
                          "technologies": list(e.technologies or []), "kind": e.kind} for e in profile.experiences],
        )

    def project(self, pid: str | None) -> dict[str, Any] | None:
        return next((p for p in self.projects if p["id"] == pid), None)


class ReferenceResolver:
    """Resolves which project / experience / technology an utterance is about
    and rewrites it into a self-contained query for retrieval and generation."""

    def __init__(self, index: ProfileIndex) -> None:
        self.index = index

    def _match_project(self, text: str, techs: list[str], cls: Classification | None = None) -> dict[str, Any] | None:
        low = text.lower()
        # 1) explicit name (full or distinctive words)
        for p in self.index.projects:
            if name_mentioned(text, p["name"]):
                return p
        # 2) "your <X> project" where X is a technology / word in a project's name
        m = re.search(r"\byour ((?:[\w+#.-]+ ){0,3}?)(project|app|application|system|tool|platform)\b", low)
        if m and m.group(1).strip():
            qual = m.group(1).strip().lower()
            for p in self.index.projects:
                if qual in p["name"].lower() or any(qual == t.lower() for t in p["technologies"]):
                    return p
        # 3) a technology used in exactly one project, asked about personally
        #    ("how did you use Docker?") - not for certification / education questions
        project_like = cls is None or cls.type in (Q.PROJECT, Q.FOLLOW_UP) or (
            cls.type.value in {"TECHNICAL", "RAG", "DATABASE", "CLOUD", "DEVOPS", "MACHINE_LEARNING", "DEEP_LEARNING",
                               "GENERATIVE_AI", "SECURITY"} and re.search(r"\b(did you|have you|you used|your project)\b", low))
        if project_like and techs and re.search(r"\b(you|your)\b", low) and not re.search(r"certif|degree|course", low):
            owners = [p for p in self.index.projects
                      if {t.lower() for t in p["technologies"]} & {t.lower() for t in techs}]
            if len(owners) == 1:
                return owners[0]
        return None

    def _match_experience(self, text: str) -> dict[str, Any] | None:
        low = text.lower()
        for e in self.index.experiences:
            if e["org"] and name_mentioned(text, e["org"]):
                return e
        if re.search(r"\byour internship\b", low):
            interns = [e for e in self.index.experiences if e["kind"] == "internship"]
            if len(interns) == 1:
                return interns[0]
        return None

    def resolve(self, text: str, cls: Classification, focus: FocusState) -> ResolvedQuestion:
        techs = find_technologies(text)
        aspect = detect_aspect(text)
        new_focus = focus.model_copy(deep=True)
        refs: list[str] = []

        project = self._match_project(text, techs, cls)
        experience = None if project else self._match_experience(text)
        is_follow_up = cls.type == Q.FOLLOW_UP

        if project:
            if project["id"] != focus.project_id:
                new_focus.technology = None
            new_focus.project_id, new_focus.project_name = project["id"], project["name"]
            new_focus.project_technologies = list(project["technologies"])
            new_focus.experience_id = new_focus.experience_name = None
            new_focus.turns_since_focus = 0
            if project["id"] != focus.project_id:
                new_focus.topic = topic_for_technologies(project["technologies"])
        elif experience:
            new_focus.experience_id, new_focus.experience_name = experience["id"], experience["name"]
            new_focus.project_id = new_focus.project_name = None
            new_focus.project_technologies = []
            new_focus.turns_since_focus = 0
        elif is_follow_up:
            new_focus.turns_since_focus += 1
        elif cls.type in (Q.PROJECT, Q.RESUME):
            # asked about a project/credential we can't match: never keep answering
            # about the previously discussed project
            new_focus.project_id = new_focus.project_name = None
            new_focus.project_technologies = []
            new_focus.experience_id = new_focus.experience_name = None
            new_focus.turns_since_focus = 0
        elif cls.type in (Q.INTRODUCTION, Q.BEHAVIORAL, Q.HR, Q.GREETING, Q.SITUATIONAL) or (
            cls.type.value in {"TECHNICAL", "CODING", "DSA", "SYSTEM_DESIGN", "MACHINE_LEARNING", "DEEP_LEARNING",
                               "GENERATIVE_AI", "RAG", "DATABASE", "CLOUD", "DEVOPS", "SECURITY"}
            and not re.search(r"\b(you|your)\b", text, re.I)
        ):
            # new, self-contained question: drop the project/experience focus
            new_focus.project_id = new_focus.project_name = None
            new_focus.project_technologies = []
            new_focus.experience_id = new_focus.experience_name = None
            new_focus.technology = None
            new_focus.turns_since_focus = 0

        if techs:
            new_focus.technology = techs[0]
            new_focus.technology_turns = 0
        else:
            new_focus.technology_turns += 1
        if cls.topic and not (project and cls.type == Q.PROJECT and cls.topic in ("PROJECT", None)):
            new_focus.topic = cls.topic
        elif techs:
            new_focus.topic = topic_for_technology(techs[0]) or new_focus.topic

        resolved = text.strip()
        if is_follow_up or (cls.requires_previous_turn_context and (new_focus.project_name or new_focus.experience_name)):
            subject = new_focus.project_name or new_focus.experience_name
            # "why did you choose that/it?" -> the technology under discussion
            # only when the technology was named in the immediately preceding question
            if (_REF_WORD.search(resolved) and not techs and focus.technology and focus.technology_turns == 0
                    and aspect == "why_choice"):
                resolved = _REF_WORD.sub(focus.technology, resolved, count=1)
                refs.append(f"that -> {focus.technology}")
                new_focus.technology = focus.technology
            if subject and subject.lower() not in resolved.lower():
                kind = "project" if new_focus.project_name else "role"
                connector = "for" if aspect in ("why_choice",) else "in"
                resolved = resolved.rstrip("?.! ") + f" {connector} the {subject} {kind}?"
                refs.append(f"context -> {subject}")
            if not subject and focus.last_question and len(resolved.split()) <= 6:
                resolved = f"{resolved.rstrip('?.! ')} (following up on: {focus.last_question})"
                refs.append("context -> previous question")

        new_focus.last_question = text.strip()
        new_focus.last_question_type = cls.type.value
        return ResolvedQuestion(original=text.strip(), resolved=resolved, is_follow_up=is_follow_up,
                                focus=new_focus, references=refs, aspect=aspect)


class ConversationManager:
    """Owns one interview session's conversational state."""

    def __init__(self, db: Session, session: InterviewSession, profile: CandidateProfile | None) -> None:
        self.db = db
        self.session = session
        self.profile = profile
        self.index = ProfileIndex.from_profile(profile)
        self.resolver = ReferenceResolver(self.index)
        state = dict(session.state or {})
        self.focus = FocusState(**state.get("focus", {}))
        self.recent: list[dict[str, Any]] = state.get("recent", [])
        self.questions_since_summary: int = state.get("questions_since_summary", 0)
        self.asked: list[str] = state.get("asked", [])

    # ---------------------------------------------------------------- persistence

    def _save_state(self) -> None:
        self.session.state = {
            **(self.session.state or {}),
            "focus": self.focus.model_dump(),
            "recent": self.recent[-SHORT_TERM_TURNS * 2:],
            "questions_since_summary": self.questions_since_summary,
            "asked": self.asked[-200:],
        }

    def next_seq(self) -> int:
        current = self.db.scalar(select(func.max(ConversationTurn.seq)).where(ConversationTurn.session_id == self.session.id))
        return (current or 0) + 1

    def store_turn(self, role: str, text: str, kind: str = "utterance", meta: dict | None = None,
                   question_id: str | None = None, answer_id: str | None = None) -> ConversationTurn:
        turn = ConversationTurn(session_id=self.session.id, seq=self.next_seq(), role=role, text=text, kind=kind,
                                meta=meta or {}, question_id=question_id, answer_id=answer_id)
        self.db.add(turn)
        self.db.flush()
        self.recent.append({"role": role, "text": text[:1200], "seq": turn.seq, "kind": kind,
                            "type": (meta or {}).get("classification", {}).get("type")})
        self._save_state()
        return turn

    # ---------------------------------------------------------------- pipeline steps

    def receive_transcript(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def classifier_context(self) -> ClassifierContext:
        return ClassifierContext(
            focus=self.focus,
            project_names=[p["name"] for p in self.index.projects],
            experience_orgs=[e["org"] for e in self.index.experiences if e["org"]],
            has_history=any(r["role"] == "interviewer" for r in self.recent),
            project_technologies={p["name"]: p["technologies"] for p in self.index.projects},
        )

    def classify_utterance(self, text: str) -> Classification:
        return classify(text, self.classifier_context())

    def detect_follow_up(self, cls: Classification) -> bool:
        return cls.type == Q.FOLLOW_UP or cls.requires_previous_turn_context

    def update_context(self, text: str, cls: Classification) -> ResolvedQuestion:
        resolved = self.resolver.resolve(text, cls, self.focus)
        self.focus = resolved.focus
        if cls.is_question:
            self.questions_since_summary += 1
            self.asked.append(text.strip().lower())
        self._save_state()
        return resolved

    def record_answer(self, answer_text: str) -> None:
        self.focus.last_answer = answer_text[:1500]
        self._save_state()

    def short_term_memory(self, exclude_last: bool = True) -> list[dict[str, Any]]:
        """Most recent exchanges (bounded), excluding the question being answered."""
        items = self.recent[:-1] if exclude_last and self.recent else list(self.recent)
        return [r for r in items if r["kind"] != "system"][-SHORT_TERM_TURNS:]

    def retrieve_relevant_history(self) -> list[dict[str, Any]]:
        """Long-term summaries for this session (most recent last)."""
        rows = self.db.scalars(select(SessionSummary).where(SessionSummary.session_id == self.session.id)
                               .order_by(SessionSummary.upto_seq)).all()
        return [r.data for r in rows][-3:]

    # ---------------------------------------------------------------- long-term memory

    def maybe_summarize(self, weak_topics: list[str] | None = None) -> dict[str, Any] | None:
        if self.questions_since_summary < SUMMARY_EVERY:
            return None
        last = self.db.scalar(select(func.max(SessionSummary.upto_seq)).where(SessionSummary.session_id == self.session.id)) or 0
        turns = self.db.scalars(select(ConversationTurn).where(
            ConversationTurn.session_id == self.session.id, ConversationTurn.seq > last).order_by(ConversationTurn.seq)).all()
        if not turns:
            return None
        data = summarize_turns(turns, weak_topics or [])
        self.db.add(SessionSummary(session_id=self.session.id, upto_seq=turns[-1].seq, data=data))
        self.questions_since_summary = 0
        self._save_state()
        self.db.flush()
        return data


def summarize_turns(turns: list[ConversationTurn], weak_topics: list[str]) -> dict[str, Any]:
    """Deterministic extractive summary of a block of turns (no LLM call in the
    live path): topics, facts discussed, candidate claims, open questions."""
    topics: dict[str, int] = {}
    facts: list[str] = []
    claims: list[str] = []
    concerns: list[str] = []
    unanswered: list[str] = []
    for t in turns:
        meta = t.meta or {}
        if t.role == "interviewer":
            topic = (meta.get("classification") or {}).get("topic")
            if topic:
                topics[topic] = topics.get(topic, 0) + 1
            if re.search(r"\b(why not|but|concern|are you sure|really|however)\b", t.text, re.I):
                concerns.append(t.text)
        elif t.role == "candidate":
            first = re.split(r"(?<=[.!?])\s", t.text.strip(), maxsplit=1)[0]
            claims.append(first[:240])
            facts += find_technologies(t.text)
            if meta.get("insufficient_context"):
                q = meta.get("question")
                if q:
                    unanswered.append(q)
    main_topic = max(topics.items(), key=lambda kv: kv[1])[0] if topics else None
    return {
        "topic": main_topic,
        "topics": topics,
        "facts_discussed": list(dict.fromkeys(facts))[:20],
        "candidate_claims": claims[-8:],
        "interviewer_concerns": concerns[-5:],
        "unanswered_questions": unanswered[-5:],
        "weak_areas": weak_topics[:5],
        "from_seq": turns[0].seq,
        "to_seq": turns[-1].seq,
    }
