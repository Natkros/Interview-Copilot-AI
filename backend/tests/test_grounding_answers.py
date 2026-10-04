"""Grounding validation, answer generation, adversarial (no-hallucination)
cases, LLM integration through a fake provider, degraded mode, scoring and
retrieval - including the Resume -> Qdrant -> Retrieval -> Answer integration."""

import asyncio

import pytest

from app.agents.classifier import classify
from app.agents.conversation import ConversationManager
from app.agents.evaluation import evaluate_answer
from app.agents.llm import LLMUnavailable, StreamResult, set_llm
from app.agents.prompts import META_DELIMITER
from app.agents.supervisor import InterviewPipeline, PipelineContext
from app.agents.validation import GroundingValidator
from app.database.models import Document, InterviewSession, User
from app.models.domain import ContextBundle, RetrievedChunk
from app.rag.indexer import reindex_profile, seed_technical_knowledge
from app.rag.retriever import HybridRetriever, RetrievalPlan
from app.rag.vectorstore import get_vector_store, reset_vector_store
from app.services.documents import extract_document
from app.services.profile import apply_parsed_resume, apply_profile_operation, get_or_create_profile
from app.services.resume_parser import parse_resume
from tests.conftest import SAMPLE_RESUME


def _chunk(cid, text, verified=True, coll="projects"):
    return RetrievedChunk(id=cid, collection=coll, source_type="project", text=text, verified=verified)


EVIDENCE = [
    _chunk("c1", "Project: Multimodal RAG Platform. Technologies used: Python, FastAPI, Qdrant, CLIP."),
    _chunk("c2", "Project Multimodal RAG Platform - What I built: Stored embeddings with metadata in Qdrant and "
                 "implemented hybrid retrieval combining vector search with BM25 keyword search."),
    _chunk("c3", "Project AI Bug Analyzer - Results: Trained a TF-IDF classifier on 12,000 labelled GitHub issues, "
                 "reaching 87% accuracy.", verified=False),
]


# ----------------------------------------------------------------------------- validator


def test_supported_claims_score_full():
    v = GroundingValidator(["Multimodal RAG Platform", "AI Bug Analyzer"])
    text, rep = v.validate("I stored embeddings with metadata in Qdrant. I built hybrid retrieval with BM25.",
                           ContextBundle(candidate=EVIDENCE))
    assert rep.grounding_score == 1.0 and rep.unsupported_claims == 0 and rep.risk == "low"
    assert not rep.rewritten


def test_invented_metric_is_removed():
    v = GroundingValidator(["Multimodal RAG Platform"])
    answer = ("I stored embeddings with metadata in Qdrant. "
              "It reduced query latency by 40% for 10,000 daily users.")
    text, rep = v.validate(answer, ContextBundle(candidate=EVIDENCE))
    assert rep.unsupported_claims == 1 and rep.rewritten
    assert "40%" not in text and "10,000" not in text
    bad = next(c for c in rep.claims if not c.supported)
    assert "40" in bad.unsupported_terms


def test_invented_technology_and_company_are_removed():
    v = GroundingValidator(["Multimodal RAG Platform"])
    answer = ("I stored embeddings with metadata in Qdrant. I deployed the Multimodal RAG Platform on Kubernetes. "
              "I built it during my internship at Google DeepMind.")
    text, rep = v.validate(answer, ContextBundle(candidate=EVIDENCE))
    assert rep.unsupported_claims == 2 and rep.risk == "high"
    assert "Kubernetes" not in text and "DeepMind" not in text


def test_unverified_evidence_gets_partial_credit():
    v = GroundingValidator(["AI Bug Analyzer"])
    _, rep = v.validate("I trained a TF-IDF classifier on 12,000 labelled GitHub issues, reaching 87% accuracy.",
                        ContextBundle(candidate=EVIDENCE))
    assert rep.supported_claims == 1 and rep.grounding_score == 0.5


def test_general_knowledge_is_not_counted_as_candidate_claim():
    v = GroundingValidator([])
    _, rep = v.validate("Qdrant uses HNSW indexing for approximate nearest-neighbour search. "
                        "I'd start by clarifying the requirements.", ContextBundle(candidate=EVIDENCE))
    assert rep.grounding_score is None and rep.general_claims == 2


def test_coaching_sentences_are_exempt():
    v = GroundingValidator([])
    _, rep = v.validate("Your resume doesn't list measured results. A safe way to answer is to describe what you checked.",
                        ContextBundle())
    assert rep.unsupported_claims == 0


def test_answer_without_any_support_becomes_insufficient():
    v = GroundingValidator(["Blockchain Voting System"])
    text, rep = v.validate("I built the Blockchain Voting System with Solidity. I led a team of 5 engineers.",
                           ContextBundle(candidate=EVIDENCE))
    assert text.startswith("Your resume doesn't provide enough information")
    assert rep.risk == "high"


# ----------------------------------------------------------------------------- pipeline helpers


@pytest.fixture
def world(db):
    u = User(email="w@example.com", password_hash="x")
    db.add(u)
    db.flush()
    doc = extract_document(SAMPLE_RESUME, "resume.txt")
    d = Document(user_id=u.id, kind="resume", filename="resume.txt", content_type="text/plain", sha256=doc.sha256,
                 text=doc.text)
    db.add(d)
    db.flush()
    prof = get_or_create_profile(db, u)
    apply_parsed_resume(db, prof, parse_resume(doc.text), d.id, "resume.txt")
    apply_profile_operation(db, prof, {"action": "confirm_all"})
    seed_technical_knowledge()
    reindex_profile(db, prof)
    s = InterviewSession(user_id=u.id, mode="live_coaching")
    db.add(s)
    db.flush()
    return {"db": db, "user": u, "profile": prof, "session": s}


def ask(world, questions, length="45s"):
    db, u, prof, s = world["db"], world["user"], world["profile"], world["session"]
    mgr = ConversationManager(db, s, prof)
    pipe = InterviewPipeline(PipelineContext(db=db, user=u, profile=prof, session=s, manager=mgr, length=length))
    results = []

    async def run():
        for q in questions:
            mgr.store_turn("interviewer", q)
            complete = None
            async for ev in pipe.run(q):
                if ev["event"] == "answer.complete":
                    complete = ev
            if complete:
                mgr.store_turn("candidate", complete["answer"]["text"], kind="suggestion")
                mgr.record_answer(complete["answer"]["text"])
            results.append(complete)

    asyncio.run(run())
    return results


# ----------------------------------------------------------------------------- adversarial


def test_adversarial_nonexistent_project(world):
    [r] = ask(world, ["Tell me about your blockchain voting project."])
    a = r["answer"]
    assert a["insufficient_context"] and a["text"].startswith("Your resume doesn't mention")
    assert "Multimodal RAG Platform" in a["text"]  # points to real projects instead


def test_adversarial_nonexistent_certification(world):
    [r] = ask(world, ["Tell me about your Google Cloud Professional Data Engineer certification."])
    a = r["answer"]
    assert a["insufficient_context"]
    assert "doesn't list" in a["text"] and "AWS Certified Cloud Practitioner" in a["text"]


def test_adversarial_nonexistent_metric(world):
    [_, r] = ask(world, ["Tell me about your RAG project.", "What accuracy did it achieve?"])
    a = r["answer"]
    assert a["insufficient_context"]
    assert not any(ch.isdigit() for ch in a["text"])


def test_adversarial_misleading_follow_up(world):
    [_, r] = ask(world, ["Tell me about your RAG project.", "Why did you choose Kubernetes for it?"])
    a = r["answer"]
    assert a["insufficient_context"]
    assert "doesn't mention using Kubernetes" in a["text"]
    assert a["grounding"]["unsupported_claims"] == 0


def test_adversarial_unknown_skill(world):
    [r] = ask(world, ["How have you used Kubernetes in your work?"])
    assert r["answer"]["insufficient_context"]
    assert "doesn't mention hands-on experience with Kubernetes" in r["answer"]["text"]


def test_real_metric_is_used_when_present(world):
    [r] = ask(world, ["What results did you get with the AI Bug Analyzer?"])
    assert "87% accuracy" in r["answer"]["text"]
    assert r["answer"]["grounding"]["grounding_score"] == 1.0


@pytest.mark.parametrize("length,lo,hi", [("20s", 15, 75), ("45s", 40, 140), ("90s", 60, 280)])
def test_answer_length_targets(world, length, lo, hi):
    [r] = ask(world, ["Tell me about yourself."], length=length)
    wc = r["answer"]["word_count"]
    assert lo <= wc <= hi, wc
    assert r["answer"]["speaking_seconds"] == pytest.approx(wc / 2.5, abs=0.1)


def test_behavioral_answer_has_star(world):
    [r] = ask(world, ["Tell me about a difficult problem you solved."])
    star = r["answer"]["star"]
    assert star["situation"] and star["task"] and star["action"] and star["result"]
    assert r["answer"]["grounding"]["unsupported_claims"] == 0


# ----------------------------------------------------------------------------- LLM provider path


class FakeLLM:
    name = "fake"
    model = "fake-model"

    def __init__(self, text: str, fail_after: int | None = None):
        self.text = text
        self.fail_after = fail_after
        self.prompts = []

    async def stream(self, system, user, max_tokens, result: StreamResult):
        self.prompts.append((system, user))
        pieces = [self.text[i:i + 7] for i in range(0, len(self.text), 7)]
        for i, p in enumerate(pieces):
            if self.fail_after is not None and i >= self.fail_after:
                raise LLMUnavailable("connection reset")
            result.text += p
            yield p
        result.model = self.model

    async def complete(self, system, user, max_tokens):
        r = StreamResult(text=self.text, model=self.model)
        return r


def test_llm_hallucination_is_stripped_before_display(world):
    fake = FakeLLM("I chose Qdrant as the vector database for my Multimodal RAG Platform. "
                   "I benchmarked it against Pinecone and it was 3x faster for our 50,000 users. "
                   "Qdrant supports payload filtering during vector search.\n"
                   f'{META_DELIMITER}\n{{"key_points": ["Qdrant", "filtering"], "insufficient_context": false}}')
    set_llm(fake)
    [_, r] = ask(world, ["Tell me about your RAG project.", "Why did you choose Qdrant?"])
    a = r["answer"]
    assert "Pinecone" not in a["text"] and "3x" not in a["text"]
    assert a["grounding"]["removed_claims"] and a["grounding"]["rewritten"]
    assert a["key_points"] == ["Qdrant", "filtering"]
    assert META_DELIMITER not in a["text"]
    # the prompt carries the grounding rules, the focus project and the unknown fields
    system, user = fake.prompts[-1]
    assert "Never invent" in system
    assert "FOCUS PROJECT" in user and "UNKNOWN FOR THIS PROJECT: challenges" in user
    assert "Why did you choose Qdrant for the Multimodal RAG Platform project?" in user


def test_llm_outage_falls_back_to_profile_answer(world):
    set_llm(FakeLLM("My Multimodal RAG Platform is great and " * 5, fail_after=2))
    events = []

    async def run():
        db, u, prof, s = world["db"], world["user"], world["profile"], world["session"]
        mgr = ConversationManager(db, s, prof)
        pipe = InterviewPipeline(PipelineContext(db=db, user=u, profile=prof, session=s, manager=mgr))
        mgr.store_turn("interviewer", "Tell me about your RAG project.")
        async for ev in pipe.run("Tell me about your RAG project."):
            events.append(ev)

    asyncio.run(run())
    kinds = [e["event"] for e in events]
    assert "answer.reset" in kinds
    complete = events[-1]
    assert complete["answer"]["degraded"] is True
    assert complete["answer"]["model"] == "offline-extractive-v1"
    assert complete["answer"]["text"].startswith("My Multimodal RAG Platform")


# ----------------------------------------------------------------------------- evaluator


def test_evaluator_rewards_grounded_natural_answers():
    cls = classify("Tell me about your RAG project.")
    _, good_rep = GroundingValidator(["Multimodal RAG Platform"]).validate(
        "I stored embeddings with metadata in Qdrant and built hybrid retrieval with BM25.",
        ContextBundle(candidate=EVIDENCE))
    good = evaluate_answer("Tell me about your RAG project.",
                           "My Multimodal RAG Platform answers questions over PDFs. I stored embeddings with metadata in "
                           "Qdrant and built hybrid retrieval with BM25, so it's accurate on exact terms too.",
                           cls, good_rep, length="20s")
    _, bad_rep = GroundingValidator(["Multimodal RAG Platform"]).validate(
        "I deployed it on Kubernetes for 1,000,000 users.", ContextBundle(candidate=EVIDENCE), rewrite=False)
    bad = evaluate_answer("Tell me about your RAG project.",
                          "The utilization of Kubernetes was strategically leveraged. Furthermore, it is robust and scalable. "
                          "I deployed it on Kubernetes for 1,000,000 users.", cls, bad_rep, length="20s")
    assert good.overall > bad.overall + 0.2
    assert bad.hallucination_risk > 0.5 and bad.naturalness < good.naturalness
    for k in ("relevance", "correctness", "grounding", "completeness", "clarity", "conciseness", "naturalness",
              "confidence", "hallucination_risk", "overall"):
        assert 0.0 <= getattr(good, k) <= 1.0


def test_evaluator_job_alignment_and_hedging():
    cls = classify("What is RAG?")
    s = evaluate_answer("What is RAG?", "Maybe it's, I think, probably retrieval with Python and Qdrant? Not sure.",
                        cls, None, job_keywords=["Python", "Qdrant", "AWS"])
    assert s.job_alignment is not None and s.job_alignment > 0.5
    assert s.confidence < 0.6


# ----------------------------------------------------------------------------- retrieval


def test_integration_resume_to_qdrant_to_retrieval(world):
    db, u = world["db"], world["user"]
    store = get_vector_store()
    assert store.count("projects", u.id) > 0

    r = HybridRetriever(db, u.id)
    rag = next(p for p in world["profile"].projects if p.name == "Multimodal RAG Platform")
    from app.models.domain import FocusState

    focus = FocusState(project_id=rag.id, project_name=rag.name)
    bundle = asyncio.run(r.retrieve("Which vector database stores the embeddings?", RetrievalPlan(technical=True),
                                    focus=focus, aspect="technology"))
    top = bundle.candidate[0]
    assert top.meta.get("project_id") == rag.id
    assert any("Qdrant" in c.text for c in bundle.candidate[:3])
    assert bundle.technical and bundle.retrieval_ms > 0


def test_tenant_isolation(world, db):
    other = User(email="other@example.com", password_hash="x")
    db.add(other)
    db.flush()
    r = HybridRetriever(db, other.id)
    bundle = asyncio.run(r.retrieve("Multimodal RAG Platform Qdrant", RetrievalPlan(history=True, job=True),
                                    session_id=world["session"].id, job_id="nope"))
    assert bundle.candidate == [] and bundle.history == [] and bundle.job == []
    store = get_vector_store()
    from app.rag.embeddings import get_embedder

    hits = store.search("projects", get_embedder().embed_one("Multimodal RAG Platform"), other.id)
    assert hits == []
    with pytest.raises(ValueError):
        store.search("projects", get_embedder().embed_one("x"), "")


def test_keyword_fallback_when_qdrant_unavailable(world, monkeypatch):
    import app.rag.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "get_vector_store", lambda: None)
    r = HybridRetriever(world["db"], world["user"].id)
    bundle = asyncio.run(r.retrieve("What did you build with Flask?", RetrievalPlan()))
    assert any("Flask" in c.text for c in bundle.candidate)
    reset_vector_store(None)
