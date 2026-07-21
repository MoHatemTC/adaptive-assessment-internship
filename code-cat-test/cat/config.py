"""Configuration, including which approach this checkout implements.

`CODE_CAT_APPROACH` is what makes the three branches comparable: it names the variant so
every audit record, every measurement run and the Streamlit header agree on what was
actually executed. A run whose results cannot be attributed to a variant is not evidence.
"""

from __future__ import annotations

import json
import logging

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

    code_cat_approach: Approach = "A"
    code_cat_rubric: Rubric = "mid"

    e2b_api_key: str = ""

    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "kimi-k2.6"
    litellm_timeout_seconds: float = 180.0

    # --- adaptive policy (section 13) ---
    min_questions: int = Field(default=5, ge=1)
    max_questions: int = Field(default=12, ge=1)
    # 0.15, not 0.20. A Beta(1,1) prior already sits at SE 0.2887, so a 0.20 target is
    # crossed after ONE question — every traced session met it at step 2 and then ran to 5
    # anyway, because min_questions was doing all the work. A fixed-length test wearing
    # adaptive clothing. Within max_questions=12 the reachable floor is about 0.12, so
    # 0.15 lands near question 7: late enough to require real evidence, early enough that
    # a converging candidate stops before the cap. Verified by stop_rule_calibration().
    target_standard_error: float = Field(default=0.15, gt=0.0)
    time_limit_minutes: int = Field(default=60, ge=1)

    # --- selection (sections 15-17) ---
    shortlist_size: int = Field(default=5, ge=1)
    # Below this fraction of the best available utility, the model's pick is overridden.
    # Section 17's optional production tolerance, on by default: it is the only thing
    # bounding how bad a constrained choice can be, and the bound costs nothing when the
    # model behaves.
    minimum_relative_utility: float = Field(default=0.75, ge=0.0, le=1.0)

    # --- admin control of the logic/LLM split ---
    # Off by default. The admin panel changes how every subsequent submission is scored,
    # so it must be opted into deliberately rather than shipped open. This is a feature
    # gate, NOT authentication: Streamlit has no user model here, so anyone who can reach
    # an admin-enabled deployment can change the weights. Put a real auth layer in front
    # of it before exposing it beyond a trusted operator.
    code_cat_admin: bool = False
    # Optional JSON, e.g. {"code_quality": 0.5, "algorithm_choice": 0.2}. Applied on top
    # of the approach preset for headless runs (score_approach.py, batch scoring) where
    # there is no UI to set it in.
    code_cat_llm_shares: str = ""
    # Call the model for DIAGNOSIS even when it holds no share of the score.
    #
    # The measured split of what the model contributes is lopsided: on 150 seeded
    # submissions it moved separation by +0.018 [+0.002, +0.034] — real but small — while
    # moving misconception recall from 0.22 to 0.92. Its value is explaining the defect,
    # not pricing it. Tying the call to score weight forces those to be bought together.
    #
    # Default False so approach A stays genuinely model-free: A's zero cost is a measured
    # property of the study, and quietly issuing calls for it would destroy that control.
    llm_diagnosis_when_unweighted: bool = False

    # --- LLM evidence handling (section 12) ---
    # Below this, the model's contribution is down-weighted rather than trusted.
    minimum_llm_confidence: float = Field(default=0.70, ge=0.0, le=1.0)

    def llm_share_override(self) -> dict[str, float]:
        """Parsed `code_cat_llm_shares`, or {} when unset or malformed.

        Never raises. A typo in an environment variable must not take down scoring — it
        falls back to the approach preset, which is a defined, documented split rather
        than an arbitrary one.
        """
        raw = (self.code_cat_llm_shares or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).warning(
                "CODE_CAT_LLM_SHARES is not valid JSON (%r) — using the %s preset instead",
                raw[:80], self.code_cat_approach,
            )
            return {}


_load_streamlit_secrets()
settings = Settings()
