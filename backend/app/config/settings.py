"""Configuration. Mirrors the parent project's pydantic-settings convention.

Only the keys this module actually needs are declared. `extra="ignore"` lets the same
`.env` serve the full backend, so dropping these files into the parent project needs no
configuration surgery — the CAT settings are simply additional keys.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM: every call routes through the LiteLLM proxy, as in the parent project ---
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "gemini/gemini-2.5-flash"
    # Reasoning models spend real wall clock on a single selection call — measured at
    # ~20s for kimi-k2.6. A timeout tuned for a non-reasoning model reports a slow call
    # as a failed one, and the selector then falls back to the deterministic path while
    # reporting that the model declined.
    litellm_timeout_seconds: float = 180.0

    # --- CAT policy -------------------------------------------------------------
    # Target posterior standard error. Reaching it is the only stop that counts as
    # measured precision. Validate a bank against this before trusting it: information
    # adds, so a pool whose items are individually weak cannot reach any target within a
    # bounded number of questions no matter how the engine selects.
    cat_se_target: float = Field(default=0.65, gt=0.0)

    # Hard ceiling on questions per competency.
    cat_max_questions: int = Field(default=12, ge=1)

    # The stable-band rule may not fire before this many questions: early on, a repeated
    # band means the prior has not moved yet, not that the estimate has settled.
    cat_min_questions: int = Field(default=6, ge=1)
    cat_stable_window: int = Field(default=3, ge=2)
    # ...and may not fire above this standard error, so "the band stopped moving" cannot
    # be mistaken for "the estimate is precise" while it is still vague.
    cat_stability_se_ceiling: float = Field(default=0.80, gt=0.0)

    # Blueprint coverage. Among items carrying at least this fraction of the best
    # available information, the least-served sub-competency wins. Expressed as a
    # constraint rather than a tie-break because a tie-break sitting behind a continuous
    # key never fires, which leaves coverage to luck.
    cat_content_balance_floor: float = Field(default=0.80, ge=0.0, le=1.0)

    # Randomesque exposure control: administer a uniform pick from the top-k ranked items
    # instead of the argmax, so a cohort at the same ability does not all receive an
    # identical form and leak the bank. 1 disables it, which is what tests and
    # reproducibility work want.
    cat_exposure_top_k: int = Field(default=3, ge=1)

    # Let the model rewrite a stem for readability. Off by default: `b` is calibrated for
    # specific wording, so a rewritten stem is not the item the parameters describe, and
    # the guard can only check surface fidelity — it cannot prove difficulty was
    # preserved. Opt in when studying the feature, not by accident.
    cat_rephrasing_enabled: bool = False


settings = Settings()
