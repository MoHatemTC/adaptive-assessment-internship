"""JSON-returning LLM calls via the LiteLLM proxy.

Reply handling carries two lessons measured against reasoning models, both of which
arrive as HTTP 200 with well-formed JSON and neither of which is detectable by asking
whether the call succeeded:

  * the answer is emitted into the reasoning channel and `content` comes back empty
  * the model echoes its own input payload back instead of answering

Reading `content or "{}"` turns the first into a valid-looking empty object, and a naive
JSON scan returns the question for the second. Both then reach the caller as a successful
reply, which is how an infrastructure quirk gets recorded as a model verdict.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services import observability
from cat_engine.engine.services.litellm_http import sync_http

logger = logging.getLogger(__name__)

TRANSPORT_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
)
REPLY_ATTEMPTS = 3
TRANSPORT_ATTEMPTS = 2

_client: OpenAI | None = None


class LLMUnavailable(RuntimeError):
    """No usable reply. Every caller must have a deterministic path."""


def _model_requires_default_temperature(model_name: str) -> bool:
    """Some routed models reject explicit temperature overrides."""
    normalized = (model_name or "").strip().lower()
    return normalized.endswith("gpt-5.6-sol")


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            timeout=settings.litellm_timeout_seconds,
            # Retries are owned here so they are logged and bounded. With the SDK's own
            # retry on top, budgets multiply and a failing step stalls for minutes with
            # nothing in the log until it finally gives up.
            max_retries=0,
            http_client=sync_http(),
        )
    return _client


def reset_client() -> None:
    global _client
    _client = None


def extract_json(raw: str, require: tuple[str, ...] = ()) -> dict[str, Any]:
    """Pull the answer object out of model output that may carry prose or a trace.

    Scans from the END backwards: in a reasoning trace the answer is last, and the trace
    usually restates the question first, so a forward scan returns the restatement.
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
    widest, widest_len = None, -1
    for position in range(len(raw) - 1, -1, -1):
        if raw[position] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(raw[position:])
        except json.JSONDecodeError:
            continue
        if require and acceptable(obj):
            return obj
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


def _one_call(
    system: str,
    user: str,
    temperature: float,
    require: tuple[str, ...],
    trace: dict[str, Any] | None = None,
) -> dict:
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
    response = get_client().chat.completions.create(
        **request_kwargs,
        **(trace or observability.generation(name="code_grader", stage="grade")),
    )
    message = response.choices[0].message
    body = (message.content or "").strip()
    if not body:
        body = (getattr(message, "reasoning_content", None) or "").strip()
    if not body:
        raise LLMUnavailable("empty reply (no content, no reasoning)")

    parsed = extract_json(body, require)
    missing = [key for key in require if key not in parsed]
    if missing:
        raise LLMUnavailable(f"reply lacks {missing}; got {sorted(parsed)[:6]}")

    usage = getattr(response, "usage", None)
    parsed["_usage"] = {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    return parsed


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    require: tuple[str, ...] = (),
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the proxy and return a parsed object, retrying what is worth retrying."""
    last: Exception | None = None
    for attempt in range(REPLY_ATTEMPTS):
        try:
            return _one_call(system, user, temperature, require, trace)
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
                reset_client()
                from cat_engine.engine.services.litellm_http import reset_http

                reset_http()
                continue
            # Never presented as a model verdict: the gateway did not answer, so nothing
            # was learned about the model.
            raise LLMUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
    raise LLMUnavailable(f"no usable reply after {REPLY_ATTEMPTS} attempts: {last}")
