"""OpenAI client for CAT selection/adaptation."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from openai import OpenAI

from config_env import load_runtime_config

load_runtime_config()

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0


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

    resp = client.chat.completions.create(
        model=get_model(),
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
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
            "Check OPENAI_API_KEY / network, or use engine-only selection."
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
