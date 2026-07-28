"""Load local .env and Streamlit Cloud secrets into process env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

KNOWN_KEYS = (
    "LLM_PROVIDER",
    "LLM_PRICE_INPUT_PER_1M",
    "LLM_PRICE_OUTPUT_PER_1M",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_TIMEOUT",
    "OPENAI_BASE_URL",
    "LITELLM_API_KEY",
    "LITELLM_BASE_URL",
    "LITELLM_MODEL",
    "LITELLM_TIMEOUT",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_BASE_URL",
    "CAT_APPROACH_ID",
    "CAT_MATH_ACTOR",
    "CAT_SELECTION_ACTOR",
    "CAT_TRACE_NAME",
)

SECRET_SECTIONS = (
    "LLM",
    "llm",
    "OpenAI",
    "openai",
    # Streamlit Cloud secrets are TOML, and grouping LiteLLM settings under a
    # [litellm] table is the obvious thing to write. Without these names the whole
    # section is skipped, the app silently finds no gateway, and — if an OpenAI key
    # is also present — quietly runs on OpenAI instead.
    "LiteLLM",
    "litellm",
    "LITELLM",
    "Fuse",
    "fuse",
    "Langfuse",
    "langfuse",
    "CAT",
    "cat",
)


def _secret_get(container, key: str):
    try:
        return container.get(key)
    except Exception:
        try:
            return container[key]
        except Exception:
            return None


def _find_secret(secrets, key: str):
    value = _secret_get(secrets, key)
    if value is not None:
        return value

    for section_name in SECRET_SECTIONS:
        section = _secret_get(secrets, section_name)
        if section is None:
            continue
        value = _secret_get(section, key)
        if value is not None:
            return value
    return None


def load_runtime_config() -> None:
    """Load config from local .env and Streamlit secrets.

    Supports both top-level TOML keys and grouped secrets such as `[LLM]`,
    `[Fuse]`, `[Langfuse]`, or `[CAT]`.
    """
    load_dotenv(ENV_PATH, override=True)

    try:
        import streamlit as st

        secrets = st.secrets
    except Exception:
        return

    for key in KNOWN_KEYS:
        if os.getenv(key):
            continue
        value = _find_secret(secrets, key)
        if value is not None:
            os.environ[key] = str(value)
