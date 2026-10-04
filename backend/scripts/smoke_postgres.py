"""End-to-end smoke test against REAL PostgreSQL and a Qdrant server
(the unit suite uses SQLite + in-process Qdrant).

    DATABASE_URL=postgresql://... QDRANT_URL=http://localhost:6333 python scripts/smoke_postgres.py

Requires `alembic upgrade head` to have run. Exercises: register, resume
upload + verification, job, live WebSocket acceptance scenario (with STT
segments), report, transcript search, and account deletion (FK cascades +
vector cleanup). Exits non-zero on the first failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LLM_PROVIDER", "offline")
os.environ.setdefault("TURN_ENDPOINT_MS", "300")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.vectorstore import get_vector_store  # noqa: E402

CSRF = {"x-interviewos-csrf": "1"}
RESUME = (Path(__file__).resolve().parents[1] / "app/evaluation/fixtures/sample_resume.txt").read_bytes()


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"ok   {msg}")


def collect(ws, until: str, timeout: float = 30) -> list[dict]:
    out, deadline = [], time.time() + timeout
    while time.time() < deadline:
        ev = json.loads(ws.receive_text())
        out.append(ev)
        if ev.get("event") == until:
            return out
    raise SystemExit(f"FAIL: timed out waiting for {until}")


def main() -> None:
    s = get_settings()
    check(s.database_url.startswith("postgres"), f"using PostgreSQL ({s.database_url.split('@')[-1]})")
    check(s.qdrant_url.startswith("http"), f"using Qdrant server ({s.qdrant_url})")
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app) as c:
        c.headers.update(CSRF)
        r = c.post("/auth/register", json={"email": email, "password": "SmokeTest123"})
        check(r.status_code == 201, "register")
        uid = r.json()["id"]
        up = c.post("/resume/upload", files={"file": ("resume.txt", RESUME, "text/plain")}).json()
        check(up["knowledge_base"]["vector_index"] is True, f"resume indexed ({up['knowledge_base']['chunks']} chunks)")
        check(c.patch("/resume", json={"operations": [{"action": "confirm_all"}]}).status_code == 200, "verify profile")
        job = c.post("/jobs", data={"text": "ML Engineer. Requirements: Python, FastAPI, Qdrant, AWS, machine learning experience."}).json()
        check(job["match"]["score"] > 0, "job match")
        sid = c.post("/interviews", json={"mode": "live_coaching", "job_id": job["id"], "consent_acknowledged": True}).json()["id"]
        ticket = c.post(f"/interviews/{sid}/ticket").json()["ticket"]
        with c.websocket_connect(f"/interviews/{sid}/live?ticket={ticket}") as ws:
            ws.send_text(json.dumps({"type": "hello"}))
            json.loads(ws.receive_text())
            answers = []
            for q in ["Tell me about your RAG project.", "Why did you choose Qdrant?", "What was the biggest challenge?"]:
                ws.send_text(json.dumps({"type": "transcript.segment", "text": q, "stt_ms": 150}))
                answers.append(collect(ws, "answer.complete")[-1]["answer"])
            check(answers[0]["text"].startswith("My Multimodal RAG Platform"), "project answer grounded")
            check(answers[1]["text"].startswith("I chose Qdrant"), "follow-up resolved to the RAG project")
            check(answers[2]["insufficient_context"], "missing challenge is not invented")
            ws.send_text(json.dumps({"type": "control", "action": "end"}))
            collect(ws, "session.ended")
        rep = c.get(f"/interviews/{sid}/report").json()
        check(rep["summary"]["questions"] == 3, "report generated")
        check(c.get(f"/interviews/{sid}/search", params={"q": "qdrant"}).json()["count"] > 0, "transcript search")
        store = get_vector_store()
        check(store is not None and store.count("projects", uid) > 0, "vectors stored with candidate_id")
        check(c.delete("/account").status_code == 204, "delete account (FK cascades)")
        check(store.count("projects", uid) == 0 and store.count("interview_history", uid) == 0, "vectors deleted")
        check(c.post("/auth/login", json={"email": email, "password": "SmokeTest123"}).status_code == 401, "account gone")
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
