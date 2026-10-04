"""LLM provider abstraction.

`AnthropicProvider` streams from the Claude Messages API (configure
LLM_PROVIDER=anthropic or just set LLM_API_KEY; model via LLM_MODEL).
When no key is configured the system uses the deterministic offline
composer in `app.agents.answer_agent` instead of a model call.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    retryable = False


class LLMUnavailable(LLMError):
    retryable = True


class LLMTimeout(LLMError):
    retryable = True


class LLMRateLimited(LLMError):
    retryable = True


class LLMRefused(LLMError):
    pass


@dataclass
class StreamResult:
    text: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    stop_reason: str | None = None
    meta: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def stream(self, system: str, user: str, max_tokens: int, result: StreamResult) -> AsyncIterator[str]: ...

    async def complete(self, system: str, user: str, max_tokens: int) -> StreamResult: ...


class AnthropicProvider:
    """Claude via the official SDK. The system prompt is static and marked for
    prompt caching; all per-question content goes in the user message."""

    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        self.model = s.llm_model
        self.effort = s.llm_effort
        self.client = anthropic.AsyncAnthropic(api_key=s.llm_api_key, timeout=s.llm_timeout_s, max_retries=1)

    def _params(self, system: str, user: str, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self.effort},
            # Server-side refusal fallback: if the primary model declines, the
            # API re-runs the request on a fallback model within the same call.
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }

    def _map_error(self, exc: Exception) -> LLMError:
        a = self._anthropic
        if isinstance(exc, a.RateLimitError):
            return LLMRateLimited("LLM rate limit reached")
        if isinstance(exc, a.APITimeoutError):
            return LLMTimeout("LLM request timed out")
        if isinstance(exc, a.APIConnectionError):
            return LLMUnavailable("LLM service unreachable")
        if isinstance(exc, a.AuthenticationError):
            return LLMError("LLM API key rejected - check LLM_API_KEY")
        if isinstance(exc, a.BadRequestError):
            return LLMError(f"LLM rejected the request: {getattr(exc, 'message', exc)}")
        if isinstance(exc, a.APIStatusError):
            err = LLMUnavailable(f"LLM service error {exc.status_code}")
            err.retryable = exc.status_code >= 500
            return err
        return LLMError(str(exc))

    async def stream(self, system: str, user: str, max_tokens: int, result: StreamResult) -> AsyncIterator[str]:
        try:
            async with self.client.beta.messages.stream(**self._params(system, user, max_tokens)) as stream:
                async for text in stream.text_stream:
                    result.text += text
                    yield text
                final = await stream.get_final_message()
        except self._anthropic.APIError as exc:
            raise self._map_error(exc) from exc
        result.model = final.model
        result.stop_reason = final.stop_reason
        usage = final.usage
        result.tokens_in = usage.input_tokens or 0
        result.tokens_out = usage.output_tokens or 0
        result.cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if final.stop_reason == "refusal":
            raise LLMRefused("The model declined to answer this question.")

    async def complete(self, system: str, user: str, max_tokens: int) -> StreamResult:
        result = StreamResult()
        async for _ in self.stream(system, user, max_tokens, result):
            pass
        return result


_provider: LLMProvider | None = None
_injected = False


def get_llm() -> LLMProvider | None:
    """The configured model provider, or None for offline (deterministic) mode."""
    global _provider
    if _injected:
        return _provider
    s = get_settings()
    if s.resolved_llm_provider != "anthropic":
        return None
    if _provider is None:
        _provider = AnthropicProvider()
    return _provider


def set_llm(provider: LLMProvider | None) -> None:
    """Test hook: inject a fake provider (None restores configuration-driven selection)."""
    global _provider, _injected
    _provider = provider
    _injected = provider is not None
