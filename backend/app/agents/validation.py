"""Validation agent: answer grounding.

Method (`lexical-entity-v1`), applied to every generated answer:
1. Claim extraction - split the answer into sentences; each is a claim.
2. Claim typing - a claim is *candidate-specific* if it speaks about the
   candidate (first person, the candidate's projects / employers /
   certifications); otherwise it is *general* knowledge. Coaching sentences
   addressed to the candidate ("Your resume doesn't...") are excluded.
3. Evidence retrieval - candidate claims are checked only against the
   candidate's knowledge base and what the candidate said earlier in the
   session (never against technical reference text).
4. Verification - hard entities (numbers/metrics, technologies, proper
   nouns) must all appear in the evidence; content-word coverage against the
   best evidence chunk must clear a threshold.
5. Rewrite - unsupported candidate claims are removed from the final answer.

Grounding score = (claims supported by verified evidence + 0.5 x claims
supported only by unverified evidence) / candidate-specific claims. It is
`None` when an answer makes no candidate-specific claims. Thresholds are
calibrated by the tests and the evaluation benchmark - the score is a
measured lexical support ratio, not a probability.
"""

from __future__ import annotations

import re

from app.models.domain import Claim, ContextBundle, GroundingReport, RetrievedChunk
from app.rag.embeddings import tokenize
from app.services.taxonomy import find_technologies

_FIRST_PERSON = re.compile(r"\b(I(?!/)|I'm|I've|I'd|I'll)\b|\b(?i:my|me|mine|myself|we|our)\b")
# first person used only hypothetically ("I'd start by...", "I would use...") is an
# approach, not a factual claim about the candidate's past
_HYPOTHETICAL = re.compile(r"\b(I'd|I would|I'll|I will|I could|I might|I'd like|if I)\b")
_FACTUAL_FP = re.compile(r"\b(I(?!/)(?!'d\b)(?!'ll\b)(?! would\b)(?! will\b)(?! could\b)(?! might\b)|I'm|I've)\b|\b(?i:my|mine|myself|we|our)\b")
_META = re.compile(r"^(your (resume|project|profile|knowledge base)|a safe way|don't|be upfront|then explain|add (the|one|a)|"
                   r"offline mode|configure)\b", re.I)
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|x\b|k\b|m\b|ms\b|s\b|hours?|days?|weeks?|months?|years?|users?|requests?)?", re.I)
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z0-9&.+-]*(?:\s+(?:of|for|and|&)?\s*[A-Z][a-zA-Z0-9&.+-]*)*)")
_PROPER_IGNORE = {
    "I", "I'm", "I've", "I'd", "I'll", "In", "The", "It", "My", "For", "On", "At", "And", "But", "So", "That",
    "This", "Then", "When", "While", "One", "Another", "Right", "Sure", "To", "With", "From", "Generally",
    "General", "Monday", "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Situation", "Task", "Action", "Result", "Also", "After", "Before",
    "Overall", "Finally", "First", "Second", "Third", "Next", "Plus", "Because", "Since", "If", "As", "We",
    "Our", "Yes", "No", "Hi", "Thanks", "Hello", "What", "Why", "How", "Which", "Where", "Who", "Here", "There",
    "These", "Those", "Each", "Every", "Most", "Some", "Any", "All", "Both", "Once", "Over", "Under", "By",
    "Academically", "Now", "Today", "Later", "Earlier", "AI", "API", "APIs", "ML",
}
_METRIC_CLAIM = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:%|percent|x\b|times|ms\b|milliseconds|seconds|minutes|hours|days|users|customers|"
    r"requests|queries|documents|engineers|people|members|accuracy|precision|recall|k\b|m\b)", re.I)
_CONTINUATION = re.compile(
    r"^(it|this|that|they|these|the (project|system|model|platform|tool|app|application|pipeline|service|api|team))\b",
    re.I)
SUPPORT_THRESHOLD = 0.34
UNION_THRESHOLD = 0.5


def split_claims(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip())
    return [p.strip() for p in parts if len(p.strip().split()) >= 3]


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group(0)).lower().rstrip(".,") for m in _NUMBER.finditer(text)
            if any(ch.isdigit() for ch in m.group(0))}


def _bare_numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def _proper_nouns(text: str, entity_terms: set[str]) -> set[str]:
    out = set()
    for m in _PROPER.finditer(text):
        phrase = m.group(1).strip(" .")
        words = [w for w in phrase.split() if w not in _PROPER_IGNORE]
        if not words:
            continue
        phrase = " ".join(words)
        if find_technologies(phrase) or phrase.lower() in entity_terms:
            continue
        if len(phrase) <= 2:
            continue
        out.add(phrase)
    return out


class GroundingValidator:
    def __init__(self, candidate_entities: list[str] | None = None) -> None:
        # project names, organisations, certification names: mentioning them
        # makes a sentence candidate-specific
        self.entities = [e for e in (candidate_entities or []) if e and len(e) > 2]

    def _is_candidate_claim(self, claim: str, prev_candidate: bool = False) -> bool:
        low = claim.lower()
        # outcome numbers ("it reduced latency by 40%") are claims about the candidate's work
        if _METRIC_CLAIM.search(claim) and (prev_candidate or _CONTINUATION.search(claim)):
            return True
        if any(e.lower() in low for e in self.entities):
            return True
        if not _FIRST_PERSON.search(claim):
            return False
        if _HYPOTHETICAL.search(claim) and not _FACTUAL_FP.search(claim) and not _bare_numbers(claim):
            return False
        return True

    def _support(self, claim: str, evidence: list[RetrievedChunk]) -> Claim:
        ev_text = " \n".join(c.text for c in evidence)
        ev_low = ev_text.lower()
        unsupported: list[str] = []

        ev_numbers = _bare_numbers(ev_text)
        for n in _bare_numbers(claim):
            if n not in ev_numbers:
                unsupported.append(n)
        ev_techs = {t.lower() for t in find_technologies(ev_text)}
        for t in find_technologies(claim):
            if t.lower() not in ev_techs and t.lower() not in ev_low:
                unsupported.append(t)
        for p in _proper_nouns(claim, {e.lower() for e in self.entities}):
            if p.lower() not in ev_low and not all(w.lower() in ev_low for w in p.split()):
                unsupported.append(p)

        c_tokens = {t for t in tokenize(claim) if len(t) > 2}
        best, best_ids, verified_best = 0.0, [], False
        union: set[str] = set()
        for chunk in evidence:
            e_tokens = set(tokenize(chunk.text))
            if not c_tokens:
                break
            cov = len(c_tokens & e_tokens) / len(c_tokens)
            if cov > 0:
                union |= c_tokens & e_tokens
            if cov > best + 1e-9:
                best, best_ids, verified_best = cov, [chunk.id], chunk.verified
            elif abs(cov - best) < 1e-9 and cov > 0:
                best_ids.append(chunk.id)
                verified_best = verified_best or chunk.verified
        union_cov = len(union) / len(c_tokens) if c_tokens else 1.0
        supported = not unsupported and (best >= SUPPORT_THRESHOLD or union_cov >= UNION_THRESHOLD)
        verified = verified_best or any(
            c.verified or c.collection == "interview_history" for c in evidence if c.id in best_ids)
        return Claim(text=claim, kind="candidate", supported=supported, support=round(max(best, union_cov * 0.9), 3),
                     evidence_ids=best_ids[:3], unsupported_terms=unsupported, verified_support=verified)

    def validate(self, answer: str, bundle: ContextBundle, rewrite: bool = True) -> tuple[str, GroundingReport]:
        evidence = bundle.candidate_evidence()
        claims: list[Claim] = []
        kept: list[str] = []
        removed: list[str] = []
        score_num = 0.0
        candidate_claims = 0
        hard_failure = False
        prev_candidate = False
        for sentence in split_claims(answer):
            if _META.search(sentence):
                kept.append(sentence)
                prev_candidate = False
                continue
            if not self._is_candidate_claim(sentence, prev_candidate):
                claims.append(Claim(text=sentence, kind="general", supported=True))
                kept.append(sentence)
                prev_candidate = False
                continue
            prev_candidate = True
            candidate_claims += 1
            c = self._support(sentence, evidence)
            claims.append(c)
            if c.supported:
                score_num += 1.0 if c.verified_support else 0.5
                kept.append(sentence)
            else:
                if c.unsupported_terms:
                    hard_failure = True
                removed.append(sentence)
        # sentences shorter than 3 words were not claims; keep them in place
        report = GroundingReport(
            grounding_score=round(score_num / candidate_claims, 3) if candidate_claims else None,
            supported_claims=sum(1 for c in claims if c.kind == "candidate" and c.supported),
            unsupported_claims=sum(1 for c in claims if c.kind == "candidate" and not c.supported),
            general_claims=sum(1 for c in claims if c.kind == "general"),
            claims=claims,
        )
        if report.unsupported_claims == 0 and (report.grounding_score is None or report.grounding_score >= 0.75):
            report.risk = "low"
        elif hard_failure or (report.grounding_score is not None and report.grounding_score < 0.5):
            report.risk = "high"
        else:
            report.risk = "medium"
        final = answer
        if rewrite and removed:
            report.removed_claims = removed
            report.rewritten = True
            final = " ".join(kept).strip()
            if not final or len(kept) < max(1, len(kept) + len(removed)) * 0.4:
                final = ("Your resume doesn't provide enough information to answer this specifically. "
                         "A safe way to answer is to talk only about what you actually did. " + final).strip()
        return final, report
