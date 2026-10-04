"""HTTP API: auth, authorization/tenant isolation, CSRF, rate limiting, uploads,
profile verification, jobs, documents, mock interviews, practice, analytics,
search and privacy."""

import json

from app.rag.vectorstore import get_vector_store
from tests.conftest import CSRF, SAMPLE_RESUME, confirm_all, register, upload_resume
from tests.test_e2e_acceptance import _collect, _open

# ----------------------------------------------------------------------------- auth


def test_register_login_logout_me(client):
    register(client, "a@example.com")
    assert client.get("/auth/me").json()["email"] == "a@example.com"
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/login", json={"email": "a@example.com", "password": "wrong-pass1"}).status_code == 401
    r = client.post("/auth/login", json={"email": "A@example.com", "password": "Passw0rd!x"})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()


def test_register_validation(client):
    assert client.post("/auth/register", json={"email": "bad", "password": "Passw0rd!x"}).status_code == 422
    assert client.post("/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"email": "x@example.com", "password": "lettersonly"}).status_code == 422
    register(client, "dup@example.com")
    assert client.post("/auth/register", json={"email": "dup@example.com", "password": "Passw0rd!x"}).status_code == 409


def test_protected_routes_require_auth(client):
    for method, path in [("get", "/resume"), ("get", "/jobs"), ("get", "/interviews"), ("get", "/analytics"),
                         ("get", "/question-bank"), ("get", "/privacy/export")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_csrf_header_required_for_cookie_mutations(client):
    register(client)
    client.headers.pop("x-interviewos-csrf")
    r = client.post("/jobs", data={"text": "x" * 60})
    assert r.status_code == 403
    client.headers.update(CSRF)
    assert client.post("/jobs", data={"text": "Backend engineer. Requirements: Python, PostgreSQL, Docker experience."}).status_code == 201


def test_cross_tenant_access_is_404(client):
    register(client, "alice@example.com")
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "mock"}).json()["id"]
    job = client.post("/jobs", data={"text": "Data engineer role. Requirements: Python, SQL, Airflow, AWS."}).json()
    client.post("/auth/logout")
    register(client, "mallory@example.com")
    assert client.get(f"/interviews/{sid}").status_code == 404
    assert client.post(f"/interviews/{sid}/ticket").status_code == 404
    assert client.delete(f"/interviews/{sid}").status_code == 404
    assert client.get(f"/jobs/{job['id']}").status_code == 404
    # Mallory's profile is empty: none of Alice's knowledge leaks into her answers
    r = client.post("/answers/generate", json={"question": "Tell me about your Multimodal RAG Platform."}).json()
    assert r["answer"]["insufficient_context"] is True
    assert "hybrid retrieval" not in r["answer"]["text"]


def test_ws_rejects_bad_ticket(client):
    register(client)
    sid = client.post("/interviews", json={"mode": "mock"}).json()["id"]
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/interviews/{sid}/live?ticket=forged") as ws:
            ws.receive_text()


def test_rate_limit(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", 3)
    codes = [client.post("/auth/login", json={"email": "z@example.com", "password": "Passw0rd!x"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


# ----------------------------------------------------------------------------- resume & profile


def test_upload_rejects_large_and_bad_files(client, monkeypatch):
    register(client)
    r = client.post("/resume/upload", files={"file": ("r.exe", b"MZ\x00\x00binary", "application/octet-stream")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "unsupported_type"
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = client.post("/resume/upload", files={"file": ("r.txt", SAMPLE_RESUME, "text/plain")})
    assert r.status_code == 413


def test_profile_verification_operations(client):
    register(client)
    prof = upload_resume(client)["profile"]
    python = next(s for s in prof["skills"] if s["name"] == "Python")
    git = next(s for s in prof["skills"] if s["name"] == "Git")
    proj = prof["projects"][0]
    r = client.patch("/resume", json={"operations": [
        {"action": "confirm", "kind": "skill", "id": python["id"]},
        {"action": "remove", "kind": "skill", "id": git["id"]},
        {"action": "edit", "kind": "project", "id": proj["id"], "data": {"candidate_role": "Sole developer"}},
        {"action": "add", "kind": "skill", "data": {"name": "golang"}},
        {"action": "add", "kind": "item", "data": {"kind": "leadership", "title": "Lead, AI Club"}},
    ]})
    assert r.status_code == 200, r.text
    p = r.json()["profile"]
    names = {s["name"]: s for s in p["skills"]}
    assert names["Python"]["verified"] and "Git" not in names
    assert names["Go"]["origin"] == "user" and names["Go"]["verified"]  # alias canonicalised
    dup = client.patch("/resume", json={"operations": [{"action": "add", "kind": "skill", "data": {"name": "postgres"}}]})
    assert dup.status_code == 422  # alias of an existing skill
    edited = next(x for x in p["projects"] if x["id"] == proj["id"])
    assert edited["candidate_role"] == "Sole developer" and edited["verified"]
    assert any(i["kind"] == "leadership" and i["origin"] == "user" for i in p["items"])
    canon = client.get("/resume").json()["canonical"]
    assert "Skill: Python" in canon["verified_facts"] and "Skill: Flask" in canon["unverified_facts"]


def test_profile_edit_validation(client):
    register(client)
    prof = upload_resume(client)["profile"]
    r = client.patch("/resume", json={"operations": [
        {"action": "edit", "kind": "project", "id": prof["projects"][0]["id"], "data": {"user_id": "hijack"}}]})
    assert r.status_code == 422
    r = client.patch("/resume", json={"operations": [{"action": "confirm", "kind": "skill", "id": "nope"}]})
    assert r.status_code == 422


def test_reupload_keeps_verified_and_manual_items(client):
    register(client)
    prof = upload_resume(client)["profile"]
    client.patch("/resume", json={"operations": [
        {"action": "confirm", "kind": "project", "id": prof["projects"][0]["id"]},
        {"action": "add", "kind": "skill", "data": {"name": "Rust"}}]})
    smaller = SAMPLE_RESUME.decode().split("AI Bug Analyzer")[0].encode()
    prof2 = upload_resume(client, smaller)["profile"]
    names = {p["name"] for p in prof2["projects"]}
    assert "Multimodal RAG Platform" in names
    assert any(s["name"] == "Rust" for s in prof2["skills"])


def test_project_document_upload_and_delete(client):
    register(client)
    prof = upload_resume(client)["profile"]
    confirm_all(client)
    rag = next(p for p in prof["projects"] if p["name"] == "Multimodal RAG Platform")
    doc = b"Architecture notes. The retriever first runs BM25 and dense search in parallel. Results are merged with reciprocal rank fusion before re-ranking."
    r = client.post("/documents", files={"file": ("design.txt", doc, "text/plain")}, data={"project_id": rag["id"]})
    assert r.status_code == 201 and r.json()["chunks"] >= 1
    gen = client.post("/answers/generate", json={"question": "How does the retriever in your RAG project merge results?"}).json()
    labels = [s["label"] for s in gen["answer"]["sources"]]
    assert any("design.txt" in lbl for lbl in labels)
    assert client.delete(f"/documents/{r.json()['id']}").status_code == 204
    assert all(d["kind"] != "project_doc" for d in client.get("/documents").json())


# ----------------------------------------------------------------------------- jobs, generation, evaluation


def test_job_parse_and_match(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    jd = ("Senior Backend Engineer\nCompany: Finly\nAbout the role\nWe build payments infrastructure for banking.\n"
          "Responsibilities\n- Design REST APIs in Python\n- Operate services on AWS\nRequirements\n- 3+ years of Python\n"
          "- PostgreSQL and Redis\n- Docker\nNice to have\n- Kubernetes\n- Kafka\n")
    job = client.post("/jobs", data={"text": jd}).json()
    p = job["parsed"]
    assert p["role"] == "Senior Backend Engineer" and job["company"] == "Finly"
    assert {"Python", "PostgreSQL", "Redis", "Docker"} <= set(p["required_skills"])
    assert {"Kubernetes", "Kafka"} <= set(p["preferred_skills"]) and p["min_years"] == 3
    assert p["domain"] == "fintech"
    m = job["match"]
    assert "Redis" in m["missing"] and "Python" in m["matched"] and 0 < m["score"] < 1
    assert len(client.get("/jobs").json()) == 1
    assert client.delete(f"/jobs/{job['id']}").status_code == 204


def test_classify_generate_evaluate_endpoints(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    c = client.post("/questions/classify", json={"text": "How would you design a rate limiter?"}).json()
    assert c["type"] == "SYSTEM_DESIGN" and c["requires_technical_context"]
    g = client.post("/answers/generate", json={"question": "What did you do at DataNest?", "answer_length": "20s"}).json()
    assert "churn prediction" in g["answer"]["text"] and g["latency"]["total_ms"] > 0
    e = client.post("/answers/evaluate", json={"question": "What did you do at DataNest?",
                                               "answer": "I built a churn model with XGBoost and I led a team of 12 people at Google."}).json()
    assert e["claims_not_in_profile"] and e["scores"]["hallucination_risk"] > 0


# ----------------------------------------------------------------------------- mock interview


def test_mock_interview_flow_with_adaptive_follow_up(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "resume_drill"}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        first = _collect(ws, "transcript.final")[-1]
        assert first["turn"]["role"] == "interviewer" and first["speak"]
        ws.send_text(json.dumps({"type": "utterance.text", "role": "candidate",
                                 "text": "I'm Arjun, an AI student. I built a Multimodal RAG Platform with FastAPI and Qdrant, "
                                         "and I trained a bug classifier that reached 87% accuracy."}))
        events = _collect(ws, "transcript.final")  # candidate turn
        events += _collect(ws, "feedback")
        fb = events[-1]
        assert 0 <= fb["evaluation"]["overall"] <= 1
        nxt = _collect(ws, "transcript.final")[-1]
        assert nxt["turn"]["role"] == "interviewer"
        assert nxt["turn"]["text"] != first["turn"]["text"]
        # resume drill probes the candidate's own claim
        assert "You said you" in nxt["turn"]["text"] or nxt["turn"]["mock"]["kind"] == "follow_up"
        ws.send_text(json.dumps({"type": "utterance.text", "role": "candidate", "text": "I'm not sure, maybe."}))
        _collect(ws, "feedback")
        _collect(ws, "transcript.final")
        ws.send_text(json.dumps({"type": "control", "action": "end"}))
        _collect(ws, "session.ended")
    rep = client.get(f"/interviews/{sid}/report").json()
    assert rep["scored_on"] == "candidate_answers"
    assert rep["scores"]["overall"] is not None


def test_answer_variants_over_ws(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True,
                                           "answer_length": "90s"}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "utterance.text", "role": "interviewer", "text": "Tell me about yourself."}))
        full = _collect(ws, "answer.complete")[-1]
        ws.send_text(json.dumps({"type": "answer.variant", "question_id": full["question_id"], "variant": "shorter"}))
        short = _collect(ws, "answer.complete")[-1]
    assert short["variant"] == "shorter"
    assert short["answer"]["word_count"] < full["answer"]["word_count"]
    detail = client.get(f"/interviews/{sid}").json()
    cand = [t for t in detail["turns"] if t["role"] == "candidate"]
    assert len(cand) == 1 and cand[0]["text"] == short["answer"]["text"]
    q = client.get(f"/interviews/{sid}/questions/{full['question_id']}").json()
    assert [a["variant"] for a in q["answers"]] == ["default", "shorter"]


def test_transcript_search(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "utterance.text", "role": "interviewer", "text": "Tell me about your RAG project."}))
        _collect(ws, "answer.complete")
    res = client.get(f"/interviews/{sid}/search", params={"q": "qdrant"}).json()
    assert res["count"] >= 1 and res["results"][0]["matches"]
    res = client.get(f"/interviews/{sid}/search", params={"q": "RAG"}).json()
    assert res["count"] >= 2  # topic + text matches


# ----------------------------------------------------------------------------- practice & analytics


def test_question_bank_practice_and_analytics(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    bank = client.get("/question-bank").json()
    assert {"Projects", "Resume", "Behavioral", "HR"} <= set(bank["categories"])
    assert any(i["question"] == "What was the biggest challenge in the Multimodal RAG Platform?" for i in bank["items"])
    item = next(i for i in bank["items"] if i["question"] == "What is overfitting and how do you prevent it?")
    r = client.post("/practice", json={"question_bank_id": item["id"],
                                       "answer": "Overfitting is when a model memorises noise. I'd use regularization, "
                                                 "cross-validation and early stopping."}).json()
    assert r["concepts_covered"] and r["item"]["attempts"] == 1
    for _ in range(2):
        client.post("/practice", json={"question_bank_id": item["id"], "answer": "Not sure."})
    a = client.get("/analytics").json()
    assert a["practice_attempts"] == 3
    d = client.get("/dashboard").json()
    assert d["questions_practiced"] >= 3 and d["profile"]["verification"]["ratio"] == 1.0
    assert client.get("/practice/history").json()[0]["question"] == item["question"]


# ----------------------------------------------------------------------------- privacy


def test_export_and_delete_account_removes_everything(client):
    user = register(client)
    upload_resume(client)
    confirm_all(client)
    store = get_vector_store()
    assert store.count("projects", user["id"]) > 0
    export = client.get("/privacy/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    data = export.json()
    assert data["profile"]["personal"]["name"] == "Arjun Mehta"
    assert data["recordings"] == "Raw audio is not stored."
    assert client.delete("/account").status_code == 204
    assert store.count("projects", user["id"]) == 0
    assert client.post("/auth/login", json={"email": "candidate@example.com", "password": "Passw0rd!x"}).status_code == 401


def test_delete_resume_and_transcript(client):
    register(client)
    upload_resume(client)
    confirm_all(client)
    assert client.delete("/resume").status_code == 204
    prof = client.get("/resume").json()["profile"]
    assert prof["projects"] == [] and prof["skills"] == []
    sid = client.post("/interviews", json={"mode": "live_coaching", "consent_acknowledged": True}).json()["id"]
    with _open(client, sid) as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "utterance.text", "role": "interviewer", "text": "Hi, how are you?"}))
        _collect(ws, "answer.complete")
    assert client.delete(f"/interviews/{sid}/transcript").status_code == 204
    assert client.get(f"/interviews/{sid}").json()["turns"] == []
    assert client.delete(f"/interviews/{sid}").status_code == 204


def test_settings_and_status(client):
    register(client)
    r = client.patch("/settings", json={"answer_length": "20s", "font_scale": 3})
    assert r.json()["preferences"]["answer_length"] == "20s" and r.json()["preferences"]["font_scale"] == 1.5
    st = client.get("/system/status").json()
    assert st["llm"]["provider"] == "offline" and st["stt"]["provider"] == "browser"
    assert client.get("/health").json()["status"] == "ok"
