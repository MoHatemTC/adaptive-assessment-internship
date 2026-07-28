"""Configuration. Mirrors the parent project's pydantic-settings convention.

Only the keys this module actually needs are declared. `extra="ignore"` lets the same
`.env` serve the full backend, so dropping these files into the parent project needs no
configuration surgery — the CAT settings are simply additional keys.

TWO ENGINES, ONE CONFIGURATION

Both modalities are configured here, prefixed by which engine reads them:

    cat_*    the MCQ engine's CAT policy (3PL, theta scale, EAP on a grid)
    code_*   the code engine's policy (Beta posterior, mastery scale, sandbox)

The prefixes are not decoration. The two engines measure on different scales, so a
standard-error target means different things to each — 0.65 is a reasonable stop for
theta over [-4, 4] and would be unreachable nonsense for mastery over [0, 1]. Sharing a
name would invite exactly that confusion.
"""

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)

# How much authority the model holds over a code score: A objective-only, B test-anchored,
# C model-led. See the README for the measurement that chose B.
CodeApproach = Literal["A", "B", "C"]

# Rubric strictness. Only ever reaches the model; the deterministic path never reads it.
CodeRubric = Literal["loose", "mid", "tight"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    # --- LLM: every call routes through the LiteLLM proxy ---
    # Use the OpenAI-compatible API root, e.g. https://host/litellm/v1
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "gemini/gemini-2.5-flash"
    # Optional model used by embedding paths in other modules.
    litellm_embedding_model: str = "text-embedding-004"
    # Speech-to-text model used by /api/cat open-audio path.
    litellm_transcribe_model: str = "openai/whisper-1"

    litellm_timeout_seconds: float = 180.0
    # The gateway at https://3.75.224.129 uses a cert whose SAN does not include the IP,
    # so curl needs --insecure and the OpenAI client needs verify=False.
    litellm_ssl_verify: bool = True

    # --- CAT policy -------------------------------------------------------------
    # Target posterior standard error. Reaching it is the only stop that counts as
    # measured precision. Validate a bank against this before trusting it: information
    # adds, so a pool whose items are individually weak cannot reach any target within a
    # bounded number of questions no matter how the engine selects.
    # Voice package defaults: retuned for fractional open/voice weights (plan risk #1).
    # Override via CAT_SE_TARGET / VOICE_SE_TARGET in backend/.env.
    cat_se_target: float = Field(default=0.80, gt=0.0)

    # Hard ceiling on questions per competency.
    cat_max_questions: int = Field(default=12, ge=1)

    # Floor before a PRECISION stop may fire (high-a items can crush SE in 2–3 answers).
    cat_precision_min_questions: int = Field(default=4, ge=1)

    # The stable-band rule may not fire before this many questions: early on, a repeated
    # band means the prior has not moved yet, not that the estimate has settled.
    cat_min_questions: int = Field(default=4, ge=1)
    cat_stable_window: int = Field(default=3, ge=2)
    # ...and may not fire above this standard error, so "the band stopped moving" cannot
    # be mistaken for "the estimate is precise" while it is still vague.
    cat_stability_se_ceiling: float = Field(default=0.95, gt=0.0)

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

    # --- code engine: scoring policy ------------------------------------------
    # B is the measured default: on 150 seeded submissions with known defects it separated
    # 49 of 67 gradable cases against the objective-only arm's 47, a paired margin of
    # +0.018 [+0.002, +0.034], while lifting misconception recall from 0.22 to 0.92. The
    # model earns its place by explaining defects, not by pricing them.
    code_approach: CodeApproach = "B"
    code_rubric: CodeRubric = "mid"

    # Untrusted code only ever runs inside E2B, never in this process.
    e2b_api_key: str = ""
    # How long a candidate's code may run is policy, not a property of the harness.
    code_execution_timeout_seconds: int = Field(default=30, ge=1)

    # --- code engine: trial runs before submitting ------------------------------
    # A candidate may run their solution against the question's PUBLIC test cases before
    # committing to it, which is how anyone actually writes code. Without it the first
    # execution a candidate ever sees is the graded one, so a typo they would have caught
    # in five seconds is measured as not knowing the material.
    #
    # Public cases only, enforced in `trial.py` and not in the UI: the hidden cases are
    # what stop a submission being tuned to the examples, so leaking them through a
    # convenience feature would quietly void the measurement. A UI-level filter would be
    # one careless edit away from doing exactly that.
    code_trial_run_tests: int = Field(default=3, ge=1)
    # Free in score terms, not in sandbox terms. Bounded per question so one candidate
    # cannot exhaust the execution budget, and so the feature stays a check rather than a
    # search procedure.
    code_trial_runs_per_question: int = Field(default=5, ge=0)

    # --- code engine: adaptive policy -----------------------------------------
    code_min_questions: int = Field(default=5, ge=1)
    code_max_questions: int = Field(default=12, ge=1)
    # 0.15 on the MASTERY scale, which is not comparable to cat_se_target on theta. A
    # Beta(1,1) prior sits at SE 0.2887, so a 0.20 target is met after one question and
    # min_questions decides every session length. Within max_questions the reachable floor
    # is ~0.12. irt.stop_rule_calibration() reports which regime a configuration is in.
    code_se_target: float = Field(default=0.15, gt=0.0)
    code_time_limit_minutes: int = Field(default=60, ge=1)

    # --- code engine: selection ------------------------------------------------
    code_shortlist_size: int = Field(default=5, ge=1)
    # Below this fraction of the best available utility the model's pick is overridden.
    code_minimum_relative_utility: float = Field(default=0.75, ge=0.0, le=1.0)
    # Below this the model's contribution is halved rather than dropped, so a hedged
    # reading degrades toward objective evidence instead of silently changing which
    # sources scored the criterion.
    code_minimum_llm_confidence: float = Field(default=0.70, ge=0.0, le=1.0)

    # --- code engine: operator control of the logic/LLM split -------------------
    # Optional JSON applied on top of the approach preset, e.g. {"code_quality": 0.5}.
    # Whatever is in force is fingerprinted onto every score.
    code_llm_shares: str = ""
    # Call the model for DIAGNOSIS even when it holds no share of the score. The two
    # contributions are worth very different amounts — separation +0.018, but misconception
    # recall 0.22 -> 0.92 — and should be purchasable separately.
    code_llm_diagnosis_when_unweighted: bool = False

    # --- orchestrator: cross-modality assessment -------------------------------
    # Items offered to the picking agent per variable. Wide enough that the choice is
    # meaningful, narrow enough that the prompt stays small.
    orchestrator_shortlist_size: int = Field(default=5, ge=1)
    # Below this fraction of the best available information the picker's choice is
    # overridden. The only bound on how bad a constrained pick can be.
    orchestrator_minimum_relative_utility: float = Field(default=0.75, ge=0.0, le=1.0)
    # Whole-assessment budget, across every variable. Per-variable stopping is the MCQ
    # engine's convergence rule; this is the outer bound.
    orchestrator_max_items: int = Field(default=60, ge=1)
    orchestrator_time_limit_minutes: int = Field(default=90, ge=1)

    # --- observability: Langfuse -----------------------------------------------
    # Traces every model call and groups them by assessment session. Off unless both keys
    # are present — see `services/observability.py` for why monitoring is never allowed to
    # be load-bearing here.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    # Keeps a tester's traces separable from a real cohort's inside one project.
    langfuse_environment: str = "development"

    @property
    def langfuse_enabled(self) -> bool:
        """Both keys, or nothing. A public key alone cannot authenticate."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

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
