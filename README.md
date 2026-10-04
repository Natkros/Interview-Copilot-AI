# InterviewOS AI

**Your Resume. Your Experience. Your AI Interview Coach.** (internal name: Real-Time Interview Intelligence Engine)

InterviewOS turns your resume into a verified candidate profile and answers interview questions from those verified facts first. It covers resume ingestion, hybrid RAG on Qdrant, streaming speech recognition, follow-up resolution, grounded answer generation with claim checking, mock interviews, job matching, reports and analytics.

It's built for preparation, mock interviews, coaching, accessibility, and interviews where AI assistance is explicitly allowed. Live coaching asks you to confirm that, and the app makes no attempt to hide from screen sharing or proctoring.

## Quick start (local, no API keys needed)

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or .venv/bin/pip
.venv/Scripts/python scripts/seed_demo.py            # optional demo account (see script for credentials)
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev            # http://localhost:3000
```

With no keys set, the backend runs offline. It uses SQLite, embedded Qdrant (`./qdrant_data`), hashing embeddings, browser speech recognition, and a deterministic answer composer that only quotes your profile. Add `LLM_API_KEY` to turn on Claude for answer generation. Every variable is documented in `.env.example`.

**Docker:** `cp .env.example .env && docker compose up --build`. This brings up PostgreSQL, the Qdrant server, Redis, the backend (which runs Alembic migrations on start) and the frontend.

**Production:** `docker compose -f docker-compose.yml -f infrastructure/docker-compose.prod.yml up -d --build`. This adds nginx with TLS (certificates go in `infrastructure/certs/`), same-origin WebSockets and closed internal ports. In production, set `ENVIRONMENT=production`, a random `AUTH_SECRET`, `COOKIE_SECURE=true` and `POSTGRES_PASSWORD`; the app refuses to start without them. Live sessions are held in process, so either run one API worker or use sticky sessions.

## Architecture

```
Browser (Next.js 16, Tailwind, shadcn/ui)
  ├─ /api/*  ── rewrite ──▶ FastAPI (REST, OpenAPI at /docs)
  └─ WS /interviews/{id}/live?ticket=…  (single-use ticket)
        │
        ▼
 TurnDetector (VAD endpointing, self-correction cleanup)
  → Classifier (24 question types, deterministic)
  → ConversationManager (focus project/technology, follow-up resolution, short- and long-term memory)
  → Supervisor plans agents → parallel hybrid retrieval (Qdrant dense + BM25 + metadata, RRF, source priorities)
  → AnswerAgent (Claude streaming, or the offline composer as default and as degraded-mode fallback)
  → ValidationAgent (claim extraction → evidence → unsupported claims removed)
  → EvaluationAgent (separate heuristic scorer; optional LLM judge)
  → structured WS events (transcript.*, question.classified, context.ready, answer.delta/complete, …)
```

| Path | Contents |
|---|---|
| `backend/app/services` | document validation and extraction, resume parser, profile verification, JD parser and match, question bank, reports |
| `backend/app/rag` | embeddings, Qdrant store (every search is forced to filter on `candidate_id`), chunking, BM25, hybrid retriever |
| `backend/app/agents` | classifier, conversation manager, prompts, LLM provider, composer, validation, evaluation, supervisor, mock interviewer |
| `backend/app/speech` | turn detector, Deepgram streaming relay, opt-in recordings |
| `backend/app/websocket/live.py` | event contract, replay-on-reconnect buffer, live runner |
| `frontend/app/(app)/interview/[id]` | live conversation UI, question and answer panels, sources, timeline, search, report |

## Provider configuration

| Capability | Default (no key) | Upgrade |
|---|---|---|
| Answers | offline extractive composer | `LLM_API_KEY` (Anthropic; `LLM_MODEL=claude-opus-5-5`, `LLM_EFFORT=low`) |
| Speech-to-text | browser Web Speech API | `STT_PROVIDER=deepgram`, `STT_API_KEY` (audio is relayed server-side; mic or shared-tab audio) |
| Embeddings | `hash` (lexical-semantic) | `EMBEDDING_PROVIDER=openai` with any OpenAI-compatible `/v1/embeddings` endpoint (`EMBEDDING_*`) |
| Vector DB | embedded Qdrant | `QDRANT_URL=http://…`, `QDRANT_API_KEY` |

API keys only ever live in backend environment variables. The frontend never sees them.

## Grounding guarantees

- Facts extracted from a resume start out **unverified**. Verified facts get full grounding credit; unverified ones get half.
- Before an answer is shown, every candidate-specific sentence is checked. Numbers, technologies and proper nouns must all appear in your knowledge base, or in what you've already said in this session, and the sentence's content words must overlap enough with that evidence. Sentences that fail are removed.
- When your profile lacks the requested information, the answer says so instead of making it up. For example: *"Your project information doesn't specify a particular challenge. A safe way to answer this would be to explain a challenge you personally encountered…"*
- The grounding score is a measured support ratio from method `lexical-entity-v1`, not a probability.

## Testing & evaluation

```bash
cd backend && pytest -q                     # 108 tests: unit, integration, WebSocket end-to-end, adversarial, security
python -m app.evaluation.benchmark          # writes evaluation_reports/*.md|json
python scripts/smoke_postgres.py            # full flow against real PostgreSQL + Qdrant server
cd frontend && npx eslint . && npx tsc --noEmit && npm run build
```

Measured benchmark (offline composer, 41 labelled items, 2026-10-04):

| Metric | Value |
|---|---|
| Classification accuracy | 1.00 |
| Retrieval Recall@5 / Precision@5 | 0.88 / 0.40 |
| Answer correctness (labelled must / must-not) | 0.976 |
| Mean grounding score | 0.96 |
| Answers where the generator produced an unsupported claim (all caught) | 0.024 |
| Unsupported claims shipped (re-validated) | 0.00 |
| Insufficient-context recall / precision | 1.00 / 0.91 |
| End-to-end latency p50 / p95 (in-process, no STT or network) | 31 ms / 105 ms |

**Caveats, read these:**

- The benchmark has one resume and 41 items, and it was used during development, so the numbers are optimistic.
- The "shipped" hallucination rate is measured with the same validator that does the filtering. It shows that nothing the validator flags reaches the user, not that the validator catches every possible fabrication.
- The latency figures are for the offline composer. Run `--provider anthropic` (billed) to measure with Claude.
- Precision@5 is low by design: only a few chunks per item are labelled as relevant.

## Known gaps / next steps

- **Docker images and the Postgres smoke test were written but not run here** (the Docker daemon wasn't available). CI runs both.
- The Deepgram relay and real-microphone capture haven't been exercised against the live services. The STT event path is tested by simulating segments.
- OCR for scanned PDFs needs the optional `ocr` extra plus the tesseract and poppler binaries.
- The classifier and evaluator are heuristic. Expanding the benchmark is the best way to tune them.
