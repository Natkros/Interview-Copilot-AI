"""Application settings.

Every external credential is read from the environment (or a `.env` file) here
and nowhere else. See `.env.example` at the repository root for documentation
of each variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "InterviewOS AI"
    environment: Literal["development", "test", "production"] = "development"

    # --- Persistence -------------------------------------------------------
    database_url: str = "sqlite:///./interviewos.db"
    # ":memory:" runs Qdrant in-process (tests / single-node dev),
    # a filesystem path runs embedded local mode, an http(s) URL uses a server.
    qdrant_url: str = "./qdrant_data"
    qdrant_api_key: str | None = None
    redis_url: str | None = None

    # --- AI providers ------------------------------------------------------
    # "auto" -> anthropic when LLM_API_KEY is set, else the deterministic
    # offline provider (extractive, grounded, no network).
    llm_provider: Literal["auto", "anthropic", "offline"] = "auto"
    llm_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    # Answer generation is latency-critical: low effort keeps first-token fast.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_timeout_s: float = 30.0
    llm_judge_enabled: bool = False  # optional LLM-as-judge in the evaluator

    embedding_provider: Literal["hash", "openai"] = "hash"
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dim: int = 384

    # "browser" -> client-side Web Speech API streams transcripts over the WS.
    # "deepgram" -> client streams PCM audio, server relays to Deepgram.
    stt_provider: Literal["browser", "deepgram"] = "browser"
    stt_api_key: str | None = None
    stt_model: str = "nova-3"

    # --- Turn detection ----------------------------------------------------
    turn_endpoint_ms: int = 1200  # silence after a final segment before answering
    turn_min_words: int = 2

    # --- Security ----------------------------------------------------------
    auth_secret: str = Field(default="dev-insecure-change-me-32-bytes-minimum!!")
    access_token_ttl_minutes: int = 60 * 12
    ws_ticket_ttl_seconds: int = 60
    cookie_secure: bool = False
    cookie_name: str = "interviewos_session"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 10
    max_upload_mb: int = 8
    upload_tmp_dir: str | None = None

    # --- Privacy -----------------------------------------------------------
    store_raw_audio: bool = False  # server-wide permission; users must also opt in
    recordings_dir: str = "./recordings"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json

                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider == "auto":
            return "anthropic" if self.llm_api_key else "offline"
        return self.llm_provider

    def validate_for_production(self) -> None:
        if self.environment != "production":
            return
        problems = []
        if self.auth_secret.startswith("dev-insecure") or len(self.auth_secret) < 32:
            problems.append("AUTH_SECRET must be a random value of at least 32 characters")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point at PostgreSQL in production")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
