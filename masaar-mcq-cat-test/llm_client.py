"""LLM client for CAT selection/adaptation.

Speaks the OpenAI chat-completions wire format, which is also what the Sprints
LiteLLM gateway serves, so one client covers both providers. Which one is used is
decided by `get_provider()` below, not by import order.
"""

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
DEFAULT_LITELLM_MODEL = "kimi-k2.6"
DEFAULT_TIMEOUT = 30.0
# Reasoning models spend a minute of wall clock on one CAT step; the OpenAI default of
# 30s guarantees a timeout that reads as "the model failed" when it only ran long.
DEFAULT_LITELLM_TIMEOUT = 180.0

# USD per 1M tokens. Kept as data rather than a hardcoded total so a model or price
# change is a one-line edit and the cost panel cannot quietly go stale.
# Cached input is billed at a discount, but these prompts are short and vary per call,
# so no cache hits are assumed.
#
# There are deliberately NO kimi entries. The Sprints LiteLLM gateway refuses
# /model/info to a non-admin virtual key (403), so this process cannot read the rate it
# is actually billed at, and a published vendor list price is not that rate — the
# gateway proxies, and a contracted rate is not a number to guess. `cost_usd()` returns
# None for an unpriced model and every caller already prints "unpriced model" rather
# than a fabricated dollar figure. Tokens are metered either way, and tokens are the
# quantity the approach comparison actually needs.
#
# To price a run, set both LLM_PRICE_INPUT_PER_1M and LLM_PRICE_OUTPUT_PER_1M; they
# override this table for whatever model is configured.
MODEL_PRICING_USD_PER_1M = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}


def _price_override() -> dict | None:
    """Explicit per-1M rates from the environment, or None if not both set."""
    try:
        pin = os.getenv("LLM_PRICE_INPUT_PER_1M", "").strip()
        pout = os.getenv("LLM_PRICE_OUTPUT_PER_1M", "").strip()
        if pin and pout:
            return {"input": float(pin), "output": float(pout)}
    except ValueError:
        pass
    return None


@dataclass
class Usage:
    """Token counter for one session. Cost is metered, not estimated from guesses."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # Subset of output_tokens the provider attributes to hidden reasoning. Billed as
    # output regardless, so it is NOT added again — this only splits out how much of
    # the output bill bought reasoning rather than answer.
    #
    # MEASURED CAVEAT: the Sprints LiteLLM gateway returns completion_tokens_details=None
    # for kimi, so this reads 0 there even though reasoning dominates. It is not that
    # kimi does no reasoning — a selection call returns ~40 tokens of JSON against
    # ~3,000 completion_tokens, and the response carries a populated `reasoning_content`.
    # The gateway simply does not break the number out. So on kimi, treat output_tokens
    # as "answer + unattributed reasoning" and do not read a 0 here as "no reasoning".
    reasoning_tokens: int = 0

    def add(self, prompt_tokens: int, completion_tokens: int,
            reasoning_tokens: int = 0) -> None:
        self.calls += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens
        self.reasoning_tokens += reasoning_tokens

    def cost_usd(self, model: str | None = None) -> float | None:
        price = _price_override() or MODEL_PRICING_USD_PER_1M.get(model or get_model())
        if price is None:
            return None
        return (self.input_tokens * price["input"] + self.output_tokens * price["output"]) / 1e6


_usage_lock = threading.Lock()
_usage = Usage()
_last_usage: Usage | None = None


def get_usage() -> Usage:
    with _usage_lock:
        return Usage(_usage.calls, _usage.input_tokens, _usage.output_tokens,
                     _usage.reasoning_tokens)


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
    return _price_override() or MODEL_PRICING_USD_PER_1M.get(model or get_model())


# Set by --model on the measurement harnesses. Process-wide on purpose: it has to reach
# the controller modules, which resolve the model through this function and take no
# model argument of their own.
_model_override: str | None = None


def set_model(model: str | None) -> None:
    """Override the configured model for this process. None restores env resolution."""
    global _model_override
    _model_override = (model or "").strip() or None
    reset_client_cache()


def get_provider() -> str:
    """Which backend this process talks to: "litellm" or "openai".

    LiteLLM wins when it is fully configured (key AND base URL), because configuring a
    gateway is an explicit act while OPENAI_API_KEY tends to linger in a .env from an
    earlier run. The previous rule preferred OpenAI unconditionally and treated the
    LiteLLM vars as a key-only fallback, so adding LITELLM_API_KEY to a .env that still
    had an OpenAI key changed precisely nothing and runs silently continued to bill
    gpt-4o-mini while the operator believed they were measuring kimi. Set LLM_PROVIDER
    to force either one.
    """
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    if forced in ("litellm", "openai"):
        return forced
    if os.getenv("LITELLM_API_KEY", "").strip() and get_base_url():
        return "litellm"
    return "openai"


def get_base_url() -> str | None:
    """Gateway base URL, normalised to include the /v1 the OpenAI SDK expects."""
    base = (
        os.getenv("LITELLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )
    if not base:
        return None
    base = base.rstrip("/")
    # The SDK appends "/chat/completions", not "/v1/chat/completions". A gateway root
    # pasted without /v1 therefore 404s on every call, which surfaces as a generic API
    # error and looks like a broken model rather than a missing path segment.
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def get_api_key() -> str:
    if get_provider() == "litellm":
        return os.getenv("LITELLM_API_KEY", "").strip()
    return (
        os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("LITELLM_API_KEY", "").strip()
    )


def get_model() -> str:
    if _model_override:
        return _model_override
    if get_provider() == "litellm":
        return os.getenv("LITELLM_MODEL", "").strip() or DEFAULT_LITELLM_MODEL
    return (
        os.getenv("OPENAI_MODEL", "").strip()
        or os.getenv("LITELLM_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def get_timeout() -> float:
    litellm = get_provider() == "litellm"
    default = DEFAULT_LITELLM_TIMEOUT if litellm else DEFAULT_TIMEOUT
    raw = (
        (os.getenv("LITELLM_TIMEOUT") if litellm else os.getenv("OPENAI_TIMEOUT"))
        or os.getenv("OPENAI_TIMEOUT")
        or os.getenv("LITELLM_TIMEOUT")
        or str(default)
    )
    try:
        return float(raw)
    except ValueError:
        return default


def provider_label() -> str:
    if not get_api_key():
        return "none"
    if get_provider() == "litellm":
        return f"LiteLLM ({get_base_url()})"
    return "OpenAI"


@lru_cache(maxsize=4)
def _build_client(api_key: str, base: str | None, timeout: float) -> OpenAI:
    """Cached on its inputs, so changing model/provider mid-process rebuilds the client.

    The previous version was `lru_cache(maxsize=1)` on a zero-argument function that
    read the environment inside its own body — so the first call froze the base URL for
    the life of the process and a later provider switch kept talking to the old host.
    """
    kwargs: dict = {"api_key": api_key, "timeout": timeout, "max_retries": 1}
    if base:
        kwargs["base_url"] = base
    return OpenAI(**kwargs)


def get_client() -> OpenAI | None:
    api_key = get_api_key()
    if not api_key:
        return None
    return _build_client(api_key, get_base_url(), get_timeout())


def reset_client_cache() -> None:
    _build_client.cache_clear()


def llm_configured() -> bool:
    return bool(get_api_key())


def chat_json(system: str, user: str, *, temperature: float = 0.0) -> dict:
    """Call OpenAI chat completions and parse a JSON object."""
    client = get_client()
    if client is None:
        raise RuntimeError(
            "No LLM key configured — set LITELLM_API_KEY + LITELLM_BASE_URL, "
            "or OPENAI_API_KEY"
        )

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
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_t = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        with _usage_lock:
            _usage.add(prompt_t, completion_t, reasoning_t)
            _last_usage = Usage(1, prompt_t, completion_t, reasoning_t)

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
        return False, (
            "No LLM key set — add LITELLM_API_KEY + LITELLM_BASE_URL "
            "(or OPENAI_API_KEY) to .env"
        )

    client = get_client()
    if client is None:
        return False, "Failed to create LLM client"

    try:
        models = client.models.list()
        ids = [m.id for m in models.data[:8]]
        configured = get_model()
        preview = ", ".join(ids) if ids else "(empty list)"
        # A model name that is not served is the single most common way a run silently
        # fails, and the models list is the only place it is cheap to catch.
        served = {m.id for m in models.data}
        if served and configured not in served:
            return False, (
                f"{provider_label()} reachable, but model={configured!r} is not served. "
                f"Available: {preview}"
            )
        return True, (
            f"{provider_label()} OK · model={configured} · sample: {preview}"
        )
    except Exception as exc:
        return False, (
            f"LLM probe failed ({type(exc).__name__}: {exc}). "
            f"Check credentials / network for {provider_label()}. "
            "There is no engine-only mode."
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
