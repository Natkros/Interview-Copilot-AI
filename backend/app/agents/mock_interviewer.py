"""Mock interviewer agent.

Asks questions from the personalised question bank, listens to the
candidate's answer, scores it with the evaluation agent, then either asks a
follow-up (simpler after a weak answer, deeper after a strong one) or moves
on - favouring the candidate's weakest categories and never repeating a
question within a session.
"""

from __future__ import annotations

import random
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import LLMError, get_llm
from app.database.models import CandidateProfile, InterviewSession, JobDescription, QuestionBankItem
from app.models.domain import EvaluationScores
from app.services.question_bank import rebuild_question_bank

TECH_CATEGORIES = ["Python", "C++", "Java", "ML", "DL", "GenAI", "RAG", "Databases", "Cloud", "DevOps"]
MODE_CATEGORIES: dict[str, list[str]] = {
    "mock": ["Projects", "Resume", "Behavioral", "HR", *TECH_CATEGORIES, "System Design", "DSA"],
    "resume_drill": ["Projects", "Resume"],
    "technical": TECH_CATEGORIES,
    "dsa": ["DSA"],
    "system_design": ["System Design"],
    "behavioral": ["Behavioral"],
    "hr": ["HR"],
    "job_specific": ["Job", "Projects"],
}
MOCK_MODES = set(MODE_CATEGORIES)
DIFFICULTIES = ["easy", "medium", "hard"]

_DEEPER = {
    "Projects": ["Why did you choose that approach over the alternatives?",
                 "What would break first if usage grew ten times?",
                 "If you rebuilt it today, what would you change and why?"],
    "Resume": ["What was the hardest part of that, specifically?", "How did you measure whether it worked?"],
    "System Design": ["How would the design change at a hundred times the traffic?",
                      "What happens when your primary database goes down?",
                      "Where would you add caching, and what are the invalidation risks?"],
    "DSA": ["Can you improve the time or space complexity?", "What edge cases would break a naive version?"],
    "Behavioral": ["What would you do differently if it happened again?", "How did the other people involved react?"],
    "HR": ["Can you give me a concrete example of that?"],
    "Job": ["Can you give a concrete example from your own work?"],
}
_DEEPER_TECH = ["What are the trade-offs or failure modes of that approach?",
                "Can you give a concrete example of where you'd use that?",
                "How would you explain that to a junior engineer?"]
_SIMPLER = {
    "Projects": "Let's narrow it down. What is one specific thing you built yourself in that project?",
    "Resume": "Let's make it concrete. What was one task you did there day to day?",
    "Behavioral": "Let's keep it simple. What was the situation, and what did you personally do?",
    "HR": "Take a moment. Can you answer that in one or two direct sentences?",
    "System Design": "Let's start smaller. What are the two or three core components you'd need?",
    "DSA": "Let's simplify. What would a brute-force approach look like?",
}
_DRILL = ["You said that - how exactly did you do it, step by step?",
          "How do you know it worked? What did you check or measure?",
          "What was your personal contribution, as opposed to the team's?",
          "Which part of that was the hardest to get right?"]


class MockInterviewer:
    def __init__(self, db: Session, session: InterviewSession, profile: CandidateProfile | None,
                 job: JobDescription | None) -> None:
        self.db = db
        self.session = session
        self.profile = profile
        self.job = job
        st = dict((session.state or {}).get("mock") or {})
        self.state: dict[str, Any] = {
            "asked_ids": st.get("asked_ids", []),
            "asked_text": st.get("asked_text", []),
            "scores": st.get("scores", {}),
            "level": st.get("level", 1),
            "current": st.get("current"),
            "count": st.get("count", 0),
        }
        self.rng = random.Random(f"{session.id}:{self.state['count']}")

    def _save(self) -> None:
        self.session.state = {**(self.session.state or {}), "mock": self.state}

    # ------------------------------------------------------------------ selection

    def _bank(self) -> list[QuestionBankItem]:
        items = list(self.db.scalars(select(QuestionBankItem).where(QuestionBankItem.user_id == self.session.user_id)))
        if not items:
            rebuild_question_bank(self.db, self.session.user_id, self.profile, self.job)
            items = list(self.db.scalars(select(QuestionBankItem).where(QuestionBankItem.user_id == self.session.user_id)))
        return items

    def _categories(self) -> list[str]:
        cats = MODE_CATEGORIES.get(self.session.mode, MODE_CATEGORIES["mock"])
        if self.session.mode in ("mock", "technical") and self.profile:
            skills = {s.name for s in self.profile.skills}
            from app.services.question_bank import _TECH_CATEGORY

            relevant = {_TECH_CATEGORY[s] for s in skills if s in _TECH_CATEGORY}
            cats = [c for c in cats if c not in TECH_CATEGORIES or c in relevant] or cats
        return cats

    def _pick_category(self, cats: list[str]) -> str:
        scores = self.state["scores"]

        def avg(c: str) -> float:
            vals = scores.get(c, [])
            return sum(vals) / len(vals) if vals else 0.6

        asked_counts: dict[str, int] = {}
        for item_cat in self.state.get("asked_cats", []):
            asked_counts[item_cat] = asked_counts.get(item_cat, 0) + 1
        # weakest categories first, then least asked; small randomness to vary sessions
        ranked = sorted(cats, key=lambda c: (avg(c) + 0.08 * asked_counts.get(c, 0) + self.rng.random() * 0.05))
        return ranked[0]

    def _first_question(self) -> dict[str, Any]:
        mode = self.session.mode
        if mode in ("mock", "hr", "resume_drill", "job_specific", "behavioral"):
            return {"text": "Thanks for joining. To start, tell me about yourself.", "category": "HR",
                    "difficulty": "easy", "kind": "question", "bank_id": None}
        return self._new_question()

    def _new_question(self) -> dict[str, Any]:
        bank = self._bank()
        cats = self._categories()
        asked = set(self.state["asked_ids"])
        target = DIFFICULTIES[max(0, min(2, self.state["level"]))]
        for _ in range(len(cats)):
            cat = self._pick_category(cats)
            pool = [i for i in bank if i.category == cat and i.id not in asked]
            if self.job and cat == "Projects":
                pool.sort(key=lambda i: -i.job_relevance)
            if pool:
                exact = [i for i in pool if i.difficulty == target] or pool
                exact.sort(key=lambda i: (-(i.resume_relevance + i.job_relevance), i.attempts))
                top = exact[: max(1, min(3, len(exact)))]
                item = self.rng.choice(top)
                return {"text": item.question, "category": item.category, "difficulty": item.difficulty,
                        "kind": "question", "bank_id": item.id, "concepts": item.expected_concepts}
            cats = [c for c in cats if c != cat] or cats
        return {"text": "That covers what I wanted to ask. Is there anything you'd like to add about your experience?",
                "category": "HR", "difficulty": "easy", "kind": "closing", "bank_id": None}

    # ------------------------------------------------------------------ API

    def opening(self) -> dict[str, Any]:
        q = self._first_question()
        self._remember(q)
        return q

    def _remember(self, q: dict[str, Any]) -> None:
        if q.get("bank_id"):
            self.state["asked_ids"].append(q["bank_id"])
        self.state["asked_text"].append(q["text"].lower())
        self.state.setdefault("asked_cats", []).append(q["category"])
        self.state["current"] = {**q, "followups": 0 if q["kind"] != "follow_up" else
                                 ((self.state.get("current") or {}).get("followups", 0) + 1)}
        self.state["count"] += 1
        self._save()

    async def next_question(self, answer_text: str, scores: EvaluationScores) -> dict[str, Any]:
        cur = self.state.get("current") or {"category": "HR", "followups": 0, "difficulty": "easy", "text": ""}
        cat = cur["category"]
        self.state["scores"].setdefault(cat, []).append(scores.overall)
        # adaptive difficulty
        if scores.overall >= 0.75:
            self.state["level"] = min(2, self.state["level"] + 1)
        elif scores.overall < 0.45:
            self.state["level"] = max(0, self.state["level"] - 1)

        q: dict[str, Any] | None = None
        followups = cur.get("followups", 0)
        drill = self.session.mode == "resume_drill"
        if followups < (2 if drill else 1) and cur.get("kind") != "closing":
            if scores.overall < 0.45 and cat in _SIMPLER:
                q = {"text": _SIMPLER[cat], "category": cat, "difficulty": "easy", "kind": "follow_up", "bank_id": None}
            else:
                missing = [c for c in (cur.get("concepts") or []) if c.lower() not in answer_text.lower()]
                text = await self._llm_followup(cur["text"], answer_text, drill)
                if text is None:
                    if drill:
                        text = self._drill_followup(answer_text)
                    elif scores.overall < 0.75 and missing and cat in TECH_CATEGORIES + ["DSA", "System Design"]:
                        text = f"You didn't mention {missing[0]}. How does that fit in?"
                    elif scores.overall >= 0.6:
                        options = _DEEPER.get(cat, _DEEPER_TECH)
                        text = self.rng.choice([o for o in options if o.lower() not in self.state["asked_text"]] or options)
                if text and text.lower() not in self.state["asked_text"]:
                    q = {"text": text, "category": cat, "difficulty": "hard" if scores.overall >= 0.75 else cur.get("difficulty", "medium"),
                         "kind": "follow_up", "bank_id": None, "concepts": cur.get("concepts")}
        if q is None:
            q = self._new_question()
        self._remember(q)
        return q

    def _drill_followup(self, answer: str) -> str:
        m = re.search(r"\bI (built|designed|implemented|developed|trained|deployed|led|reduced|improved|optimi[sz]ed|wrote|created)\b([^.]{0,80})", answer)
        if m:
            claim = (m.group(1) + m.group(2)).strip().rstrip(",")
            return f"You said you {claim}. How exactly did you do that, step by step?"
        options = [d for d in _DRILL if d.lower() not in self.state["asked_text"]] or _DRILL
        return self.rng.choice(options)

    async def _llm_followup(self, question: str, answer: str, drill: bool) -> str | None:
        llm = get_llm()
        if llm is None:
            return None
        system = ("You are a fair but probing technical interviewer. Given the last question and the candidate's answer, "
                  "ask exactly one short follow-up question (max 25 words) that tests depth or clarifies a vague claim. "
                  "Output only the question.")
        if drill:
            system += " Focus on verifying specific claims the candidate made about their own work."
        try:
            res = await llm.complete(system, f"Question: {question}\nAnswer: {answer[:2000]}", 200)
        except LLMError:
            return None
        text = res.text.strip().strip('"').split("\n")[0]
        return text if 3 <= len(text.split()) <= 40 else None
