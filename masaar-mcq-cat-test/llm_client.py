"""OpenAI client for CAT selection/adaptation."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache

from openai import OpenAI

from config_env import load_runtime_config

load_runtime_config()

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0

# USD per 1M tokens. Kept as data rather than a hardcoded total so a model or price
# change is a one-line edit and the cost panel cannot quietly go stale.
# Cached input is billed at a discount, but these prompts are short and vary per call,
# so no cache hits are assumed.
MODEL_PRICING_USD_PER_1M = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}


@dataclass
class Usage:
    """Token counter for one session. Cost is metered, not estimated from guesses."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens

    def cost_usd(self, model: str | None = None) -> float | None:
        price = MODEL_PRICING_USD_PER_1M.get(model or get_model())
        if price is None:
            return None
        return (self.input_tokens * price["input"] + self.output_tokens * price["output"]) / 1e6


_usage_lock = threading.Lock()
_usage = Usage()
_last_usage: Usage | None = None


def get_usage() -> Usage:
    with _usage_lock:
        return Usage(_usage.calls, _usage.input_tokens, _usage.output_tokens)


def consume_last_usage() -> Usage | None:
    """Usage of the most recent call, consumed on read.

    Consume-once on purpose. A tracer attaches this to the generation it just made, and
    several call sites emit a second observation right afterwards (a guard rejection, an
    error event). If this kept returning the same tokens, those follow-ups would each be
    billed again in Langfuse and the dashboard would overstate cost. Returning None the
    second time means an event with no LLM call of its own reports no usage, which is
    the truth.

    Cleared at the start of every chat_json, so a call that raises leaves None behind
    rather than the previous call's tokens.
    """
    global _last_usage
    with _usage_lock:
        last = _last_usage
        _last_usage = None
        return last


def reset_usage() -> None:
    global _usage, _last_usage
    with _usage_lock:
        _usage = Usage()
        _last_usage = None


def model_pricing(model: str | None = None) -> dict | None:
    return MODEL_PRICING_USD_PER_1M.get(model or get_model())


def get_api_key() -> str:
    # Prefer OpenAI; keep LiteLLM vars as legacy fallback.
    return (
        os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("LITELLM_API_KEY", "").strip()
    )


def get_model() -> str:
    return (
        os.getenv("OPENAI_MODEL", "").strip()
        or os.getenv("LITELLM_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def get_timeout() -> float:
    raw = os.getenv("OPENAI_TIMEOUT") or os.getenv("LITELLM_TIMEOUT") or str(DEFAULT_TIMEOUT)
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


def provider_label() -> str:
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "OpenAI"
    if os.getenv("LITELLM_API_KEY", "").strip():
        return "LiteLLM (legacy)"
    return "none"


@lru_cache(maxsize=1)
def get_client() -> OpenAI | None:
    api_key = get_api_key()
    if not api_key:
        return None
    # Official OpenAI API — no custom base_url unless OPENAI_BASE_URL is set.
    base = os.getenv("OPENAI_BASE_URL", "").strip() or None
    kwargs: dict = {
        "api_key": api_key,
        "timeout": get_timeout(),
        "max_retries": 1,
    }
    if base:
        kwargs["base_url"] = base
    return OpenAI(**kwargs)


def reset_client_cache() -> None:
    get_client.cache_clear()


def llm_configured() -> bool:
    return bool(get_api_key())


def chat_json(system: str, user: str, *, temperature: float = 0.0) -> dict:
    """Call OpenAI chat completions and parse a JSON object."""
    client = get_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Clear first: if this call raises, no stale tokens are left for an error trace to
    # pick up and mis-bill.
    global _last_usage
    with _usage_lock:
        _last_usage = None

    resp = client.chat.completions.create(
        model=get_model(),
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    # Meter every call. Counted before parsing: a response that fails to parse was
    # still billed, and a cost readout that only counts successes understates the bill.
    usage = getattr(resp, "usage", None)
    if usage is not None:
        prompt_t = getattr(usage, "prompt_tokens", 0) or 0
        completion_t = getattr(usage, "completion_tokens", 0) or 0
        with _usage_lock:
            _usage.add(prompt_t, completion_t)
            _last_usage = Usage(1, prompt_t, completion_t)

    raw = (resp.choices[0].message.content or "{}").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"LLM did not return JSON: {raw[:200]!r}")


def probe_gateway() -> tuple[bool, str]:
    """List models via OpenAI SDK — confirms key + network."""
    if not llm_configured():
        return False, "OPENAI_API_KEY not set — add it to .env"

    client = get_client()
    if client is None:
        return False, "Failed to create OpenAI client"

    try:
        models = client.models.list()
        ids = [m.id for m in models.data[:8]]
        configured = get_model()
        preview = ", ".join(ids) if ids else "(empty list)"
        return True, (
            f"{provider_label()} OK · model={configured} · sample: {preview}"
        )
    except Exception as exc:
        return False, (
            f"OpenAI probe failed ({type(exc).__name__}: {exc}). "
            "Check OPENAI_API_KEY / network. There is no engine-only mode."
        )


def test_connection() -> tuple[bool, str]:
    """Full chat round-trip — use only on explicit button click."""
    ok, msg = probe_gateway()
    if not ok:
        return ok, msg
    try:
        out = chat_json(
            'Reply with JSON only: {"ok": true, "message": "pong"}',
            "ping",
        )
        if out.get("ok"):
            return True, f"Chat OK ({get_model()})"
        return True, f"Chat responded ({get_model()}): {out}"
    except Exception as exc:
        return False, f"Models OK but chat failed: {exc}"
