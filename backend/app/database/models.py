"""ORM schema (PostgreSQL / SQLite).

Ownership: every row is reachable from `users.id`, and every query in the
service layer filters by the authenticated user's id (tenant isolation).
Deletes cascade at the database level (`ondelete="CASCADE"`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JsonType, list[Any]: JsonType}


def _fk(target: str, nullable: bool = False, ondelete: str = "CASCADE") -> Mapped[Any]:
    return mapped_column(String(32), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --------------------------------------------------------------------------- users


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferences: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)

    profile: Mapped[CandidateProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


# --------------------------------------------------------------------------- candidate


class CandidateProfile(TimestampMixin, Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(200))
    links: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    summary: Mapped[str | None] = mapped_column(Text)
    personal_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    kb_version: Mapped[int] = mapped_column(Integer, default=0)
    kb_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")
    skills: Mapped[list[Skill]] = relationship(cascade="all, delete-orphan", passive_deletes=True, order_by="Skill.position")
    projects: Mapped[list[Project]] = relationship(cascade="all, delete-orphan", passive_deletes=True, order_by="Project.position")
    experiences: Mapped[list[Experience]] = relationship(cascade="all, delete-orphan", passive_deletes=True, order_by="Experience.position")
    certifications: Mapped[list[Certification]] = relationship(cascade="all, delete-orphan", passive_deletes=True, order_by="Certification.position")
    items: Mapped[list[ProfileItem]] = relationship(cascade="all, delete-orphan", passive_deletes=True, order_by="ProfileItem.position")


class _ProfileChild(TimestampMixin):
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="resume")  # resume | user
    source: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)  # {document, section, page}
    position: Mapped[int] = mapped_column(Integer, default=0)


class Skill(_ProfileChild, Base):
    __tablename__ = "skills"
    profile_id: Mapped[str] = _fk("candidate_profiles.id")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="other")
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_skill_profile_name"),)


class Project(_ProfileChild, Base):
    __tablename__ = "projects"
    profile_id: Mapped[str] = _fk("candidate_profiles.id")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    problem: Mapped[str | None] = mapped_column(Text)
    solution: Mapped[str | None] = mapped_column(Text)
    candidate_role: Mapped[str | None] = mapped_column(Text)
    architecture: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    technologies: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    responsibilities: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    challenges: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    solutions: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    results: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    metrics: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    limitations: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    future_work: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    testing: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    url: Mapped[str | None] = mapped_column(String(500))


class Experience(_ProfileChild, Base):
    __tablename__ = "experiences"
    profile_id: Mapped[str] = _fk("candidate_profiles.id")
    kind: Mapped[str] = mapped_column(String(16), default="job")  # job | internship
    title: Mapped[str | None] = mapped_column(String(200))
    organization: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[str | None] = mapped_column(String(40))
    end_date: Mapped[str | None] = mapped_column(String(40))
    highlights: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    technologies: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class Certification(_ProfileChild, Base):
    __tablename__ = "certifications"
    profile_id: Mapped[str] = _fk("candidate_profiles.id")
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(200))
    date: Mapped[str | None] = mapped_column(String(40))


class ProfileItem(_ProfileChild, Base):
    """Education, achievements, research, publications, leadership, activities."""

    __tablename__ = "profile_items"
    profile_id: Mapped[str] = _fk("candidate_profiles.id")
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300))
    date: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[list[Any]] = mapped_column(JsonType, default=list)


# --------------------------------------------------------------------------- documents


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # resume | job_description | project_doc | other
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text, default="")
    pages: Mapped[list[Any]] = mapped_column(JsonType, default=list)  # per-page text
    status: Mapped[str] = mapped_column(String(24), default="processed")
    warnings: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class Resume(TimestampMixin, Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    document_id: Mapped[str] = _fk("documents.id")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    parse_status: Mapped[str] = mapped_column(String(24), default="parsed")
    parse_report: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    sections: Mapped[list[ResumeSection]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="ResumeSection.position"
    )


class ResumeSection(Base):
    __tablename__ = "resume_sections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    resume_id: Mapped[str] = _fk("resumes.id")
    name: Mapped[str] = mapped_column(String(40), nullable=False)  # canonical
    heading: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)


class DocumentChunk(TimestampMixin, Base):
    """Every unit indexed into Qdrant, mirrored here for keyword search,
    source display and re-indexing."""

    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # = qdrant point id (uuid)
    user_id: Mapped[str] = _fk("users.id")
    document_id: Mapped[str | None] = _fk("documents.id", nullable=True)
    collection: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_chunks_user_collection", "user_id", "collection"),)


# --------------------------------------------------------------------------- jobs


class JobDescription(TimestampMixin, Base):
    __tablename__ = "job_descriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    document_id: Mapped[str | None] = _fk("documents.id", nullable=True, ondelete="SET NULL")
    title: Mapped[str] = mapped_column(String(200), default="Untitled role")
    company: Mapped[str | None] = mapped_column(String(200))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    match: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


# --------------------------------------------------------------------------- interviews


class InterviewSession(TimestampMixin, Base):
    __tablename__ = "interview_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    job_id: Mapped[str | None] = _fk("job_descriptions.id", nullable=True, ondelete="SET NULL")
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="Interview session")
    status: Mapped[str] = mapped_column(String(16), default="created")  # created | live | paused | ended
    answer_length: Mapped[str] = mapped_column(String(12), default="45s")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Conversation-manager state (focus entity, topic stack, rolling summary),
    # persisted so a reconnect / restart resumes with full context.
    state: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    turns: Mapped[list[ConversationTurn]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="ConversationTurn.seq"
    )


class ConversationTurn(TimestampMixin, Base):
    __tablename__ = "conversation_turns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = _fk("interview_sessions.id")
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # interviewer | candidate | system
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="utterance")  # utterance | suggestion | spoken
    question_id: Mapped[str | None] = _fk("questions.id", nullable=True, ondelete="SET NULL")
    answer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_turn_session_seq"),)


class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = _fk("interview_sessions.id", nullable=True)
    user_id: Mapped[str] = _fk("users.id")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_text: Mapped[str] = mapped_column(Text, nullable=False)
    qtype: Mapped[str] = mapped_column(String(24), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(40))
    difficulty: Mapped[str] = mapped_column(String(12), default="medium")
    classification: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    focus: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class Answer(TimestampMixin, Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    question_id: Mapped[str] = _fk("questions.id")
    user_id: Mapped[str] = _fk("users.id")
    variant: Mapped[str] = mapped_column(String(20), default="default")
    length_target: Mapped[str] = mapped_column(String(12), default="45s")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    speaking_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    key_points: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    star: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    sources: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    grounding: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    latency: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    insufficient_context: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(80), default="")


class AnswerEvaluation(TimestampMixin, Base):
    __tablename__ = "answer_evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    answer_id: Mapped[str] = _fk("answers.id")
    scores: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    overall: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(40), default="heuristic-v1")
    notes: Mapped[list[Any]] = mapped_column(JsonType, default=list)


class SessionSummary(TimestampMixin, Base):
    __tablename__ = "session_summaries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = _fk("interview_sessions.id")
    upto_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class InterviewReport(TimestampMixin, Base):
    __tablename__ = "interview_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_id: Mapped[str] = _fk("users.id")
    data: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


# --------------------------------------------------------------------------- practice


class QuestionBankItem(TimestampMixin, Base):
    __tablename__ = "question_bank"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    job_id: Mapped[str | None] = _fk("job_descriptions.id", nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(12), default="medium")
    expected_concepts: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    resume_relevance: Mapped[float] = mapped_column(Float, default=0.0)
    job_relevance: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="seed")  # resume | job | seed | session
    source_ref: Mapped[str | None] = mapped_column(String(64))
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("user_id", "question", name="uq_bank_user_question"),)


class PracticeAttempt(TimestampMixin, Base):
    __tablename__ = "practice_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = _fk("users.id")
    question_bank_id: Mapped[str] = _fk("question_bank.id")
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    overall: Mapped[float] = mapped_column(Float, default=0.0)
    feedback: Mapped[list[Any]] = mapped_column(JsonType, default=list)
