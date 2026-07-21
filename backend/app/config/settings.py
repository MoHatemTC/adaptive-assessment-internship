"""Configuration for the code-question adaptive engine.

Every policy knob is here rather than inline, so what the engine does is answerable by
reading one file. The defaults are the measured ones — where a value was chosen by
experiment rather than convention, the comment says what the experiment showed.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# How much authority the model holds over the score. The production default is B, chosen
# by measurement: on 150 seeded submissions with known defects it separated 49 of 67
# gradable cases against the objective-only arm's 47, a paired margin of +0.018
# [+0.002, +0.034], while lifting misconception recall from 0.22 to 0.92. The model earns
# its place by explaining defects, not by pricing them.
Approach = Literal["A", "B", "C"]

# Rubric strictness. Only ever reaches the model; the deterministic path never reads it.
Rubric = Literal["loose", "mid", "tight"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- scoring policy ---
    code_approach: Approach = "B"
    code_rubric: Rubric = "mid"

    # --- execution ---
    # Learner code is untrusted and only ever runs inside E2B, never in this process.
    e2b_api_key: str = ""
    execution_timeout_seconds: int = Field(default=30, ge=1)

    # --- LLM gateway ---
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "kimi-k2.6"
    litellm_timeout_seconds: float = 180.0

    # --- adaptive policy ---
    min_questions: int = Field(default=5, ge=1)
    max_questions: int = Field(default=12, ge=1)
    # 0.15, not 0.20. A Beta(1,1) prior already sits at SE 0.2887, so a 0.20 target is met
    # after one question and min_questions decides every session length — a fixed-length
    # test wearing adaptive clothing. Within max_questions the reachable floor is ~0.12.
    # stop_rule_calibration() reports which regime a configuration is in.
    target_standard_error: float = Field(default=0.15, gt=0.0)
    time_limit_minutes: int = Field(default=60, ge=1)

    # --- selection ---
    shortlist_size: int = Field(default=5, ge=1)
    # Below this fraction of the best available utility the model's pick is overridden.
    # The only thing bounding how bad a constrained choice can be, and it costs nothing
    # when the model behaves.
    minimum_relative_utility: float = Field(default=0.75, ge=0.0, le=1.0)

    # --- evidence handling ---
    # Below this the model's contribution is halved rather than dropped, so a hedged
    # reading degrades toward objective evidence instead of silently changing which
    # sources scored the criterion.
    minimum_llm_confidence: float = Field(default=0.70, ge=0.0, le=1.0)

    # --- operator control of the logic/LLM split ---
    # Optional JSON, e.g. {"code_quality": 0.5, "algorithm_choice": 0.2}, applied on top of
    # the approach preset. Lets the split be tuned per deployment without a code change;
    # whatever is in force is fingerprinted onto every score.
    code_llm_shares: str = ""
    # Call the model for DIAGNOSIS even when it holds no share of the score. The two
    # contributions are worth very different amounts — separation +0.018, but misconception
    # recall 0.22 -> 0.92 — and they should be purchasable separately.
    llm_diagnosis_when_unweighted: bool = False

    def llm_share_override(self) -> dict[str, float]:
        """Parsed `code_llm_shares`, or {} when unset or malformed.

        Never raises. A typo in an environment variable must not take scoring down; it
        falls back to the approach preset, which is a defined and documented split rather
        than an arbitrary one.
        """
        raw = (self.code_llm_shares or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return {str(k): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError, AttributeError):
            logger.warning(
                "CODE_LLM_SHARES is not valid JSON (%r) — using the %s preset instead",
                raw[:80], self.code_approach,
            )
            return {}


settings = Settings()
