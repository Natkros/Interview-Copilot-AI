"""Live interview WebSocket: `WS /interviews/{id}/live?ticket=...`

Event contract (server -> client; every event carries `seq`, `session_id`, `ts`):
  session.snapshot, stt.state, transcript.partial, transcript.final,
  question.classified, context.ready, answer.start, answer.delta, answer.reset,
  answer.complete, answer.skipped, answer.cancelled, turn.candidate, turn.updated,
  feedback, summary.updated, degraded, error, session.ended, pong

Client -> server messages (JSON text frames, `type` field):
  hello {last_seq}, transcript.partial {text}, transcript.segment {text, stt_ms},
  vad {speaking}, utterance.text {text, role}, answer.variant {question_id, variant},
  control {action: pause|resume|end|answer_now}, settings {answer_length}, ping
Binary frames: PCM16 16 kHz mono audio when server-side STT is configured.

Reliability: every event is numbered and buffered per session; a client that
reconnects sends `hello{last_seq}` and receives what it missed. Turns are
persisted before they are broadcast, so a dropped socket never loses the
conversation (GET /interviews/{id} rebuilds it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.agents.conversation import ConversationManager, detect_aspect
from app.agents.evaluation import evaluate_answer
from app.agents.mock_interviewer import MOCK_MODES, MockInterviewer
from app.agents.supervisor import (
    InterviewPipeline,
    PipelineContext,
    after_answer,
    build_overview,
    load_job,
    load_profile,
)
from app.agents.validation import GroundingValidator
from app.core.config import get_settings
from app.core.logging import session_id_var
from app.core.security import ws_tickets
from app.database.models import ConversationTurn, InterviewSession, Question, User
from app.database.session import session_factory
from app.models.domain import Classification, FocusState, QuestionType, ResolvedQuestion
from app.rag.vectorstore import vector_store_status
from app.services.reports import save_report
from app.speech.recording import RecordingWriter, recording_allowed
from app.speech.stt import STTUnavailable, server_stt, stt_config
from app.speech.turn_detector import DetectorConfig, SttState, TurnDetector

log = logging.getLogger(__name__)
REPLAY_BUFFER = 2000
RUNNER_IDLE_TTL_S = 600
VARIANTS = {"regenerate", "shorter", "longer", "technical", "natural", "default"}


class SessionHub:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.seq = 0
        self.buffer: deque[dict[str, Any]] = deque(maxlen=REPLAY_BUFFER)
        self.sockets: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def emit(self, event: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            self.seq += 1
            payload = {**event, "seq": self.seq, "session_id": self.session_id, "ts": time.time()}
            # deltas are ephemeral; everything else is replayable
            if event.get("event") not in ("transcript.partial",):
                self.buffer.append(payload)
            dead = []
            for ws in self.sockets:
                try:
                    await ws.send_text(json.dumps(payload, default=str))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.sockets.discard(ws)
            return payload

    async def replay(self, ws: WebSocket, last_seq: int) -> bool:
        """Send buffered events after last_seq. Returns False if the gap is too old."""
        events = [e for e in self.buffer if e["seq"] > last_seq]
        if self.buffer and last_seq and self.buffer[0]["seq"] > last_seq + 1:
            return False
        for e in events:
            await ws.send_text(json.dumps({**e, "replayed": True}, default=str))
        return True


class LiveSessionRunner:
    """Per-session engine shared across reconnects."""

    def __init__(self, session_id: str, user_id: str) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.hub = SessionHub(session_id)
        s = get_settings()
        self.detector = TurnDetector(DetectorConfig(endpoint_ms=s.turn_endpoint_ms, min_words=s.turn_min_words))
        self.answer_task: asyncio.Task | None = None
        self.ticker: asyncio.Task | None = None
        self.stt = None
        self.last_activity = time.time()
        self.mode = "live_coaching"
        self.answer_length = "45s"
        self.closed = False
        self.queue: asyncio.Queue[tuple[str, str, float | None]] = asyncio.Queue()
        self.recorder = None
        self.worker: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            assert sess is not None
            self.mode = sess.mode
            self.answer_length = sess.answer_length
            if sess.status in ("created", "paused"):
                sess.status = "live"
                sess.started_at = sess.started_at or datetime.now(UTC)
            db.commit()
        self.detector.start()
        self.ticker = asyncio.create_task(self._tick_loop())
        self.worker = asyncio.create_task(self._work_loop())

    def _close_recording(self) -> None:
        if self.recorder is not None:
            self.recorder.finalize()
            self.recorder = None

    async def stop(self) -> None:
        self.closed = True
        self._close_recording()
        for t in (self.ticker, self.worker, self.answer_task):
            if t:
                t.cancel()
        if self.stt:
            with contextlib.suppress(Exception):
                await self.stt.stop()
            self.stt = None

    async def _tick_loop(self) -> None:
        last_state = None
        while not self.closed:
            await asyncio.sleep(0.1)
            utterance = self.detector.tick(time.monotonic())
            if utterance:
                stt_ms = self.detector.stt_latency_ms[-1] if self.detector.stt_latency_ms else None
                await self.queue.put((utterance, self._speaker_role(), stt_ms))
            if self.detector.state != last_state:
                last_state = self.detector.state
                await self.hub.emit({"event": "stt.state", "state": last_state.value})

    async def _work_loop(self) -> None:
        while not self.closed:
            text, role, stt_ms = await self.queue.get()
            try:
                await self.handle_utterance(text, role, stt_ms)
            except Exception as exc:  # never let one utterance kill the session
                log.exception("utterance handling failed")
                await self.hub.emit({"event": "error", "code": "processing_failed", "recoverable": True,
                                     "message": f"Could not process that utterance ({type(exc).__name__}). Your transcript is preserved."})

    def _speaker_role(self) -> str:
        # In live coaching the microphone hears the interviewer; in mock modes the
        # AI is the interviewer and the microphone hears the candidate.
        return "candidate" if self.mode in MOCK_MODES else "interviewer"

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> dict[str, Any]:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            turns = db.scalars(select(ConversationTurn).where(ConversationTurn.session_id == self.session_id)
                               .order_by(ConversationTurn.seq.desc()).limit(200)).all()
            focus = (sess.state or {}).get("focus", {}) if sess else {}
            return {
                "event": "session.snapshot",
                "status": sess.status if sess else "unknown",
                "mode": self.mode,
                "answer_length": self.answer_length,
                "stt": stt_config(),
                "stt_state": self.detector.state.value,
                "turns": [_turn_payload(t) for t in reversed(turns)],
                "focus": {"project": focus.get("project_name"), "topic": focus.get("topic"),
                          "technology": focus.get("technology")},
                "services": {"vector_store": vector_store_status(),
                             "llm": get_settings().resolved_llm_provider},
            }

    # ------------------------------------------------------------------ inbound

    async def on_message(self, msg: dict[str, Any]) -> None:
        self.last_activity = time.time()
        mtype = msg.get("type")
        now = time.monotonic()
        if mtype == "transcript.partial":
            text = str(msg.get("text", ""))[:2000]
            self.detector.on_partial(text, now)
            await self.hub.emit({"event": "transcript.partial", "role": self._speaker_role(), "text": text})
        elif mtype == "transcript.segment":
            stt_ms = msg.get("stt_ms")
            self.detector.on_final(str(msg.get("text", ""))[:4000], now, float(stt_ms) if isinstance(stt_ms, int | float) else None)
            await self.hub.emit({"event": "transcript.partial", "role": self._speaker_role(), "text": self.detector.buffer_text})
        elif mtype == "vad":
            self.detector.on_vad(bool(msg.get("speaking")), now)
        elif mtype == "utterance.text":
            text = str(msg.get("text", "")).strip()[:4000]
            role = msg.get("role") if msg.get("role") in ("interviewer", "candidate") else self._speaker_role()
            if text:
                await self.queue.put((text, role, None))
        elif mtype == "answer.variant":
            variant = msg.get("variant") if msg.get("variant") in VARIANTS else "regenerate"
            qid = str(msg.get("question_id", ""))
            self._start_answer_task(self._run_variant(qid, variant))
        elif mtype == "control":
            await self._control(str(msg.get("action")))
        elif mtype == "settings":
            if msg.get("answer_length") in ("20s", "45s", "90s", "detailed"):
                self.answer_length = msg["answer_length"]
                with session_factory()() as db:
                    sess = db.get(InterviewSession, self.session_id)
                    if sess:
                        sess.answer_length = self.answer_length
                        db.commit()
        elif mtype == "audio.start":
            await self._start_server_stt()
        elif mtype == "audio.stop":
            if self.stt:
                await self.stt.stop()
                self.stt = None
            self._close_recording()
        elif mtype == "ping":
            await self.hub.emit({"event": "pong"})

    async def on_audio(self, frame: bytes) -> None:
        if self.stt is None:
            return
        if self.recorder is not None:
            self.recorder.write(frame)
        try:
            await self.stt.send_audio(frame)
        except Exception:
            await self.hub.emit({"event": "degraded", "service": "stt",
                                 "message": "Speech recognition is unavailable. You can type the question instead; your transcript is preserved."})
            self.stt = None

    async def _start_server_stt(self) -> None:
        try:
            stt = server_stt()
        except STTUnavailable as exc:
            await self.hub.emit({"event": "degraded", "service": "stt", "message": str(exc)})
            return
        if stt is None:
            return

        async def on_partial(text: str) -> None:
            self.detector.on_partial(text, time.monotonic())
            await self.hub.emit({"event": "transcript.partial", "role": self._speaker_role(), "text": self.detector.buffer_text})

        async def on_final(text: str, latency: float | None) -> None:
            self.detector.on_final(text, time.monotonic(), latency)
            await self.hub.emit({"event": "transcript.partial", "role": self._speaker_role(), "text": self.detector.buffer_text})

        async def on_vad(speaking: bool) -> None:
            self.detector.on_vad(speaking, time.monotonic())

        async def on_error(message: str) -> None:
            await self.hub.emit({"event": "degraded", "service": "stt", "message": message + " Your transcript is preserved."})

        try:
            await stt.start(on_partial, on_final, on_vad, on_error)
            self.stt = stt
            with session_factory()() as db:
                user = db.get(User, self.user_id)
                if user and recording_allowed(user.preferences) and self.recorder is None:
                    self.recorder = RecordingWriter(self.session_id)
        except STTUnavailable as exc:
            await self.hub.emit({"event": "degraded", "service": "stt", "message": str(exc)})

    async def _control(self, action: str) -> None:
        if action == "pause":
            self.detector.pause()
            await self._set_status("paused")
        elif action == "resume":
            self.detector.start()
            await self._set_status("live")
        elif action == "answer_now":
            utterance = self.detector.flush()
            if utterance:
                await self.queue.put((utterance, self._speaker_role(), None))
        elif action == "end":
            await self.end()
        elif action == "start_mock" and self.mode in MOCK_MODES:
            await self._mock_open()

    async def _set_status(self, status: str) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            if sess:
                sess.status = status
                db.commit()
        await self.hub.emit({"event": "session.status", "status": status})

    async def end(self) -> None:
        if self.answer_task and not self.answer_task.done():
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self.answer_task), timeout=20)
        self.detector.pause()
        self._close_recording()
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            if sess is None:
                return
            sess.status = "ended"
            sess.ended_at = datetime.now(UTC)
            rep = save_report(db, sess)
            db.commit()
            report_id = rep.id
        await self.hub.emit({"event": "session.ended", "report_id": report_id})

    # ------------------------------------------------------------------ processing

    def _start_answer_task(self, coro) -> None:
        if self.answer_task and not self.answer_task.done():
            self.answer_task.cancel()
        self.answer_task = asyncio.create_task(self._guard(coro))

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            await self.hub.emit({"event": "answer.cancelled", "reason": "superseded by a newer question"})
            raise
        except Exception as exc:
            log.exception("answer generation failed")
            await self.hub.emit({"event": "error", "code": "answer_failed", "recoverable": True,
                                 "message": f"Answer generation failed ({type(exc).__name__}). Your transcript is preserved - try Regenerate."})
        finally:
            if self.detector.state in (SttState.PROCESSING, SttState.QUESTION_READY):
                self.detector.state = SttState.ANSWER_READY

    async def handle_utterance(self, text: str, role: str, stt_ms: float | None) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            profile = load_profile(db, self.user_id)
            mgr = ConversationManager(db, sess, profile)
            clean = mgr.receive_transcript(text)
            if role == "interviewer":
                cls = mgr.classify_utterance(clean)
                turn = mgr.store_turn("interviewer", clean, meta={"classification": cls.model_dump(mode="json"),
                                                                  "stt_ms": stt_ms})
                db.commit()
                await self.hub.emit({"event": "transcript.final", "turn": _turn_payload(turn)})
                if self.mode in MOCK_MODES:
                    return
                self.detector.state = SttState.PROCESSING
                self._start_answer_task(self._answer(clean, cls, turn.id, stt_ms))
            else:
                if self.mode in MOCK_MODES:
                    turn = mgr.store_turn("candidate", clean, kind="spoken")
                    db.commit()
                    await self.hub.emit({"event": "transcript.final", "turn": _turn_payload(turn)})
                    self._start_answer_task(self._mock_feedback(turn.id, clean))
                else:
                    turn = mgr.store_turn("candidate", clean, kind="spoken")
                    db.commit()
                    await self.hub.emit({"event": "transcript.final", "turn": _turn_payload(turn)})

    async def _answer(self, question: str, cls: Classification, turn_id: str, stt_ms: float | None) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            user = db.get(User, self.user_id)
            profile = load_profile(db, self.user_id)
            mgr = ConversationManager(db, sess, profile)
            ctx = PipelineContext(db=db, user=user, profile=profile, session=sess, manager=mgr,
                                  job=load_job(db, self.user_id, sess.job_id), length=self.answer_length,
                                  overview=build_overview(profile))
            pipeline = InterviewPipeline(ctx)
            complete = None
            question_id = None
            async for ev in pipeline.run(question, cls=cls, stt_ms=stt_ms):
                if ev["event"] == "question.classified":
                    question_id = ev["question_id"]
                    turn = db.get(ConversationTurn, turn_id)
                    if turn:
                        turn.question_id = question_id
                    db.commit()
                    ev = {**ev, "turn_id": turn_id}
                if ev["event"] == "answer.complete":
                    complete = ev
                    continue
                await self.hub.emit(ev)
            if complete is None:
                db.commit()
                self.detector.state = SttState.LISTENING
                return
            ans = complete["answer"]
            cturn = mgr.store_turn("candidate", ans["text"], kind="suggestion", question_id=question_id,
                                   answer_id=complete["answer_id"],
                                   meta={"grounding_score": ans["grounding"]["grounding_score"],
                                         "insufficient_context": ans["insufficient_context"], "question": question})
            weak = [k for k, v in (sess.state or {}).get("topic_scores", {}).items() if v < 0.6]
            summary = after_answer(db, mgr, self.user_id, self.session_id, question, ans["text"], cturn.id,
                                   cls.topic, mgr.focus.project_name, weak)
            db.commit()
            await self.hub.emit({**complete, "turn": _turn_payload(cturn)})
            if ans.get("degraded"):
                await self.hub.emit({"event": "degraded", "service": "llm",
                                     "message": "AI service temporarily unavailable. Your transcript is still being preserved; answers are assembled from your profile."})
            if summary:
                await self.hub.emit({"event": "summary.updated", "summary": summary})
        self.detector.state = SttState.ANSWER_READY

    async def _run_variant(self, question_id: str, variant: str) -> None:
        with session_factory()() as db:
            q = db.get(Question, question_id)
            if q is None or q.user_id != self.user_id or q.session_id != self.session_id:
                await self.hub.emit({"event": "error", "code": "not_found", "recoverable": True, "message": "Question not found."})
                return
            sess = db.get(InterviewSession, self.session_id)
            user = db.get(User, self.user_id)
            profile = load_profile(db, self.user_id)
            mgr = ConversationManager(db, sess, profile)
            cls = Classification(**q.classification)
            resolved = ResolvedQuestion(original=q.text, resolved=q.resolved_text, focus=FocusState(**(q.focus or {})),
                                        aspect=detect_aspect(q.text), is_follow_up=q.qtype == QuestionType.FOLLOW_UP.value)
            from app.database.models import Answer

            prev = db.scalars(select(Answer).where(Answer.question_id == q.id).order_by(Answer.created_at.desc())).first()
            ctx = PipelineContext(db=db, user=user, profile=profile, session=sess, manager=mgr,
                                  job=load_job(db, self.user_id, sess.job_id), length=self.answer_length,
                                  overview=build_overview(profile), reference_words=prev.word_count if prev else None)
            complete = None
            async for ev in InterviewPipeline(ctx).run(q.text, cls=cls, resolved=resolved, question_row=q, variant=variant):
                if ev["event"] == "answer.complete":
                    complete = ev
                    continue
                if ev["event"] == "question.classified":
                    continue
                await self.hub.emit({**ev, "variant": variant})
            if complete:
                turn = db.scalars(select(ConversationTurn).where(
                    ConversationTurn.session_id == self.session_id, ConversationTurn.question_id == q.id,
                    ConversationTurn.role == "candidate", ConversationTurn.kind == "suggestion")).first()
                if turn:
                    turn.text = complete["answer"]["text"]
                    turn.answer_id = complete["answer_id"]
                    turn.meta = {**(turn.meta or {}), "variant": variant,
                                 "grounding_score": complete["answer"]["grounding"]["grounding_score"]}
                db.commit()
                await self.hub.emit({**complete, "variant": variant, "turn": _turn_payload(turn) if turn else None})

    # ------------------------------------------------------------------ mock interviews

    async def _mock_open(self) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            has_turns = db.scalar(select(ConversationTurn.id).where(ConversationTurn.session_id == self.session_id).limit(1))
            if has_turns:
                return
            profile = load_profile(db, self.user_id)
            mock = MockInterviewer(db, sess, profile, load_job(db, self.user_id, sess.job_id))
            q = mock.opening()
            mgr = ConversationManager(db, sess, profile)
            turn = mgr.store_turn("interviewer", q["text"], meta={"mock": q})
            db.commit()
        await self.hub.emit({"event": "transcript.final", "turn": _turn_payload(turn), "speak": True})

    async def _mock_feedback(self, turn_id: str, answer: str) -> None:
        with session_factory()() as db:
            sess = db.get(InterviewSession, self.session_id)
            profile = load_profile(db, self.user_id)
            mgr = ConversationManager(db, sess, profile)
            job = load_job(db, self.user_id, sess.job_id)
            last_q = db.scalars(select(ConversationTurn).where(
                ConversationTurn.session_id == self.session_id, ConversationTurn.role == "interviewer")
                .order_by(ConversationTurn.seq.desc())).first()
            question = last_q.text if last_q else ""
            cls = mgr.classify_utterance(question) if question else Classification(type=QuestionType.UNKNOWN)
            # check the candidate's own claims against their verified profile
            from app.agents.supervisor import candidate_entities
            from app.models.domain import ContextBundle
            from app.rag.retriever import HybridRetriever

            bundle = ContextBundle(evidence_pool=HybridRetriever(db, self.user_id).evidence_pool(self.session_id))
            _, grounding = GroundingValidator(candidate_entities(profile)).validate(answer, bundle, rewrite=False)
            kws = list((job.parsed or {}).get("required_skills", [])) if job else []
            scores = evaluate_answer(question, answer, cls, grounding, job_keywords=kws, length=sess.answer_length,
                                     is_candidate_spoken=True)
            turn = db.get(ConversationTurn, turn_id)
            if turn:
                turn.meta = {**(turn.meta or {}), "evaluation": scores.model_dump(mode="json"), "question": question,
                             "topic": cls.topic or ((last_q.meta or {}).get("mock") or {}).get("category")}
            unverified = [c.text for c in grounding.claims if c.kind == "candidate" and not c.supported]
            feedback = {"event": "feedback", "turn_id": turn_id, "question": question,
                        "evaluation": scores.model_dump(mode="json"),
                        "claims_not_in_profile": unverified[:5]}
            mock = MockInterviewer(db, sess, profile, job)
            nq = await mock.next_question(answer, scores)
            nturn = mgr.store_turn("interviewer", nq["text"], meta={"mock": nq})
            db.commit()
        await self.hub.emit(feedback)
        await self.hub.emit({"event": "transcript.final", "turn": _turn_payload(nturn), "speak": True})


def _turn_payload(t: ConversationTurn) -> dict[str, Any]:
    meta = t.meta or {}
    return {
        "id": t.id, "seq": t.seq, "role": t.role, "kind": t.kind, "text": t.text,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "question_id": t.question_id, "answer_id": t.answer_id,
        "classification": meta.get("classification"), "grounding_score": meta.get("grounding_score"),
        "insufficient_context": meta.get("insufficient_context"), "evaluation": meta.get("evaluation"),
        "mock": meta.get("mock"),
    }


turn_payload = _turn_payload


# ----------------------------------------------------------------------------- registry

_runners: dict[str, LiveSessionRunner] = {}


async def get_runner(session_id: str, user_id: str) -> LiveSessionRunner:
    runner = _runners.get(session_id)
    if runner is None or runner.closed:
        runner = LiveSessionRunner(session_id, user_id)
        _runners[session_id] = runner
        await runner.start()
    return runner


async def reap_idle_runners() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for sid, r in list(_runners.items()):
            busy = r.answer_task is not None and not r.answer_task.done()
            if not r.hub.sockets and not busy and now - r.last_activity > RUNNER_IDLE_TTL_S:
                await r.stop()
                _runners.pop(sid, None)


async def shutdown_runners() -> None:
    for r in list(_runners.values()):
        await r.stop()
    _runners.clear()


async def live_endpoint(websocket: WebSocket, session_id: str) -> None:
    ticket = websocket.query_params.get("ticket", "")
    user_id = ws_tickets.redeem(ticket, session_id)
    if not user_id:
        await websocket.close(code=4401, reason="invalid or expired ticket")
        return
    with session_factory()() as db:
        sess = db.get(InterviewSession, session_id)
        if sess is None or sess.user_id != user_id:
            await websocket.close(code=4404, reason="session not found")
            return
        if sess.status == "ended":
            await websocket.close(code=4409, reason="session has ended")
            return
    await websocket.accept()
    session_id_var.set(session_id)
    runner = await get_runner(session_id, user_id)
    hub = runner.hub
    try:
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            hello = json.loads(first)
        except (TimeoutError, json.JSONDecodeError):
            hello = {}
        last_seq = int(hello.get("last_seq") or 0) if hello.get("type") == "hello" else 0
        complete = True
        if last_seq:
            complete = await hub.replay(websocket, last_seq)
        snap = runner.snapshot()
        snap["resync_required"] = not complete
        snap["seq"] = hub.seq
        snap["session_id"] = session_id
        await websocket.send_text(json.dumps(snap, default=str))
        hub.sockets.add(websocket)
        if runner.mode in MOCK_MODES and not last_seq:
            await runner._mock_open()
        if not vector_store_status()["available"]:
            await hub.emit({"event": "degraded", "service": "vector_store",
                            "message": "Vector search is unavailable; retrieval is running in keyword-only mode."})
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await runner.on_audio(message["bytes"])
            elif message.get("text") is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    await runner.on_message(data)
    except WebSocketDisconnect:
        pass
    finally:
        hub.sockets.discard(websocket)
        runner.last_activity = time.time()
