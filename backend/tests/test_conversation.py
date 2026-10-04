"""Question classification, follow-up detection, reference resolution and
turn detection."""

import pytest

from app.agents.classifier import ClassifierContext, classify
from app.agents.conversation import ProfileIndex, ReferenceResolver, detect_aspect
from app.models.domain import FocusState
from app.speech.turn_detector import DetectorConfig, SttState, TurnDetector, clean_utterance

INDEX = ProfileIndex(
    projects=[{"id": "p1", "name": "Multimodal RAG Platform", "technologies": ["Python", "FastAPI", "Qdrant", "CLIP", "RAG"]},
              {"id": "p2", "name": "AI Bug Analyzer", "technologies": ["Python", "Flask", "scikit-learn", "AWS"]}],
    experiences=[{"id": "e1", "name": "Machine Learning Intern at DataNest Analytics", "org": "DataNest Analytics",
                  "title": "Machine Learning Intern", "technologies": ["XGBoost"], "kind": "internship"}],
)
NAMES = [p["name"] for p in INDEX.projects]


@pytest.mark.parametrize("text,expected", [
    ("Hello, nice to meet you.", "GREETING"),
    ("Tell me about yourself.", "INTRODUCTION"),
    ("Tell me about a time you disagreed with a teammate.", "BEHAVIORAL"),
    ("Why should we hire you?", "HR"),
    ("What is your biggest weakness?", "HR"),
    ("What would you do if production went down during a release?", "SITUATIONAL"),
    ("Tell me about your RAG project.", "PROJECT"),
    ("What did you do at DataNest?", "RESUME"),
    ("Tell me about your AWS certification.", "RESUME"),
    ("Write a function to reverse a linked list.", "CODING"),
    ("What is the time complexity of binary search?", "DSA"),
    ("Design a URL shortener that handles millions of users.", "SYSTEM_DESIGN"),
    ("What is overfitting?", "MACHINE_LEARNING"),
    ("How does backpropagation work in a neural network?", "DEEP_LEARNING"),
    ("How do you reduce hallucinations in LLMs?", "GENERATIVE_AI"),
    ("Explain how HNSW indexing works in vector databases.", "RAG"),
    ("What is database normalization?", "DATABASE"),
    ("What is the difference between EC2 and Lambda?", "CLOUD"),
    ("What does a Dockerfile do?", "DEVOPS"),
    ("How do you prevent SQL injection?", "SECURITY"),
    ("What is the difference between a process and a thread?", "TECHNICAL"),
    ("Could you clarify what you mean?", "CLARIFICATION"),
    ("Great, that makes sense.", "FEEDBACK"),
    ("Let's move on to system design.", "STATEMENT"),
])
def test_classification(text, expected):
    c = classify(text, ClassifierContext(project_names=NAMES, experience_orgs=["DataNest Analytics"]))
    assert c.type.value == expected, (text, c)


def test_classification_contract_fields():
    focus = FocusState(project_id="p1", project_name="Multimodal RAG Platform", topic="RAG",
                       project_technologies=INDEX.projects[0]["technologies"])
    c = classify("Why did you choose Qdrant?", ClassifierContext(focus=focus, project_names=NAMES, has_history=True))
    assert c.model_dump(include={"type", "topic", "difficulty", "requires_resume_context",
                                 "requires_technical_context", "requires_previous_turn_context"}) == {
        "type": "FOLLOW_UP", "topic": "RAG", "difficulty": "medium", "requires_resume_context": True,
        "requires_technical_context": True, "requires_previous_turn_context": True}


def test_difficulty():
    assert classify("What is a list in Python?").difficulty == "easy"
    assert classify("Design a distributed rate limiter at scale.").difficulty == "hard"


def _run(questions):
    r = ReferenceResolver(INDEX)
    focus, hist, out = FocusState(), False, []
    for q in questions:
        c = classify(q, ClassifierContext(focus=focus, project_names=NAMES, experience_orgs=["DataNest Analytics"],
                                          has_history=hist))
        res = r.resolve(q, c, focus)
        focus, hist = res.focus, True
        out.append((c, res))
    return out


def test_follow_up_resolution_chain():
    out = _run(["Tell me about your RAG project.", "Why did you choose Qdrant?", "What was the biggest challenge?",
                "How would you improve it?", "What was your role?"])
    for c, res in out[1:]:
        assert c.type.value == "FOLLOW_UP"
        assert res.focus.project_name == "Multimodal RAG Platform"
        assert "Multimodal RAG Platform" in res.resolved
    assert out[1][1].aspect == "why_choice" and out[2][1].aspect == "challenge"
    assert out[3][1].aspect == "improvement" and out[4][1].aspect == "role"


def test_pronoun_resolves_to_previous_technology():
    out = _run(["Tell me about your RAG project.", "You used Qdrant there, right?", "Why did you choose that?"])
    assert out[2][1].resolved.startswith("Why did you choose Qdrant")
    assert "that -> Qdrant" in out[2][1].references


def test_topic_switch_clears_focus():
    out = _run(["Tell me about your RAG project.", "What is the difference between TCP and UDP?",
                "What was the biggest challenge?"])
    assert out[1][1].focus.project_id is None
    assert out[2][0].type.value != "FOLLOW_UP" or out[2][1].focus.project_id is None


def test_switch_between_projects_by_partial_name():
    out = _run(["Tell me about your RAG project.", "How did you deploy the bug analyzer?", "Why did you choose Flask?"])
    assert out[1][1].focus.project_name == "AI Bug Analyzer"
    assert out[2][1].focus.project_name == "AI Bug Analyzer"


def test_unknown_project_does_not_inherit_focus():
    out = _run(["Tell me about your RAG project.", "Tell me about your blockchain voting project."])
    assert out[1][1].focus.project_id is None


def test_aspects():
    assert detect_aspect("What was the hardest part?") == "challenge"
    assert detect_aspect("How did you test it?") == "testing"
    assert detect_aspect("What results did you get?") == "result"


# ----------------------------------------------------------------------------- turn detection


def test_turn_waits_for_endpoint_silence():
    d = TurnDetector(DetectorConfig(endpoint_ms=1000, question_endpoint_ms=600))
    d.start()
    d.on_vad(True, 0.0)
    d.on_partial("Can you explain", 0.2)
    d.on_final("Can you explain how your", 0.5)
    d.on_vad(False, 0.6)
    assert d.tick(1.0) is None  # short pause mid-question: keep listening
    d.on_vad(True, 1.1)
    d.on_final("retrieval pipeline works?", 1.6)
    d.on_vad(False, 1.7)
    assert d.tick(2.0) is None
    assert d.tick(2.4) == "Can you explain how your retrieval pipeline works?"
    assert d.state == SttState.QUESTION_READY


def test_statement_needs_full_endpoint():
    d = TurnDetector(DetectorConfig(endpoint_ms=1000, question_endpoint_ms=500))
    d.start()
    d.on_final("Walk me through your internship", 0.0)
    assert d.tick(0.7) is None
    assert d.tick(1.1) == "Walk me through your internship"


def test_paused_detector_ignores_audio():
    d = TurnDetector()
    d.on_final("hello there friend", 0.0)
    assert d.tick(5.0) is None and d.state == SttState.IDLE


@pytest.mark.parametrize("raw,clean", [
    ("Can you explain your—actually, let's talk about your internship.", "Let's talk about your internship."),
    ("Why did you pick Flask - sorry, why did you pick Flask over Django?", "Why did you pick Flask over Django?"),
    ("How did you actually measure the accuracy of the model?", "How did you actually measure the accuracy of the model?"),
    ("Did you have to wait a long time for the model to train?", "Did you have to wait a long time for the model to train?"),
    ("Um, tell me about, uh, your project.", "tell me about, your project."),
])
def test_interruption_and_disfluency_cleanup(raw, clean):
    assert clean_utterance(raw) == clean


def test_recording_is_opt_in_and_deletable(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.speech.recording import (
        RecordingWriter,
        delete_recording,
        recording_allowed,
        recording_path,
    )

    s = get_settings()
    monkeypatch.setattr(s, "recordings_dir", str(tmp_path))
    monkeypatch.setattr(s, "store_raw_audio", False)
    assert not recording_allowed({"store_recordings": True})  # server disallows
    monkeypatch.setattr(s, "store_raw_audio", True)
    assert not recording_allowed({})  # user hasn't opted in
    assert recording_allowed({"store_recordings": True})
    w = RecordingWriter("abc123")
    w.write(b"\x00\x01" * 1600)
    wav = w.finalize()
    assert wav and wav.read_bytes()[:4] == b"RIFF" and recording_path("abc123") == wav
    assert delete_recording("abc123") and recording_path("abc123") is None
