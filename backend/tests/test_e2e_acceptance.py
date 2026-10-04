"""Spec section 66/68: the full end-to-end flow and the exact acceptance scenario,
driven through the HTTP API and the live WebSocket (STT segments -> turn
detection -> classification -> retrieval -> grounded answer -> UI events)."""

import json
import time

from tests.conftest import confirm_all, register, upload_resume

JD = """Machine Learning Engineer - Acme AI
Responsibilities
- Build retrieval-augmented generation (RAG) services in Python
- Deploy ML models on AWS
Requirements
- Python, Flask or FastAPI
- Experience with vector databases such as Qdrant
- Machine Learning fundamentals
Nice to have
- Docker, Kubernetes
"""


def _collect(ws, until: str, timeout: float = 20.0) -> list[dict]:
    events = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        ev = json.loads(ws.receive_text())
        events.append(ev)
        if ev.get("event") == until:
            return events
    raise AssertionError(f"timed out waiting for {until}; got {[e.get('event') for e in events]}")


def _open(client, session_id: str, last_seq: int = 0):
    ticket = client.post(f"/interviews/{session_id}/ticket").json()["ticket"]
    ws = client.websocket_connect(f"/interviews/{session_id}/live?ticket={ticket}")
    return ws


def _speak(ws, text: str):
    """Simulate streaming STT: partials, then the final segment."""
    words = text.split()
    for i in range(2, len(words), 3):
        ws.send_text(json.dumps({"type": "transcript.partial", "text": " ".join(words[:i])}))
    ws.send_text(json.dumps({"type": "transcript.segment", "text": text, "stt_ms": 180}))


def _ask(ws, text: str) -> tuple[dict, dict, list[dict]]:
    _speak(ws, text)
    events = _collect(ws, "answer.complete")
    final = next(e for e in events if e["event"] == "transcript.final")
    complete = events[-1]
    return final, complete, events


def test_definition_of_done_and_acceptance_scenario(client):
    # register -> upload resume -> parsed -> candidate verifies -> embeddings/Qdrant
    register(client)
    up = upload_resume(client)
    assert up["knowledge_base"]["chunks"] > 10 and up["knowledge_base"]["vector_index"] is True
    names = {p["name"] for p in up["profile"]["projects"]}
    assert {"Multimodal RAG Platform", "AI Bug Analyzer"} <= names
    skills = {s["name"] for s in up["profile"]["skills"]}
    assert {"Python", "Flask", "Machine Learning", "AWS", "Qdrant"} <= skills
    assert all(not s["verified"] for s in up["profile"]["skills"])  # nothing trusted until confirmed
    prof = confirm_all(client)["profile"]
    assert prof["verification"]["ratio"] == 1.0

    # job description
    job = client.post("/jobs", data={"text": JD}).json()
    assert job["match"]["score"] > 0.5
    assert "Qdrant" in job["parsed"]["required_skills"]

    # start interview
    r = client.post("/interviews", json={"mode": "live_coaching", "job_id": job["id"], "consent_acknowledged": True})
    assert r.status_code == 201
    sid = r.json()["id"]

    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello", "last_seq": 0}))
        snap = json.loads(ws.receive_text())
        assert snap["event"] == "session.snapshot" and snap["turns"] == []

        # 1. "Tell me about your RAG project."
        final, complete, events = _ask(ws, "Tell me about your RAG project.")
        assert final["turn"]["role"] == "interviewer"
        assert final["turn"]["text"] == "Tell me about your RAG project."
        assert any(e["event"] == "transcript.partial" for e in events)
        classified = next(e for e in events if e["event"] == "question.classified")
        assert classified["classification"]["type"] == "PROJECT"
        assert classified["focus"]["project"] == "Multimodal RAG Platform"
        assert any(e["event"] == "answer.delta" for e in events)  # streamed
        a1 = complete["answer"]
        assert a1["text"].startswith("My Multimodal RAG Platform")
        assert complete["turn"]["role"] == "candidate"
        assert a1["grounding"]["unsupported_claims"] == 0 and a1["grounding"]["grounding_score"] == 1.0
        assert {"projects"} & {s["collection"] for s in a1["sources"]}

        # 2. "Why did you choose Qdrant?" -> resolved against the RAG project
        final, complete, events = _ask(ws, "Why did you choose Qdrant?")
        classified = next(e for e in events if e["event"] == "question.classified")
        assert classified["classification"]["type"] == "FOLLOW_UP"
        assert "Multimodal RAG Platform" in classified["resolved"]
        a2 = complete["answer"]
        assert a2["text"].startswith("I chose Qdrant")
        assert "Multimodal RAG Platform" in a2["text"]
        assert a2["grounding"]["unsupported_claims"] == 0
        # general technical reasoning is labelled, and the candidate is told the reason isn't recorded
        assert any("doesn't record why" in n for n in a2["notes"])

        # 3. "What was the biggest challenge?" -> RAG project has no recorded challenge
        final, complete, events = _ask(ws, "What was the biggest challenge?")
        classified = next(e for e in events if e["event"] == "question.classified")
        assert classified["classification"]["type"] == "FOLLOW_UP"
        assert "Multimodal RAG Platform" in classified["resolved"]
        a3 = complete["answer"]
        assert a3["text"].startswith("Your project information doesn't specify a particular challenge.")
        assert "A safe way to answer this would be to explain a challenge you personally encountered" in a3["text"]
        assert a3["insufficient_context"] is True
        assert "deduplication" not in a3["text"]  # must not borrow another project's challenge

        # conversation continues; every turn is persisted in order
        ws.send_text(json.dumps({"type": "control", "action": "end"}))
        ended = _collect(ws, "session.ended")[-1]
        assert ended["report_id"]

    detail = client.get(f"/interviews/{sid}").json()
    roles = [t["role"] for t in detail["turns"]]
    assert roles == ["interviewer", "candidate"] * 3
    assert detail["status"] == "ended"

    report = client.get(f"/interviews/{sid}/report").json()
    assert report["summary"]["questions"] == 3
    assert report["scores"]["overall"] is not None
    assert any("challenge" in m["question"].lower() for m in report["questions_missed"])


def test_challenge_is_used_when_verified(client):
    """Same scenario, but the candidate adds a verified challenge: the answer must use it."""
    register(client)
    profile = upload_resume(client)["profile"]
    rag = next(p for p in profile["projects"] if p["name"] == "Multimodal RAG Platform")
    client.patch("/resume", json={"operations": [
        {"action": "confirm_all"},
        {"action": "edit", "kind": "project", "id": rag["id"],
         "data": {"challenges": ["retrieving the right table rows when questions mixed text and table data"],
                  "solutions": ["indexing each table row as its own chunk with the table caption as metadata"]}},
    ]})
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "utterance.text", "text": "Tell me about your RAG project.", "role": "interviewer"}))
        _collect(ws, "answer.complete")
        ws.send_text(json.dumps({"type": "utterance.text", "text": "What was the biggest challenge?", "role": "interviewer"}))
        complete = _collect(ws, "answer.complete")[-1]
    text = complete["answer"]["text"]
    assert "retrieving the right table rows" in text
    assert "indexing each table row" in text
    assert complete["answer"]["insufficient_context"] is False
    assert complete["answer"]["grounding"]["grounding_score"] == 1.0


def test_reconnect_replays_missed_events(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "utterance.text", "text": "Tell me about your RAG project.", "role": "interviewer"}))
        events = _collect(ws, "answer.complete")
        checkpoint = events[1]["seq"]  # pretend the client only saw the first couple of events
    with _open(client, sid) as ws2:
        ws2.send_text(json.dumps({"type": "hello", "last_seq": checkpoint}))
        replayed = []
        while True:
            ev = json.loads(ws2.receive_text())
            if ev["event"] == "session.snapshot":
                snap = ev
                break
            replayed.append(ev)
    assert any(e["event"] == "answer.complete" for e in replayed)
    assert all(e.get("replayed") for e in replayed)
    assert [t["role"] for t in snap["turns"]] == ["interviewer", "candidate"]


def test_interruption_uses_completed_utterance(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "transcript.segment", "text": "Can you explain your—"}))
        ws.send_text(json.dumps({"type": "transcript.segment", "text": "actually, let's talk about your internship at DataNest."}))
        events = _collect(ws, "answer.complete")
    finals = [e for e in events if e["event"] == "transcript.final"]
    assert len(finals) == 1
    assert finals[0]["turn"]["text"] == "Let's talk about your internship at DataNest."
    assert "DataNest Analytics" in events[-1]["answer"]["text"]


def test_live_coaching_requires_consent(client):
    register(client)
    r = client.post("/interviews", json={"mode": "live_coaching"})
    assert r.status_code == 422
