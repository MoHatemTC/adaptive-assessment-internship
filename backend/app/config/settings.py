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

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PACKAGE_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM: every call routes through the LiteLLM proxy, as in the parent project ---
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    litellm_model: str = "openai/gpt-5.6-sol"
    litellm_embedding_model: str = "text-embedding-004"
    litellm_transcribe_model: str = "openai/whisper-1"
    # Gemini Live interviewer via LiteLLM realtime websocket (/v1/realtime).
    litellm_live_preview_model: str = "gemini/gemini-3.1-flash-live-preview"
    # Reasoning models spend real wall clock on a single selection call — measured at
    # ~20s for kimi-k2.6. A timeout tuned for a non-reasoning model reports a slow call
    # as a failed one, and the selector then falls back to the deterministic path while
    # reporting that the model declined.
    litellm_timeout_seconds: float = 180.0
    # Gateway often reached by IP with a cert SAN mismatch — set LITELLM_SSL_VERIFY=false.
    litellm_ssl_verify: bool = True

    # --- bank selection ---------------------------------------------------------
    # Which registered bank a session uses when the caller does not name one. Resolved by
    # `services.orchestrator.registry`, which owns the id -> (bank file, graph file) map;
    # this module deliberately does not import it, since settings is imported by nearly
    # everything and the registry imports the item schema.
    #
    # A bank and its competency graph are selected TOGETHER. Pairing an AI Engineer bank
    # with the Data Analysis graph makes every required coverage node unmeasurable and
    # vetoes convergence forever, which reads as a measurement fault rather than a
    # configuration one.
    active_bank: str = "AIE"

    # Where finished assessment states are appended, one JSON line per session, as
    # `{dir}/{bank_id}.jsonl`. Empty disables it.
    #
    # This is the only corpus `scripts/validate_prerequisite_edges.py` can compute edge
    # validity from — P(pass child | fail parent) needs sessions where both nodes received
    # direct evidence, and nothing else in the system persists a finished session. Off by
    # default because a dump of assessment states is candidate data.
    cat_session_dump_dir: str = ""

    # --- CAT policy -------------------------------------------------------------
    # Target posterior standard error. Reaching it (after the precision floor below) is
    # the stop that counts as measured precision. Validate a bank against this before
    # trusting it: information adds, so a pool whose items are individually weak cannot
    # reach any target within a bounded number of questions no matter how selection works.
    #
    # 0.55 (≈90% on the certainty scale) is tighter than a one-shot high-a update can
    # usually fake: three very informative items near the candidate often reach ~0.59,
    # which used to stop a 0.65 target after a short interview.
    cat_se_target: float = Field(default=0.55, gt=0.0)

    # Hard ceiling on questions per competency.
    cat_max_questions: int = Field(default=12, ge=1)

    # Floor before a PRECISION stop may fire. High-discrimination items can crush SE in
    # two or three answers; without this floor the test ends before coverage / modality
    # mix has a chance to corroborate the estimate. Independent of stable_band's floor.
    cat_precision_min_questions: int = Field(default=6, ge=1)

    # The stable-band rule may not fire before this many questions: early on, a repeated
    # band means the prior has not moved yet, not that the estimate has settled.
    cat_min_questions: int = Field(default=6, ge=1)
    cat_stable_window: int = Field(default=3, ge=2)
    # ...and may not fire above this standard error, so "the band stopped moving" cannot
    # be mistaken for "the estimate is precise" while it is still vague.
    cat_stability_se_ceiling: float = Field(default=0.55, gt=0.0)

    # A narrow posterior based only on easy items is not enough to classify the candidate.
    # Before convergence, require an administered item near the estimate; after a streak
    # of strong answers, require a challenge near the next ability-band boundary.
    cat_difficulty_corroboration_slack: float = Field(default=0.25, ge=0.0)
    # Keep a challenge near that boundary rather than jumping several levels at once.
    cat_challenge_difficulty_window: float = Field(default=0.75, gt=0.0)

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

    # Stop when the reported LEVEL is probably right, rather than when the estimate is
    # precise. OFF, and additive: it can end a competency early, never hold one open.
    #
    # Secondary because it asks a different question from precision and, near a band cut
    # point, demands materially more evidence for the same standard error. That is the
    # rule working — but it changes test length, and that wants measuring before it is
    # trusted.
    cat_band_probability_stop_enabled: bool = False
    cat_band_probability_target: float = Field(default=0.80, gt=0.0, lt=1.0)
    # Require the precision target TOO, rather than accepting P(band) instead of it.
    #
    # Without this the rule is evaluated before the precision rule and never sees the
    # standard error, so it can finalise a competency whose posterior is far wider than
    # target — concentrated mass in one band is not a precise estimate, and near a cut
    # point the two claims come apart. Off by default so no shipped report changes; the
    # evaluation harness turns it on, because every propagation configuration there is
    # judged by its effect on a posterior and the posterior has to mean one thing.
    cat_band_probability_stop_conjunctive: bool = False

    # REPORTING ONLY. Widen the reported SE and credible interval by a measured factor.
    #
    # The posterior is calibrated when a competency really is one skill and 30-40%
    # overconfident when it is several: SE calibration RMSE/SE landed at 1.33-1.45 under
    # DGP-2 against a 0.95-1.10 bar, and 95% intervals covered 83-87% against a 93-97%
    # bar. That was measured and then reported as a diagnostic while the engine went on
    # shipping the overconfident interval.
    #
    # Applied at the reporting boundary and NOWHERE else. Widening
    # `VariableState.standard_error` would move the precision stop, change session length,
    # and so change the posterior the factor was calibrated against — a self-referential
    # correction. `VariableReport` carries the raw SE alongside the widened one so the
    # adjustment is auditable rather than invisible.
    cat_interval_widening_enabled: bool = False
    cat_interval_widening_factor: float = Field(default=1.0, ge=1.0, le=3.0)
    # Per-competency overrides as a JSON object, e.g. {"C1": 1.33, "C6": 1.45}. The
    # factor is a property of how multidimensional a competency is, so one global number
    # is the wrong shape wherever that differs by main.
    cat_interval_widening_by_variable: str = ""

    # Flag a response that the current posterior did not expect. Report-only: no branch
    # may read it and change an estimate.
    cat_aberrant_residual_threshold: float = Field(default=2.0, gt=0.0)
    cat_aberrance_drives_verification: bool = False

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
    # A runaway guard, not a budget. At three mains and a twelve-question cap the session
    # can administer at most 36 items, so this never binds — TIME does. Raised from 60 so
    # it stops looking like the operative limit.
    orchestrator_max_items: int = Field(default=120, ge=1)
    orchestrator_time_limit_minutes: int = Field(default=90, ge=1)
    # Held back for the report and wrap-up, and excluded from item admissibility.
    orchestrator_time_reserve_seconds: int = Field(default=120, ge=0)

    # Rank on information PER MINUTE, weighted by the evidence a modality carries.
    #
    # ON by default because without it the 90-minute limit is arithmetically unreachable:
    # eighteen code items — the bare observation floor for three mains under a code-heavy
    # diet — is 147 minutes. OFF restores ranking on raw information exactly.
    orchestrator_time_aware_selection_enabled: bool = True

    # JSON. How much evidence a response in each modality typically carries. MCQ is exact
    # (the grader sets weight from the item loading at confidence 1.0, with no degradation
    # path); the others are read off the graders' discount rungs, NOT measured. They set
    # the modality mix and therefore session length — measure them at the first pilot.
    orchestrator_expected_weight_by_modality: str = ""
    # JSON. Per-modality wall clock. Empty means "use the measured calibration if there
    # is one, else the documented default" — see `services.orchestrator.selection_calibration`.
    orchestrator_item_seconds_by_modality: str = ""

    # Guarantee a mix. Pure information-per-minute makes the top twelve items all MCQ on
    # the live bank — measured, a 6.6x advantage — so a "mixed" assessment quietly stops
    # being one. Enforced as a pool constraint before the observation floor is reached, for
    # the same reason coverage is: a soft bonus cannot survive that ratio.
    orchestrator_modality_minimums: str = '{"code": 1, "voice": 1}'

    # --- competency graph layer (graph-augmented CAT) ------------------------
    # THE MASTER SWITCH. Every graph entry point checks it first, through
    # `competency_graph.config.graph_enabled()`; off means the engine behaves as though no
    # graph existed, whatever the sub-flags say.
    #
    # It defaults True because the coverage gate below is already live by default, so the
    # graph layer is already load-bearing — a switch that claimed to disable it while it
    # kept running is worse than no switch, because an operator will reach for it during
    # an incident and nothing will happen.
    competency_graph_enabled: bool = True
    graph_shadow_mode: bool = True

    # Inference / filtering / utility stay off until measured. The edges they would read
    # are unvalidated (see scripts/validate_prerequisite_edges.py).
    graph_filtering_enabled: bool = False
    graph_upward_inference_enabled: bool = False
    graph_descendant_blocking_enabled: bool = False
    graph_utility_enabled: bool = False
    # On by default: a main may not claim precision/stable-band convergence until every
    # required sub-competency has direct success or failure evidence. Budget stops still
    # finalise without that claim.
    graph_convergence_gate_enabled: bool = True
    # False = every sub under the main must be directly measured; True = critical only.
    # Overridden per bank by `BankProfile.coverage_critical_only` — the right answer
    # depends on how many sub-competencies a main declares against the question budget.
    graph_coverage_critical_only: bool = False

    # REPORTING ONLY. Runs inference and blocking again with the per-edge validation gates
    # waived, so a session records what the unvalidated PREREQUISITE edges WOULD have
    # concluded. Nothing reads it: not the posterior, not selection, not coverage.
    #
    # Without it a bank whose edges all ship inert reports zero inferred and zero blocked
    # nodes forever, which is indistinguishable from inference being broken — and leaves no
    # way to judge an edge until someone enables it on live candidates. See
    # `scripts/validate_prerequisite_edges.py`, which turns this record into a verdict.
    graph_edge_preview_enabled: bool = True

    # --- propagation policy ---------------------------------------------------
    # The specification's suggested values, none of which has been calibrated against
    # candidate data. Exposed because they are precisely the numbers an operator needs to
    # move while watching a metric, and they used to be literals at five call sites.
    graph_strong_success_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    graph_strong_failure_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    graph_minimum_propagation_confidence: float = Field(default=0.80, ge=0.0, le=1.0)
    # Blocking is a stronger claim than inferring: it denies a candidate the chance to
    # demonstrate a skill, so it demands more confidence.
    graph_downward_block_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    # Consistent direct failures of a prerequisite before its descendants may be blocked.
    # At 1 the block inherits the whole per-observation error rate; measured on a graph
    # with correct edges that put false blocking at 4.8-5.1% against a 2% gate. Two
    # consistent failures square the error and bring it inside the gate without changing
    # a single edge, at the cost of one extra observation before a block can fire.
    graph_minimum_failures_to_block: int = Field(default=2, ge=1)
    graph_upward_decay: float = Field(default=0.70, gt=0.0, le=1.0)
    graph_minimum_inferred_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    graph_maximum_inferred_weight: float = Field(default=0.60, ge=0.0, le=1.0)
    # 0 is legal and means "draw no upward conclusion at all". That is NOT the same as
    # GRAPH_UPWARD_INFERENCE_ENABLED=false, which means "draw it, record it in the audit
    # mirror, act on none of it". The distinction is load-bearing: the first produces no
    # provenance to audit later, the second produces the audit trail that decides whether
    # to switch it on. A depth floor of 1 made the first state unreachable.
    graph_maximum_propagation_depth: int = Field(default=4, ge=0)
    # Blank defers to `graph_maximum_propagation_depth`. Set either one to sweep inference
    # depth without moving the blocking frontier, or the reverse.
    graph_maximum_inference_depth: int | None = Field(default=None, ge=0)
    graph_maximum_blocking_depth: int | None = Field(default=None, ge=0)

    # Independent strong successes before an ancestor may be inferred mastered. At 1 an
    # inference carries the whole per-observation error rate, measured at 22.4% wrong.
    # What counts as independent is `graph_corroboration_independence`; at anything weaker
    # than `source_node` the requirement can be satisfied by answering clones of one item,
    # which makes it a decoration rather than a control.
    graph_minimum_corroborations: int = Field(default=1, ge=1)
    graph_corroboration_independence: str = "source_node"

    # One multiple-choice hit does not imply prerequisite mastery. The specification asks
    # for two confirming items first; `graph_minimum_corroborations` is now that knob.
    graph_allow_mcq_single_hit_inference: bool = False
    graph_allow_code_upward_inference: bool = True
    graph_allow_voice_upward_inference: bool = True
    # Explicit modality allowlist, comma-separated, e.g. "code" or "code,voice". Blank
    # derives the set from the three booleans above, so an existing .env is unaffected.
    # Unlike the booleans this can separate `voice` from `open`, which they cannot.
    graph_inference_modalities: str = ""
    # Edge validation statuses this DEPLOYMENT will act on, comma-separated. Blank means
    # no opinion and the bank's own list stands. A deployment may only narrow: the two
    # lists are intersected, never unioned, so this can turn a permissive bank down and
    # can never turn a restrictive one up. "refuted" is refused outright.
    graph_accepted_validation_statuses: str = ""

    # --- graph selection effects ----------------------------------------------
    # Penalties, not exclusions. An excluded item can never disprove the belief that
    # excluded it, which is how a false block becomes permanent and unmeasurable. At
    # -0.90 a blocked item wins only when it is more than ten times better than anything
    # else available — rare, and exactly the case a hard filter destroys.
    #
    # Both must stay strictly above -1.0: at -1.0 the ranking key is zero and the
    # tie-break decides selection; below it, the ranking inverts.
    graph_blocked_penalty: float = Field(default=-0.90, gt=-1.0, le=0.0)
    # "We already know this" is a weaker claim than "we believe this is unreachable".
    graph_mastered_noncritical_penalty: float = Field(default=-0.60, gt=-1.0, le=0.0)
    graph_contradiction_bonus: float = Field(default=0.25, ge=0.0)
    # Specification section 27's S(q). Per ADDITIONAL open main an item also evidences.
    # Ranking is per variable, so without this an item measuring two open competencies is
    # scored as though it measured one, and the shared-evidence design never pays off.
    graph_shared_main_gain: float = Field(default=0.20, ge=0.0)
    # Serve a blocked-node item every Nth observation, so the block is testable. 0 is off,
    # and off means the false-blocking rate cannot be measured in production at all.
    graph_unblocking_probe_period: int = Field(default=5, ge=0)

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

    def _parsed_float_map(self, raw: str, name: str) -> dict[str, float]:
        """A JSON object of modality -> float. Never raises.

        A typo in an environment variable must not silently change how items are ranked.
        """
        raw = (raw or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return {str(k).lower(): float(v) for k, v in parsed.items()}
        except (ValueError, TypeError, AttributeError):
            logger.warning("%s is not valid JSON (%r) — ignoring it", name, raw[:80])
            return {}

    def expected_weight_override(self) -> dict[str, float]:
        """Operator-pinned expected weights. Empty means "decide from data or default"."""
        return self._parsed_float_map(
            self.orchestrator_expected_weight_by_modality,
            "ORCHESTRATOR_EXPECTED_WEIGHT_BY_MODALITY",
        )

    def item_seconds_by_modality(self) -> dict[str, float]:
        """Operator-pinned item durations. Empty means "decide from data or default"."""
        return self._parsed_float_map(
            self.orchestrator_item_seconds_by_modality,
            "ORCHESTRATOR_ITEM_SECONDS_BY_MODALITY",
        )

    def interval_widening_for(self, variable: str) -> float:
        """The reporting widening factor for one competency. 1.0 when disabled.

        A per-variable override wins over the global factor; a missing one falls back to
        it. Never below 1.0 — this exists to widen an overconfident interval, and letting
        it narrow one would turn a calibration correction into a second way to overstate
        precision.
        """
        if not self.cat_interval_widening_enabled:
            return 1.0
        overrides = self._parsed_float_map(
            self.cat_interval_widening_by_variable, "CAT_INTERVAL_WIDENING_BY_VARIABLE"
        )
        factor = overrides.get((variable or "").lower(), self.cat_interval_widening_factor)
        return max(1.0, float(factor))

    @staticmethod
    def _parsed_csv(raw: str) -> tuple[str, ...]:
        return tuple(part.strip().lower() for part in (raw or "").split(",") if part.strip())

    def inference_modalities(self) -> frozenset[str] | None:
        """The explicit modality allowlist, or None to derive it from the legacy booleans.

        Raises on an unknown member rather than dropping it. The other environment parsers
        on this class warn and continue, because a bad weight override changes a ranking;
        a bad modality name changes which evidence is allowed to license an inference, and
        failing open there means propagating from a modality nobody authorised.
        """
        parsed = self._parsed_csv(self.graph_inference_modalities)
        if not parsed:
            return None
        known = {"mcq", "code", "voice", "open"}
        unknown = {m for m in parsed if m not in known} - {"audio"}
        if unknown:
            raise ValueError(
                f"GRAPH_INFERENCE_MODALITIES names unknown modalities {sorted(unknown)}; "
                f"known: {sorted(known)}"
            )
        return frozenset("voice" if m == "audio" else m for m in parsed)

    def accepted_validation_statuses(self) -> tuple[str, ...] | None:
        """The deployment's edge allowlist, or None for "no opinion"."""
        parsed = self._parsed_csv(self.graph_accepted_validation_statuses)
        if not parsed:
            return None
        if "refuted" in parsed:
            raise ValueError(
                "GRAPH_ACCEPTED_VALIDATION_STATUSES may not contain 'refuted': it is the "
                "one verdict meaning the edge was tested and found wrong"
            )
        return parsed

    def modality_minimums(self) -> dict[str, int]:
        """Parsed per-main modality floor. Never raises."""
        raw = (self.orchestrator_modality_minimums or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return {str(k): int(v) for k, v in parsed.items() if int(v) > 0}
        except (ValueError, TypeError, AttributeError):
            logger.warning(
                "ORCHESTRATOR_MODALITY_MINIMUMS is not valid JSON (%r) — ignoring", raw[:80]
            )
            return {}

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
