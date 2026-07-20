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
from engine_log import get_logger

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


def provider_name() -> str:
    """Short backend name for UI copy: "LiteLLM", "OpenAI", or "no LLM".

    Separate from provider_label() because that one includes the gateway URL, which is
    right for a diagnostics line and far too long for a status caption or a footer. The
    UI used to hardcode the string "OpenAI" in both places, so a kimi run announced
    itself as OpenAI on every screen.
    """
    if not get_api_key():
        return "no LLM"
    return "LiteLLM" if get_provider() == "litellm" else "OpenAI"


def provider_label() -> str:
    if not get_api_key():
        return "none"
    if get_provider() == "litellm":
        return f"LiteLLM ({get_base_url()})"
    return "OpenAI"


def missing_key_help() -> str:
    """Markdown telling the operator how to configure a backend. One source of truth.

    Every app.py said "No OPENAI_API_KEY found" and named only that variable, which is
    actively wrong guidance for a LiteLLM deployment: setting OPENAI_API_KEY would make
    the app talk to OpenAI, which is the opposite of what the reader wanted.
    """
    return (
        "**No LLM backend configured.** This app requires a working LLM — there is no "
        "engine-only mode.\n\n"
        "Set **one** of these, in `.env` locally or in Streamlit Cloud app secrets:\n\n"
        "- **LiteLLM gateway** — `LITELLM_API_KEY` **and** `LITELLM_BASE_URL` "
        "(optionally `LITELLM_MODEL`, default `" + DEFAULT_LITELLM_MODEL + "`)\n"
        "- **OpenAI direct** — `OPENAI_API_KEY` "
        "(optionally `OPENAI_MODEL`, default `" + DEFAULT_MODEL + "`)\n\n"
        "With both configured the LiteLLM gateway wins; force either with "
        "`LLM_PROVIDER=litellm|openai`."
    )


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


def chat_json(system: str, user: str, *, temperature: float = 0.0,
              require: tuple[str, ...] = (), attempts: int = 3) -> dict:
    """Call chat completions and parse a JSON object, retrying an unusable reply.

    `require` names the keys that make a reply an ANSWER rather than merely valid JSON.
    Two measured failure modes need it, both on kimi-k2.6 and neither detectable from
    JSON-validity alone:

      1. The model returns content='' with a short reasoning trace and simply never
         answers — ~17-25% of calls, independent of prompt.
      2. When it does that, the reasoning trace often contains the ECHOED INPUT payload.
         A JSON scan then happily returns `{"item":..., "previous_state":..., ...}` — a
         well-formed object that is the question, not the answer.

    Without `require`, case 2 reaches the controller as a successful reply with no
    theta_hat, which llm_math books as "no usable theta_hat" and approach 3 treats as an
    invalid step that ABORTS the competency. Measured on approach 3: 3 of 5 sessions
    aborted, and the branch scored as though the model could not run a CAT. It is
    transient flakiness, so it is retried rather than surfaced as a model verdict.

    Every attempt is metered — a retried call costs real tokens and the cost panel must
    say so. Retries are logged so a run cannot quietly cost 3x without anyone noticing.
    """
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

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return _one_call(client, system, user, temperature, require)
        except _UnusableReply as exc:
            last_error = exc
            if attempt < attempts - 1:
                get_logger().warning(
                    "LLM_RETRY | attempt %d/%d unusable (%s) — retrying",
                    attempt + 1, attempts, exc,
                )
    raise ValueError(f"LLM gave no usable reply in {attempts} attempts: {last_error}")


class _UnusableReply(Exception):
    """A 200 that is not an answer. Retryable; distinct from a transport failure."""


def _one_call(client, system: str, user: str, temperature: float,
              require: tuple[str, ...]) -> dict:
    global _last_usage
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
    # This is inside the retry loop on purpose — a retried step costs twice and the
    # cost panel must show both.
    usage = getattr(resp, "usage", None)
    if usage is not None:
        prompt_t = getattr(usage, "prompt_tokens", 0) or 0
        completion_t = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_t = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        with _usage_lock:
            _usage.add(prompt_t, completion_t, reasoning_t)
            _last_usage = Usage(1, prompt_t, completion_t, reasoning_t)

    message = resp.choices[0].message
    raw = (message.content or "").strip()

    # Reasoning models sometimes put the whole answer in the reasoning channel and
    # return content=''. Measured on kimi-k2.6 via the Sprints gateway: ~30% of CAT
    # selection calls come back with finish_reason='stop', content='', and a
    # reasoning_content ending in the complete, correct JSON object.
    #
    # This used to read `content or "{}"`, so an empty response became a valid-looking
    # empty object. Every controller then saw a well-formed reply with no selected_id /
    # no theta_hat and booked it as the model declining to answer: approach 1 fell back
    # to coded selection on 29% of steps, approach 2 to the coded EAP, and approach 3
    # invalidated and ABORTED the session. The model had answered correctly every time.
    # Scoring the branches without this fix would have measured a client-layer bug and
    # attributed it to the model.
    if not raw:
        raw = (getattr(message, "reasoning_content", None) or "").strip()

    if not raw:
        raise _UnusableReply("empty response (no content, no reasoning)")

    parsed = _extract_json(raw, require)
    missing = [k for k in require if k not in parsed]
    if missing:
        raise _UnusableReply(f"reply lacks required key(s) {missing}; got {sorted(parsed)[:6]}")
    return parsed


def _extract_json(raw: str, require: tuple[str, ...] = ()) -> dict:
    """Parse a JSON object out of model output that may have prose around it.

    Scans candidate `{` positions from the END backwards with raw_decode. Backwards
    because when the text is a reasoning trace the real answer is the last object in it,
    and the trace usually contains earlier partial or hypothetical objects that would
    win a forward scan. The old `re.search(r"\\{.*\\}", DOTALL)` spanned greedily from
    the first brace to the last, which on such a trace concatenates unrelated fragments
    into something that fails to parse — or, worse, parses into the wrong object.

    `require` disambiguates when the text holds several complete objects. The measured
    case: kimi echoes the INPUT payload into its reasoning trace, so a positional scan
    returns the question instead of the answer. Preferring an object that carries the
    answer's keys picks the right one; without it the caller receives a well-formed
    object that happens to be its own prompt.
    """
    def acceptable(obj) -> bool:
        return isinstance(obj, dict) and bool(obj) and all(k in obj for k in require)

    try:
        parsed = json.loads(raw)
        if acceptable(parsed):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    fallback: dict | None = None
    fallback_len = -1
    for pos in range(len(raw) - 1, -1, -1):
        if raw[pos] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(raw[pos:])
        except json.JSONDecodeError:
            continue
        if require and acceptable(obj):
            # Scanning backwards, so the first acceptable hit is the LAST such object in
            # the text — the answer, not the restatement of the question that preceded it.
            # Guarded on `require` being non-empty: with no keys to match, `acceptable`
            # is true for every dict and this would return whichever fragment the scan
            # reached first, which is the innermost trailing leaf.
            return obj
        # Keep the widest well-formed object as a fallback for callers with no `require`.
        # "First one found" would be the innermost nested fragment, since a backwards
        # scan reaches `{"correct": true}` before the object containing it — so a reply
        # this function used to hand back whole would arrive as one of its own leaves.
        if isinstance(obj, dict) and obj and end > fallback_len:
            fallback, fallback_len = obj, end
    if fallback is not None:
        return fallback

    # A truncated trailing object is common when the model runs out of budget mid-write.
    # Recover the last complete one rather than discarding the whole response.
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise _UnusableReply(f"no JSON object in reply: {raw[:160]!r}")


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
