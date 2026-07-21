"""Configuration, including which approach this checkout implements.

`CODE_CAT_APPROACH` is what makes the three branches comparable: it names the variant so
every audit record, every measurement run and the Streamlit header agree on what was
actually executed. A run whose results cannot be attributed to a variant is not evidence.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_streamlit_secrets() -> None:
    """Copy Streamlit Cloud secrets into the environment before Settings reads it.

    Streamlit Cloud has no .env and does NOT export st.secrets to os.environ, so a
    settings object that reads only the environment deploys with no E2B key and no LLM
    credentials — the app comes up looking healthy and fails on the first submission.

    Runs before Settings() is constructed, is a no-op outside Streamlit, and never
    overwrites a value already set so a local .env still wins during development.
    Supports both flat keys and a [cat] / [llm] table, since grouping is the obvious
    thing to write in a secrets TOML.
    """
    try:
        import streamlit as st

        secrets = st.secrets
    except Exception:
        return

    def put(key: str, value) -> None:
        if value is not None and not os.environ.get(key.upper()):
            os.environ[key.upper()] = str(value)

    for key in (
        "E2B_API_KEY", "LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL",
        "LITELLM_TIMEOUT_SECONDS", "CODE_CAT_APPROACH", "CODE_CAT_RUBRIC",
    ):
        try:
            put(key, secrets.get(key))
        except Exception:
            pass
        for section in ("cat", "CAT", "llm", "LLM"):
            try:
                table = secrets.get(section)
                if table is not None:
                    put(key, table.get(key))
            except Exception:
                pass

# A: objective only        tests + static analysis, deterministic rubric, no LLM scoring
# B: test-anchored LLM     LLM interprets; functional correctness anchored to tests
# C: LLM-led               LLM scores every criterion; only hard contradictions blocked
Approach = Literal["A", "B", "C"]

# Rubric strictness. The second factor in the study: loose names the criteria, mid adds
# evaluation criteria and quality checks, tight decomposes each criterion into binary
# points with worked examples and adds negative mistake patterns.
#
# Only reaches the model. Approach A never calls it, so A's three rubric cells must come
# out identical — which makes them the control that proves any rubric effect elsewhere is
# real rather than run-to-run noise.
Rubric = Literal["loose", "mid", "tight"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    code_cat_approach: Approach = "B"
    code_cat_rubric: Rubric = "mid"

    e2b_api_key: str = ""

    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "kimi-k2.6"
    litellm_timeout_seconds: float = 180.0

    # --- adaptive policy (section 13) ---
    min_questions: int = Field(default=5, ge=1)
    max_questions: int = Field(default=12, ge=1)
    target_standard_error: float = Field(default=0.20, gt=0.0)
    time_limit_minutes: int = Field(default=60, ge=1)

    # --- selection (sections 15-17) ---
    shortlist_size: int = Field(default=5, ge=1)
    # Below this fraction of the best available utility, the model's pick is overridden.
    # Section 17's optional production tolerance, on by default: it is the only thing
    # bounding how bad a constrained choice can be, and the bound costs nothing when the
    # model behaves.
    minimum_relative_utility: float = Field(default=0.75, ge=0.0, le=1.0)

    # --- LLM evidence handling (section 12) ---
    # Below this, the model's contribution is down-weighted rather than trusted.
    minimum_llm_confidence: float = Field(default=0.70, ge=0.0, le=1.0)


_load_streamlit_secrets()
settings = Settings()
