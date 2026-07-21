"""Async JSON-returning LLM calls, via the LiteLLM proxy.

Thin on purpose: the engine's correctness never depends on this module, only its
explanations do. What is not thin is the reply handling, because two failure modes were
measured against a reasoning model and both arrive as HTTP 200 with well-formed JSON —
neither is detectable by asking "did the call succeed".
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.config.settings import settings

logger = logging.getLogger(__name__)

# The gateway never produced an answer. Retryable, and — the reason this is a distinct
# category — never evidence about the model.
TRANSPORT_ERRORS = (APITimeoutError, APIConnectionError, InternalServerError, RateLimitError)

# A malformed reply comes back fast, so retrying it is cheap. A timeout costs the entire
# timeout, so three of those is a multi-minute stall with a candidate waiting.
REPLY_ATTEMPTS = 3
TRANSPORT_ATTEMPTS = 2

_client: AsyncOpenAI | None = None


class LLMUnavailable(RuntimeError):
    """No usable reply, for any reason. Callers fall back to the deterministic path."""


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            timeout=settings.litellm_timeout_seconds,
            # Retries are owned here, not by the SDK. With both, the budgets multiply: a
            # single failing step becomes SDK-retries x our-retries silent attempts, and
            # nothing reaches the log until the whole thing finally gives up.
            max_retries=0,
        )
    return _client


def reset_client() -> None:
    """Drop the cached client. For tests, and after a settings change."""
    global _client
    _client = None


def extract_json(raw: str, require: tuple[str, ...] = ()) -> dict[str, Any]:
    """Pull a JSON object out of model output that may have prose around it.

    Scans candidate positions from the END backwards. When the text is a reasoning trace,
    the answer is the last object in it, and the trace usually restates the question
    first — a forward scan returns the restatement. `require` names the keys that make an
    object the answer, which is what disambiguates when several are present.
    """

    def acceptable(value: Any) -> bool:
        return isinstance(value, dict) and bool(value) and all(k in value for k in require)

    try:
        parsed = json.loads(raw)
        if acceptable(parsed):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    widest: dict[str, Any] | None = None
    widest_len = -1
    for position in range(len(raw) - 1, -1, -1):
        if raw[position] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(raw[position:])
        except json.JSONDecodeError:
            continue
        if require and acceptable(obj):
            return obj
        # Widest, not first: scanning backwards reaches the innermost nested fragment
        # before the object containing it, so "first found" returns a leaf of the reply.
        if isinstance(obj, dict) and obj and end > widest_len:
            widest, widest_len = obj, end
    if widest is not None:
        return widest

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise LLMUnavailable(f"no JSON object in reply: {raw[:160]!r}")


async def _one_call(system: str, user: str, temperature: float, require: tuple[str, ...]):
    response = await _llm().chat.completions.create(
        model=settings.litellm_model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    message = response.choices[0].message
    body = (message.content or "").strip()

    # Reasoning models sometimes emit the whole answer into the reasoning channel and
    # return an empty content. Reading `content or "{}"` turns that into a valid-looking
    # empty object, which every caller then reads as the model declining to answer —
    # measured at roughly a third of selection calls, every one of which had in fact
    # answered correctly.
    if not body:
        body = (getattr(message, "reasoning_content", None) or "").strip()
    if not body:
        raise LLMUnavailable("empty reply (no content, no reasoning)")

    parsed = extract_json(body, require)
    missing = [key for key in require if key not in parsed]
    if missing:
        # A reply can be perfectly well-formed JSON and still not be an answer: the
        # measured case is a model echoing its own input payload back. Without this check
        # that reaches the caller as a successful reply carrying none of the fields it
        # needs.
        raise LLMUnavailable(f"reply lacks {missing}; got {sorted(parsed)[:6]}")
    return parsed


async def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    require: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Call the proxy and return a parsed JSON object, retrying what is worth retrying.

    Raises `LLMUnavailable` when no usable reply arrives. Callers must have a
    deterministic path — this module is never the only way to make a decision.
    """
    last: Exception | None = None
    for attempt in range(REPLY_ATTEMPTS):
        try:
            return await _one_call(system, user, temperature, require)
        except LLMUnavailable as exc:
            last = exc
            if attempt < REPLY_ATTEMPTS - 1:
                logger.warning("llm reply unusable (%s), retrying", exc)
        except TRANSPORT_ERRORS as exc:
            last = exc
            if attempt < TRANSPORT_ATTEMPTS - 1:
                logger.warning("llm transport failure (%s), retrying", type(exc).__name__)
                continue
            raise LLMUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
    raise LLMUnavailable(f"no usable reply after {REPLY_ATTEMPTS} attempts: {last}")
