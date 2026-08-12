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
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    OpenAIError,
    RateLimitError,
)

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services import observability
from cat_engine.engine.services.litellm_http import async_http

logger = logging.getLogger(__name__)

# The gateway never produced an answer. Retryable, and — the reason this is a distinct
# category — never evidence about the model.
TRANSPORT_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
)

# A malformed reply comes back fast, so retrying it is cheap. A timeout costs the entire
# timeout, so three of those is a multi-minute stall with a candidate waiting.
REPLY_ATTEMPTS = 3
TRANSPORT_ATTEMPTS = 2

_client: AsyncOpenAI | None = None


class LLMUnavailable(RuntimeError):
    """No usable reply, for any reason. Callers fall back to the deterministic path."""


def _model_requires_default_temperature(model_name: str) -> bool:
    """Some routed models reject explicit temperature overrides."""
    normalized = (model_name or "").strip().lower()
    return normalized.endswith("gpt-5.6-sol")


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        # Before the client exists, so instrumentation is in place for its first call.
        # (It patches the resource classes rather than the instance, so the order is
        # belt-and-braces — but a reader should not have to know that to trust this.)
        observability.configure()
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            timeout=settings.litellm_timeout_seconds,
            # Retries are owned here, not by the SDK. With both, the budgets multiply: a
            # single failing step becomes SDK-retries x our-retries silent attempts, and
            # nothing reaches the log until the whole thing finally gives up.
            max_retries=0,
            http_client=async_http(),
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
        return (
            isinstance(value, dict) and bool(value) and all(k in value for k in require)
        )

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


async def _one_call(
    system: str,
    user: str,
    temperature: float,
    require: tuple[str, ...],
    trace: dict[str, Any] | None = None,
):
    request_kwargs: dict[str, Any] = {
        "model": settings.litellm_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # gpt-5.6-sol (via the current LiteLLM route) rejects temperature=0 and requires
    # provider default behavior; omit the field completely in that case.
    if not _model_requires_default_temperature(settings.litellm_model):
        request_kwargs["temperature"] = temperature

    response = await _llm().chat.completions.create(
        # Empty unless Langfuse is configured, and stripped by its argument extractor
        # before the request is built either way — the gateway never sees these.
        **request_kwargs,
        **(trace or {}),
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
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the proxy and return a parsed JSON object, retrying what is worth retrying.

    Raises `LLMUnavailable` when no usable reply arrives — and `LLMUnavailable` is the
    ONLY exception that leaves here. Callers all have a deterministic path and catch
    exactly that; anything else escaping propagates out of a background queue fill and
    ends a candidate's assessment over a monitoring-grade problem.

    `user` must already be text. A caller passing the payload dict it built is the
    measured bug this guard exists for: the OpenAI SDK forwards a dict as the message
    content, and what comes back is a gateway 400 reading "'str' object has no attribute
    'get'" — an error about the proxy's parser, pointing nowhere near the call site, on a
    path whose whole design is to survive the model failing.
    """
    if not isinstance(user, str):
        raise TypeError(
            f"chat_json expects text, got {type(user).__name__} — serialise the payload "
            "with json.dumps() at the call site"
        )

    last: Exception | None = None
    for attempt in range(REPLY_ATTEMPTS):
        try:
            return await _one_call(system, user, temperature, require, trace)
        except LLMUnavailable as exc:
            last = exc
            if attempt < REPLY_ATTEMPTS - 1:
                logger.warning("llm reply unusable (%s), retrying", exc)
        except TRANSPORT_ERRORS as exc:
            last = exc
            if attempt < TRANSPORT_ATTEMPTS - 1:
                logger.warning(
                    "llm transport failure (%s), retrying", type(exc).__name__
                )
                # Drop pooled clients: instantaneous Connection errors are usually a
                # dead keepalive to the LiteLLM proxy; retrying on the same socket
                # just records another Langfuse ERROR span.
                reset_client()
                from cat_engine.engine.services.litellm_http import reset_http

                reset_http()
                continue
            raise LLMUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
        except APIStatusError as exc:
            # The gateway answered, and refused. Not retryable — a rejected request is
            # rejected identically the second time — but emphatically not fatal either:
            # a bad model name, a revoked key, an unsupported response_format or a
            # payload the proxy would not parse are all configuration faults, and the
            # engine has a correct next question regardless of what the model thinks.
            logger.error(
                "llm rejected the request (HTTP %s): %s",
                exc.status_code,
                str(exc)[:300],
            )
            raise LLMUnavailable(
                f"gateway rejected the request: HTTP {exc.status_code}"
            ) from exc
        except OpenAIError as exc:
            # Anything else the SDK raises — a malformed response it could not parse, an
            # argument it would not accept. Same reasoning: degrade, do not escape.
            logger.error(
                "llm client error (%s): %s", type(exc).__name__, str(exc)[:300]
            )
            raise LLMUnavailable(f"llm client error: {type(exc).__name__}") from exc
    raise LLMUnavailable(f"no usable reply after {REPLY_ATTEMPTS} attempts: {last}")
