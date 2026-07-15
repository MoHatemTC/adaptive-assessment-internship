"""Load local .env and Streamlit Cloud secrets into process env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

KNOWN_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_TIMEOUT",
    "OPENAI_BASE_URL",
    "LITELLM_API_KEY",
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


def load_runtime_config() -> None:
    """Load config from local .env and top-level Streamlit secrets.

    Streamlit Community Cloud stores secrets in `st.secrets`; root-level TOML
    values are not always available through `os.getenv()` early enough for
    imported clients. This bridge makes the app work in both local and cloud
    runtimes without committing secrets.
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
        try:
            value = secrets.get(key)
        except Exception:
            continue
        if value is not None:
            os.environ[key] = str(value)
