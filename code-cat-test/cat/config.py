"""Configuration, including which approach this checkout implements.

`CODE_CAT_APPROACH` is what makes the three branches comparable: it names the variant so
every audit record, every measurement run and the Streamlit header agree on what was
actually executed. A run whose results cannot be attributed to a variant is not evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# A: objective only        tests + static analysis, deterministic rubric, no LLM scoring
# B: test-anchored LLM     LLM interprets; functional correctness anchored to tests
# C: LLM-led               LLM scores every criterion; only hard contradictions blocked
Approach = Literal["A", "B", "C"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    code_cat_approach: Approach = "C"

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


settings = Settings()
