"""Streaming speech-to-text providers.

- `browser` (default): the browser's Web Speech API performs recognition and
  streams partial/final transcripts over the session WebSocket. No audio
  reaches the server.
- `deepgram`: the browser streams 16 kHz mono PCM16 frames over the session
  WebSocket; the server relays them to Deepgram's streaming API (credentials
  stay server-side: STT_API_KEY) and turns its results into the same
  partial/final/VAD callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)

OnPartial = Callable[[str], Awaitable[None]]
OnFinal = Callable[[str, float | None], Awaitable[None]]
OnVad = Callable[[bool], Awaitable[None]]
OnError = Callable[[str], Awaitable[None]]


class STTUnavailable(RuntimeError):
    pass


class StreamingSTT(Protocol):
    name: str

    async def start(self, on_partial: OnPartial, on_final: OnFinal, on_vad: OnVad, on_error: OnError) -> None: ...

    async def send_audio(self, frame: bytes) -> None: ...

    async def stop(self) -> None: ...


class DeepgramSTT:
    name = "deepgram"
    URL = "wss://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str, sample_rate: int = 16000) -> None:
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._keepalive: asyncio.Task | None = None
        self._last_audio_end: float | None = None

    async def start(self, on_partial: OnPartial, on_final: OnFinal, on_vad: OnVad, on_error: OnError) -> None:
        import websockets

        params = (f"?model={self.model}&encoding=linear16&sample_rate={self.sample_rate}&channels=1"
                  "&interim_results=true&smart_format=true&punctuate=true&vad_events=true&endpointing=300")
        try:
            self._ws = await websockets.connect(
                self.URL + params, additional_headers={"Authorization": f"Token {self.api_key}"},
                open_timeout=8, ping_interval=20, max_size=2**22,
            )
        except Exception as exc:
            raise STTUnavailable(f"Could not connect to Deepgram: {exc}") from exc

        async def reader() -> None:
            try:
                async for raw in self._ws:  # type: ignore[union-attr]
                    msg = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "SpeechStarted":
                        await on_vad(True)
                    elif mtype == "UtteranceEnd":
                        await on_vad(False)
                    elif mtype == "Results":
                        alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
                        text = (alt.get("transcript") or "").strip()
                        if not text:
                            continue
                        if msg.get("is_final"):
                            start = float(msg.get("start", 0.0)) + float(msg.get("duration", 0.0))
                            latency = None
                            if self._last_audio_end is not None:
                                latency = max(0.0, (time.monotonic() - self._last_audio_end) * 1000)
                            await on_final(text, latency if start else None)
                            if msg.get("speech_final"):
                                await on_vad(False)
                        else:
                            await on_partial(text)
            except Exception as exc:  # connection dropped
                log.warning("deepgram stream ended", extra={"error": str(exc)})
                await on_error("Speech recognition connection lost.")

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(8)
                try:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))  # type: ignore[union-attr]
                except Exception:
                    return

        self._reader = asyncio.create_task(reader())
        self._keepalive = asyncio.create_task(keepalive())

    async def send_audio(self, frame: bytes) -> None:
        if self._ws is None:
            raise STTUnavailable("STT stream not started")
        self._last_audio_end = time.monotonic()
        await self._ws.send(frame)

    async def stop(self) -> None:
        for task in (self._keepalive,):
            if task:
                task.cancel()
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await asyncio.wait_for(self._reader, timeout=3) if self._reader else None
            except Exception:
                pass
            await self._ws.close()
        self._ws = None


def server_stt() -> StreamingSTT | None:
    """The configured server-side STT, or None when the browser performs STT."""
    s = get_settings()
    if s.stt_provider == "deepgram":
        if not s.stt_api_key:
            raise STTUnavailable("STT_PROVIDER=deepgram but STT_API_KEY is not set")
        return DeepgramSTT(s.stt_api_key, s.stt_model)
    return None


def stt_config() -> dict:
    s = get_settings()
    return {
        "provider": s.stt_provider,
        "server_side": s.stt_provider != "browser",
        "sample_rate": 16000,
        "endpoint_ms": s.turn_endpoint_ms,
        "available": s.stt_provider == "browser" or bool(s.stt_api_key),
    }
