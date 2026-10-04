"""Opt-in interview recordings.

Raw audio is never stored by default. A recording is written only when the
server allows it (STORE_RAW_AUDIO=true) AND the user enabled "store recordings"
in Settings, and only for server-side STT sessions (browser STT never sends
audio to the server). Files live in RECORDINGS_DIR, one WAV per session, and
can be downloaded or deleted from the interview page / API.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

from app.core.config import get_settings

SAMPLE_RATE = 16000


def recordings_dir() -> Path:
    d = Path(get_settings().recordings_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths(session_id: str) -> tuple[Path, Path]:
    safe = "".join(c for c in session_id if c.isalnum())
    base = recordings_dir()
    return base / f"{safe}.pcm", base / f"{safe}.wav"


def recording_allowed(user_preferences: dict | None) -> bool:
    return get_settings().store_raw_audio and bool((user_preferences or {}).get("store_recordings"))


class RecordingWriter:
    def __init__(self, session_id: str) -> None:
        self.pcm_path, self.wav_path = _paths(session_id)
        self._fh = open(self.pcm_path, "ab")  # noqa: SIM115 - long-lived handle, closed in finalize()
        try:
            os.chmod(self.pcm_path, 0o600)
        except OSError:
            pass

    def write(self, frame: bytes) -> None:
        self._fh.write(frame)

    def finalize(self) -> Path | None:
        self._fh.close()
        if not self.pcm_path.exists() or self.pcm_path.stat().st_size == 0:
            self.pcm_path.unlink(missing_ok=True)
            return None
        data = self.pcm_path.read_bytes()
        header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack(
            "<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(data))
        # append to an existing WAV from an earlier part of the session
        if self.wav_path.exists():
            old = self.wav_path.read_bytes()[44:]
            data = old + data
            header = header[:4] + struct.pack("<I", 36 + len(data)) + header[8:40] + struct.pack("<I", len(data))
        self.wav_path.write_bytes(header + data)
        self.pcm_path.unlink(missing_ok=True)
        return self.wav_path


def recording_path(session_id: str) -> Path | None:
    _, wav = _paths(session_id)
    return wav if wav.exists() else None


def delete_recording(session_id: str) -> bool:
    pcm, wav = _paths(session_id)
    existed = wav.exists() or pcm.exists()
    pcm.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    return existed
