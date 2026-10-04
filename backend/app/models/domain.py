"""Typed contracts between subsystems (classifier -> conversation manager ->
retrieval -> answer generation -> grounding -> evaluation -> websocket)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class QuestionType(StrEnum):
    GREETING = "GREETING"
    INTRODUCTION = "INTRODUCTION"
    BEHAVIORAL = "BEHAVIORAL"
    HR = "HR"
    RESUME = "RESUME"
    PROJECT = "PROJECT"
    TECHNICAL = "TECHNICAL"
    CODING = "CODING"
    DSA = "DSA"
    SYSTEM_DESIGN = "SYSTEM_DESIGN"
    MACHINE_LEARNING = "MACHINE_LEARNING"
    DEEP_LEARNING = "DEEP_LEARNING"
    GENERATIVE_AI = "GENERATIVE_AI"
    RAG = "RAG"
    DATABASE = "DATABASE"
    CLOUD = "CLOUD"
    DEVOPS = "DEVOPS"
    SECURITY = "SECURITY"
    SITUATIONAL = "SITUATIONAL"
    FOLLOW_UP = "FOLLOW_UP"
    CLARIFICATION = "CLARIFICATION"
    FEEDBACK = "FEEDBACK"
    STATEMENT = "STATEMENT"
    UNKNOWN = "UNKNOWN"


TECHNICAL_TYPES = {
    QuestionType.TECHNICAL, QuestionType.CODING, QuestionType.DSA, QuestionType.SYSTEM_DESIGN,
    QuestionType.MACHINE_LEARNING, QuestionType.DEEP_LEARNING, QuestionType.GENERATIVE_AI,
    QuestionType.RAG, QuestionType.DATABASE, QuestionType.CLOUD, QuestionType.DEVOPS,
    QuestionType.SECURITY,
}

# Types that never warrant a generated candidate answer.
NON_ANSWERABLE_TYPES = {QuestionType.STATEMENT, QuestionType.FEEDBACK}

Difficulty = Literal["easy", "medium", "hard"]
AnswerLength = Literal["20s", "45s", "90s", "detailed"]
AnswerVariant = Literal[
    "default", "regenerate", "shorter", "longer", "technical", "natural",
]


class Classification(BaseModel):
    type: QuestionType
    topic: str | None = None
    difficulty: Difficulty = "medium"
    is_question: bool = True
    requires_resume_context: bool = False
    requires_technical_context: bool = False
    requires_previous_turn_context: bool = False
    confidence: float = 0.5
    signals: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)  # technologies / project names mentioned


class FocusState(BaseModel):
    """What the conversation is currently 'about' - used to resolve
    references such as "why did you choose that?"."""

    project_id: str | None = None
    project_name: str | None = None
    project_technologies: list[str] = Field(default_factory=list)
    experience_id: str | None = None
    experience_name: str | None = None
    technology: str | None = None
    technology_turns: int = 0
    topic: str | None = None
    last_question: str | None = None
    last_answer: str | None = None
    last_question_type: str | None = None
    turns_since_focus: int = 0


class ResolvedQuestion(BaseModel):
    original: str
    resolved: str
    is_follow_up: bool = False
    focus: FocusState = Field(default_factory=FocusState)
    references: list[str] = Field(default_factory=list)  # e.g. ["that -> Qdrant"]
    aspect: str | None = None  # challenge | role | why_choice | improvement | testing | result | limitation ...


class RetrievedChunk(BaseModel):
    id: str
    collection: str
    source_type: str
    text: str
    score: float = 0.0
    priority: float = 0.5
    verified: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        m = self.meta
        parts = [m.get("source") or self.source_type]
        if m.get("section"):
            parts.append(str(m["section"]))
        if m.get("project"):
            parts.append(str(m["project"]))
        if m.get("field"):
            parts.append(str(m["field"]))
        return " -> ".join(parts)


class ProjectFacts(BaseModel):
    id: str
    name: str
    verified: bool
    description: str | None = None
    problem: str | None = None
    solution: str | None = None
    candidate_role: str | None = None
    architecture: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)
    solutions: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    testing: list[str] = Field(default_factory=list)


class ContextBundle(BaseModel):
    candidate: list[RetrievedChunk] = Field(default_factory=list)
    job: list[RetrievedChunk] = Field(default_factory=list)
    technical: list[RetrievedChunk] = Field(default_factory=list)
    history: list[RetrievedChunk] = Field(default_factory=list)
    focus_project: ProjectFacts | None = None
    candidate_name: str | None = None
    headline: str | None = None
    verified_skills: list[str] = Field(default_factory=list)
    job_title: str | None = None
    job_keywords: list[str] = Field(default_factory=list)
    retrieval_ms: float = 0.0
    agents_used: list[str] = Field(default_factory=list)
    # The candidate's full knowledge base: the grounding validator checks
    # candidate claims against all of it, not only the top-k shown to the model.
    evidence_pool: list[RetrievedChunk] = Field(default_factory=list, exclude=True)

    def all_chunks(self) -> list[RetrievedChunk]:
        return [*self.candidate, *self.job, *self.technical, *self.history]

    def candidate_evidence(self) -> list[RetrievedChunk]:
        """Evidence admissible for candidate-specific claims: the candidate's own
        knowledge base plus what the candidate has already said in this session."""
        seen: set[str] = set()
        out = []
        for c in [*self.candidate, *self.history, *self.evidence_pool]:
            if c.id not in seen:
                seen.add(c.id)
                out.append(c)
        return out


class Claim(BaseModel):
    text: str
    kind: Literal["candidate", "general"]
    supported: bool
    support: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    unsupported_terms: list[str] = Field(default_factory=list)
    verified_support: bool = False


class GroundingReport(BaseModel):
    method: str = "lexical-entity-v1"
    grounding_score: float | None = None  # None when there are no candidate-specific claims
    supported_claims: int = 0
    unsupported_claims: int = 0
    general_claims: int = 0
    risk: Literal["low", "medium", "high"] = "low"
    claims: list[Claim] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    rewritten: bool = False


class SourceRef(BaseModel):
    id: str
    label: str
    collection: str
    excerpt: str
    verified: bool
    meta: dict[str, Any] = Field(default_factory=dict)


class AnswerResult(BaseModel):
    text: str
    key_points: list[str] = Field(default_factory=list)
    star: dict[str, str] = Field(default_factory=dict)
    structure: str = "direct"
    sources: list[SourceRef] = Field(default_factory=list)
    grounding: GroundingReport = Field(default_factory=GroundingReport)
    word_count: int = 0
    speaking_seconds: float = 0.0
    speaking_range: tuple[int, int] = (0, 0)
    insufficient_context: bool = False
    variant: str = "default"
    length: str = "45s"
    model: str = ""
    job_relevance: float | None = None
    notes: list[str] = Field(default_factory=list)  # coaching notes, never spoken
    degraded: bool = False  # produced by the offline fallback because the LLM failed


class EvaluationScores(BaseModel):
    relevance: float
    correctness: float
    grounding: float
    completeness: float
    clarity: float
    conciseness: float
    naturalness: float
    job_alignment: float | None
    confidence: float
    hallucination_risk: float
    overall: float
    method: str = "heuristic-v1"
    notes: list[str] = Field(default_factory=list)


class LatencyBreakdown(BaseModel):
    stt_ms: float | None = None
    classification_ms: float = 0.0
    resolution_ms: float = 0.0
    retrieval_ms: float = 0.0
    first_token_ms: float | None = None
    generation_ms: float = 0.0
    grounding_ms: float = 0.0
    evaluation_ms: float = 0.0
    total_ms: float = 0.0
