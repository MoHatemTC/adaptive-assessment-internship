"""Resilience wrapper for LLM calls routed through the LiteLLM proxy.

The proxy/upstream (Vertex/Gemini) occasionally returns transient failures —
timeouts, 429s, 5xx, and a flaky 401 whose message is "Client is not connected
to the query engine, you must call connect() before attempting to query data."
A single such blip was hard-failing the learner onboarding (CV parse) and the
admin "Generate with AI" action. This helper retries those transient failures
with exponential backoff + jitter, while surfacing genuine errors immediately.
"""

import asyncio
import random

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    RateLimitError,
    InternalServerError,
)

# HTTP statuses that are worth retrying (transient on the gateway/upstream side).
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# Substrings that mark an otherwise-permanent status (e.g. 401) as actually a
# transient upstream connectivity blip we should retry.
_TRANSIENT_HINTS = (
    "not connected to the query engine",
    "must call connect()",
    "temporarily unavailable",
    "overloaded",
    "please try again",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        if status in _TRANSIENT_STATUS:
            return True
        msg = (str(getattr(exc, "message", "")) or str(exc)).lower()
        return any(hint in msg for hint in _TRANSIENT_HINTS)
    return False


async def chat_completion(
    client,
    *,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    **kwargs,
):
    """Drop-in replacement for `await client.chat.completions.create(**kwargs)`
    with retry/backoff on transient failures. Re-raises the last error once the
    retry budget is exhausted, and re-raises non-transient errors immediately."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — we classify below
            if not _is_transient(exc) or attempt == retries:
                raise
            last_exc = exc
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.25)  # jitter to avoid thundering herd
            await asyncio.sleep(delay)
    # Unreachable, but keeps type-checkers happy.
    raise last_exc  # type: ignore[misc]
