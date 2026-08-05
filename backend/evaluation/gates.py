"""The gate table, restructured into tiers, plus the absolute bars the plan never had.

TWO PROBLEMS THIS FILE FIXES

B4 — multiplicity. Section 27 lists 26 gates and presents them as one species of claim.
Eight are deterministic unit tests with no sampling error; the rest are estimates. At
alpha = 0.05 each, roughly eighteen statistical gates give about a 60% chance that an
acceptable Approach C fails at least one of them by noise. So the gates are tiered:

    TIER 1  deterministic invariants   binary, no interval, no alpha. CI-gated.
    TIER 2  ONE primary endpoint       exact-level non-inferiority at 2pp, one-sided 0.05.
                                       This alone carries the measurement verdict.
    TIER 3  safety rates               one-sided 95% UPPER bounds, not point estimates.
    TIER 4  everything else            reported with intervals, no pass/fail verdict.

B3 — no absolute bar. Every gate in section 27 is B-versus-C relative, so C can match B
exactly, pass all 26, and ship an instrument that assigns the right level 63% of the time.
The absolute gates below apply to WHICHEVER approach ships and are evaluated per arm,
independently of any comparison. If they fail for both arms the correct output of the study
is "neither is ready", and the binding constraint is test length or scale granularity, not
the DAG.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    gate_id: str
    metric: str
    tier: int
    rule: str
    # The comparison to apply: "<=", "<", ">=", "in" (a range), or "deterministic".
    op: str
    threshold: float | tuple[float, float]
    source: str = ""


# TIER 2 — the single primary endpoint. One alpha, one decision.
PRIMARY = Gate(
    gate_id="M-03",
    metric="exact_level_accuracy_degradation_upper_bound_pp",
    tier=2,
    rule="one-sided 95% upper bound on level-accuracy degradation < 2 pp, COMMON band scale",
    op="<",
    threshold=2.0,
    source="plan sec 27; promoted to sole primary endpoint by validation A5",
)

# TIER 3 — safety. Evaluated as UPPER BOUNDS, which is what makes the sample-size
# requirement visible instead of hidden.
SAFETY = (
    Gate("C-DAG-03", "wrong_inference_rate", 3, "95% UCB < 3%", "<", 0.03, "plan sec 20"),
    Gate("C-DAG-04", "false_blocking_rate", 3, "95% UCB < 2%", "<", 0.02, "plan sec 20"),
    # The plan's 0% applies to STRICT prerequisites. The AI Engineer graph declares none —
    # every PREREQUISITE edge carries strength 0.5 and `validation_status: unvalidated` —
    # so this is reported as a rate and carries no verdict. A gate at zero against edges
    # nobody declared strict would be a verdict about a category the graph does not have.
    Gate("C-DAG-05", "missed_blocking_rate", 4, "descriptive: no strict edges in this graph", ">=", 0.0, "plan sec 20"),
    # DETERMINISTIC, not statistical: the evidence ledger either deduplicated or it did
    # not. Evaluated on the observed COUNT, so a clean run reads PASS instead of failing
    # on the upper bound of a rate that is exactly zero.
    Gate("C-DAG-11", "duplicate_evidence_events", 1, "0 events", "<=", 0.0, "plan sec 20"),
    # RENAMED, AND ITS GATE CORRECTED. This measures how often a converged competency's
    # own 95% credible interval excludes the truth — which is interval coverage, not
    # premature stopping. A perfectly calibrated interval excludes the truth 5% of the
    # time BY CONSTRUCTION, so the plan's "<2%" demanded deliberate over-coverage and
    # could never be met by a well-calibrated estimator. The honest gate is two-sided
    # around the nominal level.
    Gate("U-02", "interval_non_coverage_rate", 3, "3%-7% (nominal 5%)", "in", (0.03, 0.07), "corrected from plan S-02"),
    Gate("C-DAG-15", "over_convergence_rate", 3, "95% UCB < 2%", "<", 0.02, "plan sec 20"),
)

# B3's missing absolute bars. Applied per arm, with no reference to the other arm.
ABSOLUTE = (
    Gate(
        "ABS-01",
        "exact_level_accuracy_common",
        2,
        ">= 0.80 overall",
        ">=",
        0.80,
        "Livingston & Lewis (1995); Rudner (2001)",
    ),
    Gate("ABS-02", "min_band_accuracy", 2, ">= 0.70 in every reported band", ">=", 0.70, "Rudner (2001)"),
    Gate(
        "ABS-03",
        "marginal_reliability",
        2,
        ">= 0.85 to certify a level; >= 0.80 for routing",
        ">=",
        0.85,
        "Nunnally & Bernstein (1994)",
    ),
    Gate("ABS-04", "min_decile_accuracy", 2, "no true-theta decile below 0.65", ">=", 0.65, "validation B3"),
    Gate("ABS-05", "decision_consistency", 2, ">= 0.75", ">=", 0.75, "Livingston & Lewis (1995)"),
    Gate(
        "ABS-06",
        "se_calibration_ratio",
        2,
        "RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting",
        "in",
        (0.95, 1.10),
        "validation, new U-05",
    ),
    Gate(
        "ABS-07",
        "p90_duration_minutes",
        2,
        "P90 modelled duration <= 90 minutes",
        "<=",
        90.0,
        "validation: E-04 gains a gate",
    ),
)

# TIER 4 — reported, never a verdict. Listed so the report can say explicitly that these
# were measured and deliberately not gated, rather than leaving a reader to assume the
# absence of a verdict means the absence of a result.
DESCRIPTIVE_METRICS = (
    "theta_rmse",
    "theta_mae",
    "within_one_level",
    "questions_per_candidate",
    "question_reduction",
    "duration_minutes",
    "heldout_auc",
    "heldout_brier",
    "heldout_log_loss",
    "heldout_ece",
    "final_se_median",
    "interval_coverage_95",
    "information_realization_ratio",
    "stop_reason_distribution",
    "modality_blueprint_compliance",
    "coverage_satisfied_rate",
)


def verdict(gate: Gate, value: float | None) -> str:
    """PASS / FAIL / NOT MEASURED for one gate. Never invents a verdict from a missing value."""
    if value is None or value != value:  # None or NaN
        return "NOT MEASURED"
    if gate.op == "<":
        return "PASS" if value < float(gate.threshold) else "FAIL"
    if gate.op == "<=":
        return "PASS" if value <= float(gate.threshold) else "FAIL"
    if gate.op == ">=":
        return "PASS" if value >= float(gate.threshold) else "FAIL"
    if gate.op == "in":
        low, high = gate.threshold  # type: ignore[misc]
        return "PASS" if low <= value <= high else "FAIL"
    raise ValueError(f"unknown gate operator {gate.op!r}")


def family_wise_error(independent_gates: int, alpha: float = 0.05) -> float:
    """P(at least one spurious failure) when every gate runs at `alpha`.

    Printed in the report beside the gate count, because the number is the argument: 26
    gates at 0.05 is a 73.6% chance of failing one by noise even when C is fine.
    """
    return float(1.0 - (1.0 - alpha) ** independent_gates)
