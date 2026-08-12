"""A short hash over the settings that change what the engine MEASURES.

THE FAILURE THIS EXISTS TO MAKE VISIBLE

The engine's configuration is environment-driven and read once at import. In a monolith
that is unremarkable — there is one process, so there is one answer. Split across services
there are four, and nothing makes them agree. A grader with `CODE_APPROACH=C` beside an
orchestrator that believes it is `B` produces a session whose scores were computed one way
and whose stopping rule assumed another. Every number in the report is internally
consistent; all of them are wrong; and no request fails.

So every service reports this on `/health`, and the orchestrator compares its peers'
against its own at startup. A mismatched pair becomes a log line and a dashboard field
rather than a measurement nobody can reproduce.

WHAT IS IN IT, AND WHAT IS NOT

Only settings that change a NUMBER a candidate is scored by, or a rule that decides when to
stop. Not URLs, not keys, not log levels, not retention windows — a bank registry and an
orchestrator legitimately differ on all of those, and a fingerprint that flagged it would
be ignored within a week.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The settings a shared fingerprint covers. Grouped by what they decide, because the
#: grouping is what a reader needs when the hashes differ and they have to find out where.
MEASUREMENT_SETTINGS: tuple[str, ...] = (
    # what a theta estimate means, and when one is finished
    "cat_se_target",
    "cat_max_questions",
    "cat_min_questions",
    "cat_precision_min_questions",
    "cat_stable_window",
    "cat_stability_se_ceiling",
    "cat_band_probability_stop_enabled",
    "cat_band_probability_target",
    "cat_band_probability_stop_conjunctive",
    "cat_interval_widening_enabled",
    "cat_interval_widening_factor",
    "cat_band_decisions_certified",
    # What counts as a response the posterior could not explain, and whether one delays
    # convergence. Both change what `converged` MEANS, which is the test for belonging here.
    "cat_aberrant_residual_threshold",
    "cat_aberrance_drives_verification",
    # which item is chosen
    "cat_content_balance_floor",
    "cat_exposure_top_k",
    "cat_difficulty_corroboration_slack",
    "cat_challenge_difficulty_window",
    "orchestrator_shortlist_size",
    "orchestrator_minimum_relative_utility",
    "orchestrator_max_items",
    "orchestrator_time_limit_minutes",
    "orchestrator_time_reserve_seconds",
    "orchestrator_time_aware_selection_enabled",
    "orchestrator_modality_minimums",
    # how a code submission is scored
    "code_approach",
    "code_rubric",
    "code_minimum_llm_confidence",
    "code_execution_timeout_seconds",
    # what the graph is allowed to do
    "competency_graph_enabled",
    "graph_filtering_enabled",
    "graph_upward_inference_enabled",
    "graph_descendant_blocking_enabled",
    "graph_utility_enabled",
    "graph_convergence_gate_enabled",
    "graph_coverage_critical_only",
    "graph_minimum_failures_to_block",
    "graph_strong_success_threshold",
    "graph_strong_failure_threshold",
    "graph_minimum_propagation_confidence",
    # which bank, when a caller does not say
    "active_bank",
)


def measurement_settings() -> dict[str, Any]:
    """The values in force, for a `/config` block or a mismatch diagnosis."""
    from cat_engine.engine.config.settings import settings

    return {name: getattr(settings, name, None) for name in MEASUREMENT_SETTINGS}


def engine_config_fingerprint() -> str:
    """Twelve hex characters over the measurement settings.

    Short on purpose: it goes in a health payload and a log line, and its whole job is to
    be compared for equality by eye. `measurement_settings()` is what you call once the
    comparison fails.
    """
    payload = json.dumps(measurement_settings(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
