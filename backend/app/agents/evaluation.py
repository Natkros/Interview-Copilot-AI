"""Evaluation agent: scores answers independently of the generator.

`heuristic-v1` is deterministic and runs on every answer (live suggestions,
mock-interview responses and practice attempts). Each metric is a documented
formula over observable properties of the answer, the question and the
grounding report - it does not ask the generating model to grade itself.
Optionally (LLM_JUDGE_ENABLED=true) a separate LLM judge call refines
correctness and relevance for practice attempts.
"""

from __future__ import annotations

import json
import logging
import re

from app.agents.prompts import target_words
from app.models.domain import (
    TECHNICAL_TYPES,
    Classification,
    EvaluationScores,
    GroundingReport,
    QuestionType,
)
from app.rag.embeddings import tokenize

log = logging.getLogger(__name__)
Q = QuestionType

_JARGON = re.compile(r"\b(utiliz\w*|leverag\w*|strategically|synerg\w*|paradigm|furthermore|moreover|henceforth|"
                     r"in conclusion|it is worth noting|robust and scalable|cutting-edge|state-of-the-art|"
                     r"seamless(ly)?|holistic|spearhead\w*)\b", re.I)
_HEDGES = re.compile(r"\b(maybe|perhaps|i think|i guess|probably|kind of|sort of|not sure|i believe|possibly|"
                     r"somewhat|i don'?t know)\b", re.I)
_CONTRACTIONS = re.compile(r"\b\w+'(m|s|re|ve|d|ll|t)\b", re.I)
_MARKDOWN = re.compile(r"(^|\n)\s*([-*#]|\d+\.)\s|\*\*")

_STRUCTURE_CUES: dict[str, list[str]] = {
    "behavioral": [r"\b(while|when|at the time|situation|working on)\b", r"\b(needed to|had to|goal|task|responsible)\b",
                   r"\b(i (built|added|decided|fixed|solved|led|wrote|created|changed|talked|organized|reached))\b",
                   r"\b(result|outcome|improv|reduc|increas|learned|ended up|as a result)\w*"],
    "project": [r"\b(problem|goal|designed to|aims? to|helps?|enables?|platform|tool|app)\b",
                r"\b(i (built|designed|implemented|developed|created|trained|deployed|stored))\b",
                r"\b(architecture|pipeline|backend|frontend|api|database|model|index)\b",
                r"\b(challenge|difficult|result|accuracy|faster|improv|reduc)\w*"],
    "technical": [r"\b(is|are|means|refers to)\b", r"\b(because|so that|which|works by|uses)\b",
                  r"\b(for example|e\.g\.|such as|like|in my)\b", r"\b(trade-?off|but|however|downside|cost|whereas|versus)\b"],
    "system_design": [r"\brequirement", r"\b(load balancer|api|server|database|cache|queue)\b", r"\bscal",
                      r"\b(reliab|replica|fail|retry)", r"\b(security|auth|rate limit)", r"\btrade-?off"],
    "coding": [r"\b(approach|idea|first)\b", r"\b(complexity|o\()", r"\bedge case", r"\b(loop|pointer|hash|array|recurs|stack|queue)"],
}


def _clamp(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 3)


def _structure_key(t: QuestionType) -> str:
    if t in (Q.BEHAVIORAL, Q.SITUATIONAL):
        return "behavioral"
    if t in (Q.PROJECT, Q.RESUME, Q.FOLLOW_UP, Q.INTRODUCTION):
        return "project"
    if t == Q.SYSTEM_DESIGN:
        return "system_design"
    if t in (Q.CODING, Q.DSA):
        return "coding"
    return "technical"


def evaluate_answer(
    question: str,
    answer: str,
    cls: Classification,
    grounding: GroundingReport | None,
    job_keywords: list[str] | None = None,
    length: str = "45s",
    insufficient_context: bool = False,
    technical_evidence: list[str] | None = None,
    is_candidate_spoken: bool = False,
) -> EvaluationScores:
    notes: list[str] = []
    words = re.findall(r"\b[\w'+#.-]+\b", answer)
    wc = len(words)
    sents = [s for s in re.split(r"(?<=[.!?])\s+", answer.strip()) if s.strip()]

    # relevance: share of the question's content terms addressed by the answer
    q_terms = {t for t in tokenize(question) if len(t) > 2}
    a_terms = set(tokenize(answer))
    overlap = len(q_terms & a_terms) / len(q_terms) if q_terms else 1.0
    relevance = _clamp(0.35 + 0.65 * min(1.0, overlap * 1.4)) if wc else 0.0
    if insufficient_context:
        relevance = max(relevance, 0.6)

    # grounding / hallucination risk
    if grounding and grounding.grounding_score is not None:
        g = grounding.grounding_score
        total = grounding.supported_claims + grounding.unsupported_claims
        halluc = grounding.unsupported_claims / total if total else 0.0
    else:
        g = 1.0
        halluc = 0.0
    if grounding and grounding.risk == "high":
        halluc = max(halluc, 0.6)

    # correctness: for technical questions, agreement with reference material;
    # for personal questions, grounding is the correctness signal
    if cls.type in TECHNICAL_TYPES and technical_evidence:
        ref = set()
        for e in technical_evidence:
            ref |= set(tokenize(e))
        cov = len(a_terms & ref) / len(a_terms) if a_terms else 0.0
        correctness = _clamp(0.4 + 0.6 * min(1.0, cov * 1.6))
    else:
        correctness = _clamp(g)

    # completeness: expected structural elements present
    cues = _STRUCTURE_CUES[_structure_key(cls.type)]
    hits = sum(1 for c in cues if re.search(c, answer, re.I))
    completeness = _clamp(hits / len(cues))
    if insufficient_context:
        completeness = min(completeness, 0.5)
        notes.append("The profile lacked information for part of this question.")

    # clarity: sentence length close to spoken norms (10-22 words)
    if sents:
        avg = wc / len(sents)
        clarity = _clamp(1.0 - max(0.0, avg - 22) / 25 - max(0.0, 8 - avg) / 20)
        long_sents = sum(1 for s in sents if len(s.split()) > 35)
        clarity = _clamp(clarity - 0.1 * long_sents)
    else:
        clarity = 0.0

    # conciseness: distance from target length
    target = target_words(length, "default")
    conciseness = _clamp(1.0 - abs(wc - target) / max(target, 1)) if wc else 0.0
    if wc > target * 1.6:
        notes.append(f"Long answer ({wc} words) for a {length} target.")

    # naturalness: spoken register
    jargon = len(_JARGON.findall(answer))
    contractions = len(_CONTRACTIONS.findall(answer))
    first_person = len(re.findall(r"\b(I|my|I'm|I've|I'd)\b", answer))
    naturalness = 0.65 + min(0.15, contractions * 0.04) + (0.1 if first_person else 0) - 0.12 * jargon
    if _MARKDOWN.search(answer):
        naturalness -= 0.25
        notes.append("Contains list/markdown formatting, which doesn't read naturally aloud.")
    naturalness = _clamp(naturalness)
    if jargon:
        notes.append("Uses stiff or buzzword phrasing; prefer plain spoken language.")

    # job alignment
    job_alignment = None
    if job_keywords:
        kws = [k.lower() for k in job_keywords]
        low = answer.lower()
        present = sum(1 for k in kws if k in low)
        job_alignment = _clamp(0.3 + 0.7 * min(1.0, present / max(1, min(4, len(kws)))))

    # confidence: hedging language (most relevant for the candidate's own answers)
    hedges = len(_HEDGES.findall(answer))
    confidence = _clamp(1.0 - 0.15 * hedges)
    if is_candidate_spoken and hedges >= 2:
        notes.append("Several hedging phrases; state what you did directly.")

    weights = {"relevance": 0.18, "correctness": 0.18, "grounding": 0.16, "completeness": 0.12, "clarity": 0.08,
               "conciseness": 0.07, "naturalness": 0.08, "confidence": 0.05, "job_alignment": 0.08}
    vals = {"relevance": relevance, "correctness": correctness, "grounding": _clamp(g), "completeness": completeness,
            "clarity": clarity, "conciseness": conciseness, "naturalness": naturalness, "confidence": confidence,
            "job_alignment": job_alignment}
    total_w = sum(w for k, w in weights.items() if vals[k] is not None)
    overall = sum(weights[k] * v for k, v in vals.items() if v is not None) / total_w
    # an answer that doesn't address the question can't be good, however fluent
    overall = _clamp(overall * (1 - 0.5 * halluc) * (0.6 + 0.4 * relevance))
    return EvaluationScores(
        relevance=relevance, correctness=correctness, grounding=_clamp(g), completeness=completeness,
        clarity=clarity, conciseness=conciseness, naturalness=naturalness, job_alignment=job_alignment,
        confidence=confidence, hallucination_risk=_clamp(halluc), overall=overall, notes=notes,
    )


JUDGE_SYSTEM = """You grade interview answers. Return only JSON: {"relevance": 0-1, "correctness": 0-1, "completeness": 0-1, "feedback": ["short actionable tips"]}. Grade the answer against the question; for factual technical content judge correctness of the claims; be strict and concise."""


async def llm_judge(question: str, answer: str) -> dict | None:
    """Optional independent LLM judge (LLM_JUDGE_ENABLED=true)."""
    from app.agents.llm import LLMError, get_llm
    from app.core.config import get_settings

    if not get_settings().llm_judge_enabled:
        return None
    llm = get_llm()
    if llm is None:
        return None
    try:
        res = await llm.complete(JUDGE_SYSTEM, f"Question: {question}\n\nAnswer: {answer}", 600)
        m = re.search(r"\{.*\}", res.text, re.S)
        return json.loads(m.group(0)) if m else None
    except (LLMError, json.JSONDecodeError) as exc:
        log.warning("llm judge failed", extra={"error": str(exc)})
        return None
