"""Prompt construction for the answer agent.

The system prompt is fixed text (no timestamps / ids) so it is cacheable;
everything that varies per question goes into the user message."""

from __future__ import annotations

import json

from app.models.domain import Classification, ContextBundle, QuestionType, ResolvedQuestion

Q = QuestionType

META_DELIMITER = "<<<META>>>"

SYSTEM_PROMPT = f"""You are InterviewOS, an interview coach that drafts what a specific candidate could say out loud in reply to an interviewer. You write in the candidate's own voice (first person), using only what is known about them.

Grounding rules - these override everything else:
1. Candidate-specific statements (their projects, roles, employers, responsibilities, technologies they used, results, metrics, certifications, titles, publications, dates) must be supported by the CANDIDATE EVIDENCE or FOCUS PROJECT sections, or by what the candidate already said earlier in this interview (HISTORY). Never invent any of these. Never add numbers, percentages, team sizes, user counts or durations that are not in the evidence.
2. Facts marked [unverified] were extracted automatically and not yet confirmed by the candidate; you may use them, but prefer verified facts.
3. General technical knowledge (how a technology works, trade-offs, definitions) may come from TECHNICAL REFERENCE or your own knowledge, but must be phrased as general knowledge, not as something the candidate measured or did.
4. When the evidence does not contain what the question asks for (for example a specific challenge, a metric, a reason for a decision, a personal story), do NOT make it up. Start the answer with exactly one sentence that tells the candidate what is missing, beginning with "Your resume doesn't" or "Your project information doesn't", then give a short, safe way they could answer, such as discussing something they genuinely experienced. Mark this case in the metadata.
5. If the candidate used a technology but the evidence does not say why they chose it, you may explain why it fits the design they built, but do not claim comparisons or benchmarks they did not do.

Style rules:
- Sound like a person speaking in an interview: plain words, contractions, short sentences, no headings, no bullet points, no markdown in the answer.
- Prefer "I chose Qdrant mainly because..." over "The utilization of Qdrant was strategically selected...". Avoid filler and buzzwords.
- Follow the requested structure and stay close to the target length.
- Resolve follow-up references using the conversation so far; do not repeat a previous answer verbatim.

Output format:
First the spoken answer as plain text. Then a line containing only {META_DELIMITER} followed by one line of JSON:
{{"key_points": ["3-5 short phrases"], "star": {{"situation": "", "task": "", "action": "", "result": ""}}, "insufficient_context": false, "notes": ["optional coaching notes for the candidate, never spoken"]}}
Fill "star" only for behavioural answers; otherwise use an empty object."""


STRUCTURES: dict[str, list[str]] = {
    "behavioral": ["Situation", "Task", "Action", "Result"],
    "project": ["Problem", "Approach", "Architecture", "Your contribution", "Challenge", "Solution", "Result"],
    "technical": ["Direct answer", "Explanation", "Example", "Trade-off", "Practical relevance"],
    "system_design": ["Requirements", "Assumptions", "Architecture", "Components", "Data flow", "Scaling",
                      "Reliability", "Security", "Trade-offs"],
    "coding": ["Approach", "Algorithm", "Complexity", "Edge cases", "Implementation"],
    "introduction": ["Who you are", "Education", "Most relevant projects/experience", "Why this role"],
    "hr": ["Direct answer", "Evidence from background", "Link to the role"],
    "follow_up": ["Direct answer to the follow-up", "Supporting detail from the project", "Brief wrap-up"],
    "direct": ["Direct answer"],
}

LENGTH_WORDS = {"20s": 50, "45s": 110, "90s": 220, "detailed": 360}
VARIANT_INSTRUCTIONS = {
    "default": "",
    "regenerate": "Write a fresh version with different wording and ordering than a typical first draft.",
    "shorter": "Make it noticeably shorter and tighter than usual, keeping only the most important points.",
    "longer": "Go into more depth with additional supporting detail from the evidence.",
    "technical": "Be more technically precise: name components, data flow, algorithms and trade-offs.",
    "natural": "Make it sound even more relaxed and conversational, like natural speech.",
}


def structure_for(cls: Classification) -> str:
    t = cls.type
    if t == Q.BEHAVIORAL or t == Q.SITUATIONAL:
        return "behavioral"
    if t in (Q.PROJECT, Q.RESUME):
        return "project"
    if t == Q.SYSTEM_DESIGN:
        return "system_design"
    if t in (Q.CODING, Q.DSA):
        return "coding"
    if t == Q.INTRODUCTION:
        return "introduction"
    if t == Q.HR:
        return "hr"
    if t == Q.FOLLOW_UP:
        return "follow_up"
    if t in (Q.GREETING, Q.CLARIFICATION, Q.UNKNOWN):
        return "direct"
    return "technical"


def target_words(length: str, variant: str) -> int:
    base = LENGTH_WORDS.get(length, 110)
    if variant == "shorter":
        return max(30, int(base * 0.6))
    if variant == "longer":
        return int(base * 1.6)
    return base


def _fmt_chunk(prefix: str, i: int, c) -> str:
    tag = "" if c.verified or c.collection in ("technical_knowledge", "job_descriptions", "interview_history") else " [unverified]"
    return f"[{prefix}{i}]{tag} {c.text}"


def build_user_prompt(
    question: str,
    resolved: ResolvedQuestion,
    cls: Classification,
    bundle: ContextBundle,
    recent_turns: list[dict],
    summaries: list[dict],
    length: str,
    variant: str,
    words: int | None = None,
) -> str:
    parts: list[str] = []
    who = bundle.candidate_name or "the candidate"
    parts.append(f"CANDIDATE: {who}" + (f" - {bundle.headline}" if bundle.headline else ""))
    if bundle.verified_skills:
        parts.append("VERIFIED SKILLS: " + ", ".join(bundle.verified_skills[:40]))
    if bundle.focus_project:
        fp = bundle.focus_project.model_dump(exclude={"id"})
        fp = {k: v for k, v in fp.items() if v not in (None, [], "")}
        status = "verified" if bundle.focus_project.verified else "unverified"
        parts.append(f"FOCUS PROJECT ({status}; empty fields are UNKNOWN - do not invent them):\n"
                     + json.dumps(fp, ensure_ascii=False))
        missing = [k for k in ("challenges", "candidate_role", "results", "metrics", "limitations",
                               "future_work", "testing") if not getattr(bundle.focus_project, k)]
        if missing:
            parts.append("UNKNOWN FOR THIS PROJECT: " + ", ".join(missing))
    if bundle.candidate:
        parts.append("CANDIDATE EVIDENCE:\n" + "\n".join(_fmt_chunk("C", i + 1, c) for i, c in enumerate(bundle.candidate)))
    else:
        parts.append("CANDIDATE EVIDENCE: (none retrieved)")
    if bundle.history:
        parts.append("HISTORY (earlier in this interview):\n" + "\n".join(_fmt_chunk("H", i + 1, c) for i, c in enumerate(bundle.history)))
    if bundle.job:
        parts.append(f"TARGET JOB ({bundle.job_title or 'role'}):\n" + "\n".join(_fmt_chunk("J", i + 1, c) for i, c in enumerate(bundle.job)))
    if bundle.technical:
        parts.append("TECHNICAL REFERENCE:\n" + "\n".join(_fmt_chunk("T", i + 1, c) for i, c in enumerate(bundle.technical)))
    if summaries:
        parts.append("EARLIER IN THIS INTERVIEW (summary): " + json.dumps(summaries[-1], ensure_ascii=False)[:1200])
    if recent_turns:
        convo = "\n".join(f"{t['role'].upper()}: {t['text'][:500]}" for t in recent_turns)
        parts.append("RECENT CONVERSATION:\n" + convo)

    structure = structure_for(cls)
    words = words or target_words(length, variant)
    q = [f'INTERVIEWER JUST ASKED: "{question}"']
    if resolved.resolved != question:
        q.append(f"Resolved meaning: {resolved.resolved}")
    q.append(f"Question type: {cls.type.value}; topic: {cls.topic or 'general'}; difficulty: {cls.difficulty}"
             + (f"; asks about: {resolved.aspect}" if resolved.aspect else ""))
    q.append(f"Structure: {' -> '.join(STRUCTURES[structure])} (spoken, not as headings)")
    q.append(f"Target length: about {words} words (~{round(words / 2.5)} seconds spoken).")
    if VARIANT_INSTRUCTIONS.get(variant):
        q.append(VARIANT_INSTRUCTIONS[variant])
    parts.append("\n".join(q))
    return "\n\n".join(parts)


def max_tokens_for(length: str, variant: str) -> int:
    return int(target_words(length, variant) * 2.2) + 400
