#!/usr/bin/env python3
"""Join the arms, compute the metrics, apply the tiered gates, write the report.

    python -m evaluation.analyse --runs eval-results/runs --cohorts eval-results/cohorts \
                                 --out eval-results/report

READING ORDER OF THE OUTPUT

    1. DGP-0, the null arm. If Approach C shows a benefit where no prerequisite structure
       exists, the benefit is an artefact and nothing below it means anything.
    2. The absolute gates, per arm. If both arms fail them the comparison is moot.
    3. The primary endpoint, once, at alpha = 0.05.
    4. Safety upper bounds.
    5. Everything else, with intervals and no verdicts.

WHAT IS PAIRED WITH WHAT

    B  vs C-off    prices the branch divergence (band width, stopping rule, corroboration,
                   time-aware selection). NOT the DAG.
    C-off vs C-full   prices the DAG, with every other setting identical by construction.
    C-off vs C-shipped   prices the coverage gate alone, which is the only part of the
                   graph layer that is live on the shipped configuration.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import gates as gate_defs
from . import stats
from .dgp import Cohort

# Per the plan: exact-level degradation <= 2pp is the primary margin.
LEVEL_MARGIN_PP = 2.0
RMSE_RELATIVE_MARGIN = 0.05
QUESTION_REDUCTION_TARGET = 0.15


# --- loading -----------------------------------------------------------------------


def load_runs(runs_dir: Path) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for path in sorted(runs_dir.glob("*.jsonl")):
        arm, dgp = path.stem.split("__", 1)
        out[(arm, dgp)] = [json.loads(line) for line in path.open(encoding="utf-8")]
    return out


def load_cohorts(cohorts_dir: Path) -> dict[str, Cohort]:
    return {c.dgp: c for c in (Cohort.load(p) for p in sorted(cohorts_dir.glob("cohort_*.json")))}


# --- per-arm frames -------------------------------------------------------------------


class Frame:
    """One arm's results, flattened to the (simulee, main) unit the endpoints live on."""

    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.keys: list[tuple[str, str]] = []
        self.simulee: list[str] = []
        self.family: list[str] = []
        self.true_theta: list[float] = []
        self.theta: list[float] = []
        self.se: list[float] = []
        self.common_level: list[int] = []
        self.common_true: list[int] = []
        self.native_level: list[int] = []
        self.native_true: list[int] = []
        self.band_probabilities: list[dict[str, float]] = []
        self.converged: list[bool] = []
        self.stop_reason: list[str] = []
        self.observations: list[int] = []
        self.credible: list[list[float]] = []
        # Tri-state: True / False / None where the arm has no coverage gate.
        self.coverage_ok: list[bool | None] = []
        self.modalities: list[list[str]] = []

        for record in records:
            for variable in record["variables"]:
                self.keys.append((record["simulee_id"], variable["variable"]))
                self.simulee.append(record["simulee_id"])
                self.family.append(record["family"])
                self.true_theta.append(variable["true_theta"])
                self.theta.append(variable["estimated_theta"])
                self.se.append(variable["standard_error"])
                self.common_level.append(variable["common_level"])
                self.common_true.append(variable["common_true_level"])
                self.native_level.append(variable["native_level"])
                self.native_true.append(variable["native_true_level"])
                self.band_probabilities.append(variable.get("common_band_probabilities", {}))
                self.converged.append(variable["converged"])
                self.stop_reason.append(variable["stop_reason"])
                self.observations.append(variable["observations"])
                self.credible.append(variable.get("credible_interval_95") or [])
                # None = this arm has no coverage gate. Kept as None and excluded from the
                # rate rather than counted as a pass: an arm that never ran the gate did
                # not satisfy it, and scoring it 1.0 makes C-off look like it cleared a
                # check it does not have.
                self.coverage_ok.append(variable.get("graph_coverage_satisfied"))
                self.modalities.append(variable.get("modalities_used", []))

    def index(self) -> dict[tuple[str, str], int]:
        return {key: i for i, key in enumerate(self.keys)}

    # session-level vectors, keyed by simulee
    def sessions(self) -> dict[str, dict]:
        return {r["simulee_id"]: r for r in self.records}


def _align(ref: Frame, test: Frame):
    """Common (simulee, main) keys, in a stable order. The pairing, made explicit."""
    ref_ix, test_ix = ref.index(), test.index()
    keys = [k for k in ref.keys if k in test_ix]
    return keys, np.array([ref_ix[k] for k in keys]), np.array([test_ix[k] for k in keys])


# --- per-arm absolute metrics ----------------------------------------------------------


def _rate_over_gated(values: list[bool | None]) -> float:
    """Satisfaction rate over the variables the gate actually applied to.

    NaN, not 1.0, when it applied to none. An arm without a coverage gate has not
    satisfied it — the gate did not run — and reporting 100% for that arm invites the
    comparison "C-off's coverage is as good as C-shipped's", which is a statement about
    a check one of them never performed.
    """
    applicable = [bool(v) for v in values if v is not None]
    return round(float(np.mean(applicable)), 5) if applicable else float("nan")


def absolute_metrics(frame: Frame) -> dict:
    """Everything that can be said about ONE arm without reference to another."""
    theta = np.array(frame.theta)
    true_theta = np.array(frame.true_theta)
    se = np.array(frame.se)
    correct = np.array(frame.common_level) == np.array(frame.common_true)
    native_correct = np.array(frame.native_level) == np.array(frame.native_true)

    rmse = float(np.sqrt(np.mean((theta - true_theta) ** 2)))
    mean_se = float(np.mean(se))

    # Accuracy in each REPORTED band: a strong overall figure can hide a band the
    # instrument cannot resolve at all, and a hiring decision is made per band.
    by_band: dict[int, float] = {}
    for band in sorted(set(frame.common_true)):
        mask = np.array(frame.common_true) == band
        if mask.sum() >= 5:
            by_band[int(band)] = float(correct[mask].mean())

    # Conditional accuracy by true-theta decile — the tail failure an average hides.
    deciles = np.quantile(true_theta, np.linspace(0, 1, 11))
    by_decile: list[float] = []
    for i in range(10):
        lo, hi = deciles[i], deciles[i + 1]
        mask = (true_theta >= lo) & (true_theta <= hi if i == 9 else true_theta < hi)
        if mask.sum() >= 5:
            by_decile.append(float(correct[mask].mean()))

    coverage_95 = float(
        np.mean(
            [
                bool(ci) and ci[0] <= t <= ci[1]
                for ci, t in zip(frame.credible, frame.true_theta)
            ]
        )
    )

    sessions = list(frame.sessions().values())
    durations = np.array([s["modelled_duration_minutes"] for s in sessions])
    questions = np.array([s["items_administered"] for s in sessions])

    # Held-out prediction, pooled.
    p_hat = np.array([h["p_hat"] for s in sessions for h in s["heldout"]])
    y = np.array([h["y"] for s in sessions for h in s["heldout"]])

    blueprint = float(
        np.mean([len(set(m) & {"code", "voice"}) == 2 for m in frame.modalities])
    )

    expected = np.array([s.get("expected_weight_sum", 0.0) for s in sessions])
    realized = np.array([s.get("realized_weight_sum", 0.0) for s in sessions])

    return {
        "n_pairs": len(frame.keys),
        "n_sessions": len(sessions),
        "theta_rmse": round(rmse, 5),
        "theta_mae": round(float(np.mean(np.abs(theta - true_theta))), 5),
        "exact_level_accuracy_common": round(float(correct.mean()), 5),
        "exact_level_accuracy_native": round(float(native_correct.mean()), 5),
        "within_one_level_common": round(
            float(np.mean(np.abs(np.array(frame.common_level) - np.array(frame.common_true)) <= 1)), 5
        ),
        "accuracy_by_band": {str(k): round(v, 4) for k, v in by_band.items()},
        "min_band_accuracy": round(min(by_band.values()), 5) if by_band else float("nan"),
        "min_decile_accuracy": round(min(by_decile), 5) if by_decile else float("nan"),
        "marginal_reliability": round(stats.marginal_reliability(theta, se), 5),
        "decision_consistency": round(stats.decision_consistency(frame.band_probabilities), 5),
        "final_se_median": round(float(np.median(se)), 5),
        "mean_se": round(mean_se, 5),
        # RMSE / mean SE. Above 1.10 the posterior is narrower than the errors justify,
        # which is what double counting looks like from the outside.
        "se_calibration_ratio": round(rmse / mean_se, 5) if mean_se else float("nan"),
        "interval_coverage_95": round(coverage_95, 5),
        "questions_per_candidate_mean": round(float(questions.mean()), 4),
        "questions_per_candidate_median": float(np.median(questions)),
        "duration_minutes_p50": round(float(np.percentile(durations, 50)), 3),
        "duration_minutes_p90": round(float(np.percentile(durations, 90)), 3),
        "p90_duration_minutes": round(float(np.percentile(durations, 90)), 3),
        "duration_over_90min_rate": round(float(np.mean(durations > 90.0)), 5),
        "converged_rate": round(float(np.mean(frame.converged)), 5),
        "coverage_satisfied_rate": _rate_over_gated(frame.coverage_ok),
        "coverage_gate_applicable_n": sum(v is not None for v in frame.coverage_ok),
        "modality_blueprint_compliance": round(blueprint, 5),
        "information_realization_ratio": (
            round(float(realized.sum() / expected.sum()), 5) if expected.sum() > 0 else float("nan")
        ),
        "stop_reason_distribution": {
            reason: round(frame.stop_reason.count(reason) / len(frame.stop_reason), 4)
            for reason in sorted(set(frame.stop_reason))
        },
        "heldout_auc": round(stats.auc(p_hat, y), 5) if p_hat.size else float("nan"),
        "heldout_brier": round(stats.brier(p_hat, y), 5) if p_hat.size else float("nan"),
        "heldout_log_loss": round(stats.log_loss(p_hat, y), 5) if p_hat.size else float("nan"),
        "heldout_ece": round(stats.expected_calibration_error(p_hat, y), 5) if p_hat.size else float("nan"),
    }


def absolute_verdicts(metrics: dict) -> list[dict]:
    rows = []
    for gate in gate_defs.ABSOLUTE:
        value = metrics.get(gate.metric)
        rows.append(
            {
                "gate": gate.gate_id,
                "metric": gate.metric,
                "rule": gate.rule,
                "value": value,
                "verdict": gate_defs.verdict(gate, value),
                "source": gate.source,
            }
        )
    return rows


# --- pairwise comparison ---------------------------------------------------------------


def _per_candidate(values: np.ndarray, keys, families: dict[str, str]):
    """Collapse (simulee, main) rows to one number per candidate, plus their families.

    The resampling unit is the candidate, not the row: a candidate's three mains share a
    generating profile, so treating them as three independent observations would shrink
    every interval. Reported by family too, because a benefit concentrated in one profile
    family is a different finding from a uniform one.
    """
    totals: dict[str, list[float]] = defaultdict(list)
    for value, (simulee, _main) in zip(values, keys):
        totals[simulee].append(float(value))
    simulees = sorted(totals)
    return (
        np.array([float(np.mean(totals[s])) for s in simulees]),
        np.array([families.get(s, "?") for s in simulees]),
        simulees,
    )


def compare(ref: Frame, test: Frame, *, ref_name: str, test_name: str) -> dict:
    keys, ri, ti = _align(ref, test)
    if not keys:
        return {"error": "no shared (simulee, main) keys"}

    families = {s: f for s, f in zip(ref.simulee, ref.family)}

    ref_correct = np.array(ref.common_level)[ri] == np.array(ref.common_true)[ri]
    test_correct = np.array(test.common_level)[ti] == np.array(test.common_true)[ti]

    # PRIMARY ENDPOINT. The decision comes from the clustered bootstrap, because the unit
    # is (simulee, main) and the three mains of one simulee are not independent. Tango's
    # score interval is reported beside it as the analytic cross-check the plan's section
    # 24 asks for, with the caveat that it assumes independent pairs.
    difference = ref_correct.astype(float) - test_correct.astype(float)
    per_candidate, family_strata, _simulees = _per_candidate(difference, keys, families)
    boot = stats.bca_bootstrap(per_candidate, family_strata, one_sided=True, resamples=4000)
    tango = stats.non_inferiority_binary(ref_correct, test_correct, LEVEL_MARGIN_PP / 100.0)

    # By profile family, because the plan's five families are five different questions and
    # a benefit concentrated in one of them is not the benefit the headline claims.
    by_family = {}
    for family in sorted(set(family_strata.tolist())):
        mask = family_strata == family
        by_family[family] = round(100.0 * float(per_candidate[mask].mean()), 3)

    ref_theta = np.array(ref.theta)[ri]
    test_theta = np.array(test.theta)[ti]
    ref_true = np.array(ref.true_theta)[ri]
    squared_difference = (test_theta - ref_true) ** 2 - (ref_theta - ref_true) ** 2
    rmse_ref = float(np.sqrt(np.mean((ref_theta - ref_true) ** 2)))
    rmse_test = float(np.sqrt(np.mean((test_theta - ref_true) ** 2)))
    squared_per_candidate, _fam, _s = _per_candidate(squared_difference, keys, families)
    rmse_boot = stats.bca_bootstrap(
        squared_per_candidate, family_strata, one_sided=True, resamples=4000
    )

    # The between-arm correlation. Section 6 already freezes the seed; this is what that
    # buys, and reporting it is free.
    correlation = float(np.corrcoef(ref_theta, test_theta)[0, 1])

    ref_sessions, test_sessions = ref.sessions(), test.sessions()
    shared = [s for s in ref_sessions if s in test_sessions]
    q_ref = np.array([ref_sessions[s]["items_administered"] for s in shared], dtype=float)
    q_test = np.array([test_sessions[s]["items_administered"] for s in shared], dtype=float)
    d_ref = np.array([ref_sessions[s]["modelled_duration_minutes"] for s in shared])
    d_test = np.array([test_sessions[s]["modelled_duration_minutes"] for s in shared])

    # Held-out AUC/Brier/log-loss, matched item by item so DeLong applies.
    ref_pred = {
        (s, h["item_id"]): h["p_hat"] for s in shared for h in ref_sessions[s]["heldout"]
    }
    test_pred = {
        (s, h["item_id"]): h["p_hat"] for s in shared for h in test_sessions[s]["heldout"]
    }
    labels = {(s, h["item_id"]): h["y"] for s in shared for h in ref_sessions[s]["heldout"]}
    matched = sorted(ref_pred.keys() & test_pred.keys())
    p_ref = np.array([ref_pred[k] for k in matched])
    p_test = np.array([test_pred[k] for k in matched])
    y = np.array([labels[k] for k in matched])

    pareto = float(
        np.mean(
            [
                (test_sessions[s]["items_administered"] < ref_sessions[s]["items_administered"])
                for s in shared
            ]
        )
    )

    return {
        "reference_arm": ref_name,
        "test_arm": test_name,
        "n_pairs": len(keys),
        "n_sessions": len(shared),
        "between_arm_theta_correlation": round(correlation, 5),
        "primary_endpoint": {
            "metric": "exact-level accuracy, COMMON band scale",
            "accuracy_reference": round(float(ref_correct.mean()), 5),
            "accuracy_test": round(float(test_correct.mean()), 5),
            "degradation_pp": round(100.0 * float(difference.mean()), 4),
            "upper_bound_pp_clustered_bca": round(100.0 * boot.upper, 4),
            "margin_pp": LEVEL_MARGIN_PP,
            "non_inferior": bool(100.0 * boot.upper < LEVEL_MARGIN_PP),
            "tango_upper_bound_pp": tango["upper_bound_pp"],
            "discordance_psi": tango["discordance_psi"],
            "discordant_pairs": tango["discordant_pairs"],
            "degradation_pp_by_family": by_family,
        },
        "theta_rmse": {
            "reference": round(rmse_ref, 5),
            "test": round(rmse_test, 5),
            "relative_degradation": round((rmse_test - rmse_ref) / rmse_ref, 5) if rmse_ref else None,
            "margin": RMSE_RELATIVE_MARGIN,
            "paired_squared_error_difference_upper": round(float(rmse_boot.upper), 6),
            "cohens_d_paired": round(stats.cohens_d_paired(squared_difference), 4),
        },
        "efficiency": {
            "questions_reference_mean": round(float(q_ref.mean()), 4),
            "questions_test_mean": round(float(q_test.mean()), 4),
            "question_reduction": round(1.0 - float(q_test.mean() / q_ref.mean()), 5),
            "target": QUESTION_REDUCTION_TARGET,
            "cliffs_delta": round(stats.cliffs_delta(q_test, q_ref), 4),
            "duration_reduction": round(1.0 - float(d_test.mean() / d_ref.mean()), 5),
            "pareto_shorter_rate": round(pareto, 5),
        },
        "prediction": (
            {
                **stats.delong_auc_difference(p_ref, p_test, y),
                "brier_reference": round(stats.brier(p_ref, y), 5),
                "brier_test": round(stats.brier(p_test, y), 5),
                "log_loss_reference": round(stats.log_loss(p_ref, y), 5),
                "log_loss_test": round(stats.log_loss(p_test, y), 5),
                "n_heldout": int(y.size),
            }
            if y.size
            else {}
        ),
    }


# --- DAG safety, verified against the cohort truth --------------------------------------


def dag_safety(records: list[dict], cohort: Cohort) -> dict:
    """C-DAG-01/03/04/05/11 computed against known node truth.

    In production these need forced verification questions, and A1 shows that costs
    ~100-155 verified events per gate even at a true rate of zero. In simulation the truth
    is in the cohort file, so every inference and every block is verified at no cost. That
    is the strongest reason to run the simulation layer before the pilot, not after it.
    """
    truth = {s.simulee_id: s.nodes for s in cohort.simulees}
    parents: dict[str, list[str]] = defaultdict(list)
    for parent, child in cohort.true_prerequisites:
        parents[child].append(parent)

    inferred_total = inferred_wrong = 0
    blocked_total = blocked_false = 0
    should_block_total = should_block_missed = 0
    duplicate_events = 0
    sessions_with_inference = 0
    inferred_by_source = {s: {"n": 0, "wrong": 0} for s in ("enforced", "shadow", "preview")}
    blocked_by_source = {s: {"n": 0, "wrong": 0} for s in ("enforced", "shadow", "preview")}

    for record in records:
        graph = record.get("graph") or {}
        nodes = truth.get(record["simulee_id"], {})
        if not nodes:
            continue

        # THREE SOURCES, NEVER POOLED. Each answers a different question, and an arm
        # usually has evidence from exactly one of them:
        #
        #   enforced  what the deployment ACTED on. Empty in C-shipped and C-hybrid,
        #             which hold the switches off by definition.
        #   shadow    what the graph CONCLUDED under the live edge set without acting.
        #             Also empty in C-shipped — not because of the enforcement gate but
        #             because the AIE edges all ship `unvalidated` and are inert, so the
        #             traversal draws nothing to mirror.
        #   preview   what the graph WOULD conclude if the unvalidated edges were
        #             trusted. The only source with any events at all on the shipped
        #             configuration: 30 inferences per 20 sessions where the other two
        #             record zero.
        #
        # Coalescing them would compute a wrong-inference rate that means "acted on" for
        # one arm and "hypothetically, on edges nobody has validated" for another, and
        # report both in one column. The rates are computed per source and the source is
        # named; §8's stratification rules say which one a given claim may cite.
        for source, key in (
            ("enforced", "inferred_mastered"),
            ("shadow", "shadow_inferred_mastered"),
            ("preview", "preview_inferred"),
        ):
            for node in list(graph.get(key) or []):
                if node not in nodes:
                    continue
                inferred_by_source[source]["n"] += 1
                inferred_by_source[source]["wrong"] += int(nodes[node] == 0)

        # The headline numerator: what the deployment acted on, falling back to what it
        # concluded. Preview is deliberately NOT folded in — it is reported beside this,
        # never inside it.
        inferred = list(
            graph.get("inferred_mastered") or graph.get("shadow_inferred_mastered") or []
        )
        if inferred:
            sessions_with_inference += 1
        for node in inferred:
            if node in nodes:
                inferred_total += 1
                inferred_wrong += int(nodes[node] == 0)

        for source, key in (
            ("enforced", "blocked"),
            ("shadow", "shadow_blocked"),
            ("preview", "preview_blocked"),
        ):
            for node in list(graph.get(key) or []):
                if node not in nodes:
                    continue
                blocked_by_source[source]["n"] += 1
                blocked_by_source[source]["wrong"] += int(nodes[node] == 1)

        blocked = list(graph.get("blocked") or graph.get("shadow_blocked") or [])
        for node in blocked:
            if node in nodes:
                blocked_total += 1
                # A false block: the graph declared the node unreachable and the candidate
                # actually has it.
                blocked_false += int(nodes[node] == 1)

        # Missed blocking: a node whose TRUE prerequisite the candidate lacks, that the
        # engine measured directly anyway without blocking it.
        measured = set(graph.get("direct_mastered") or []) | set(
            graph.get("direct_not_mastered") or []
        )
        for node in measured:
            if any(nodes.get(p, 1) == 0 for p in parents.get(node, ())):
                should_block_total += 1
                should_block_missed += int(node not in blocked)

        # The ledger is supposed to make a duplicate impossible; measuring it is how the
        # claim stops being an assertion. A duplicate shows up as the same evidence id
        # recorded against a node more than once.
        duplicate_events += max(
            0,
            int(graph.get("direct_evidence_id_count", 0))
            - int(graph.get("unique_direct_evidence_ids", 0)),
        )

    def rate(k: int, n: int) -> dict:
        interval = stats.clopper_pearson(k, n)
        return {
            "events": k,
            "verified": n,
            "rate": round(interval.point, 5) if n else None,
            "upper_95": round(stats.clopper_pearson_upper(k, n), 5) if n else None,
        }

    # U-02 INTERVAL NON-COVERAGE, formerly reported as "premature convergence".
    #
    # It counts converged competencies whose own 95% credible interval excludes the truth.
    # That is a coverage statistic, and naming it premature stopping was wrong twice over:
    # it measures calibration rather than stopping, and it was gated at <2% when a
    # perfectly calibrated 95% interval excludes the truth 5% of the time by construction.
    # No well-calibrated estimator could ever have passed.
    #
    # A real premature-stop metric needs a counterfactual continuation — force k more
    # items past the trigger and count band changes — which the harness does not run.
    # Reported as absent rather than approximated by this.
    converged = premature = 0
    over_converged = over_convergence_denominator = 0
    for record in records:
        for variable in record["variables"]:
            if not variable["converged"]:
                continue
            converged += 1
            interval = variable.get("credible_interval_95") or []
            if len(interval) == 2 and not (interval[0] <= variable["true_theta"] <= interval[1]):
                premature += 1

            # C-DAG-15 OVER-CONVERGENCE. A main that claimed convergence while the graph
            # still had required sub-competencies unmeasured, or with nodes waived.
            # Only counted where a coverage gate exists. On an arm without one there is
            # no such thing as converging past it, and folding those variables into the
            # denominator dilutes the rate for the arms that do have the gate.
            coverage = variable.get("graph_coverage_satisfied")
            if coverage is None:
                continue
            over_convergence_denominator += 1
            if not coverage:
                over_converged += 1
            elif (record.get("graph") or {}).get("waived", {}).get(variable["variable"]):
                over_converged += 1

    return {
        "sessions": len(records),
        "sessions_with_inference": sessions_with_inference,
        "U-02_interval_non_coverage": rate(premature, converged),
        "C-DAG-15_over_convergence": rate(over_converged, over_convergence_denominator),
        "C-DAG-03_wrong_inference": rate(inferred_wrong, inferred_total),
        "C-DAG-01_inference_precision": (
            round(1.0 - inferred_wrong / inferred_total, 5) if inferred_total else None
        ),
        "C-DAG-04_false_blocking": rate(blocked_false, blocked_total),
        "C-DAG-05_missed_blocking": rate(should_block_missed, should_block_total),
        "C-DAG-11_duplicate_evidence": rate(duplicate_events, max(1, sum(
            (r.get("graph") or {}).get("processed_evidence_ids", 0) for r in records
        ))),
        # Which evidence the headline rate above is actually made of. Named rather than
        # implied: on C-shipped it is preview-only, and a reader has to know that before
        # comparing it with C-full's enforced number.
        "wrong_inference_source": _dominant_source(inferred_by_source),
        "false_blocking_source": _dominant_source(blocked_by_source),
        "wrong_inference_by_source": {
            s: rate(v["wrong"], v["n"]) for s, v in inferred_by_source.items()
        },
        "false_blocking_by_source": {
            s: rate(v["wrong"], v["n"]) for s, v in blocked_by_source.items()
        },
        # C-DAG-03d / C-DAG-03e. Depth-1 inference (the immediate parent) and depth-4
        # inference (three decayed hops) are different mechanisms; run 3 reported them as
        # one number. Per-edge is the second stratification, and the one that supports a
        # remedy — an edge with a bad rate can be removed from the allowlist, whereas a
        # bad pooled rate can only turn the whole feature off.
        **_stratified_inference(records, truth),
    }


def _dominant_source(by_source: dict[str, dict]) -> str:
    """Which source the HEADLINE rate is made of.

    Only `enforced` and `shadow` are candidates, in that order, because those are what
    the headline reads. Preview is never the answer here even when it is the only source
    with events — a rate computed from edges nobody validated is not the same claim, and
    labelling it as though it were would let it be compared with C-full's enforced number.
    "none" is the honest answer for the shipped configuration: it concludes nothing, so
    there is nothing to be right or wrong about, and `wrong_inference_by_source` carries
    the preview figure separately for anyone who wants the hypothetical.
    """
    for source in ("enforced", "shadow"):
        if by_source[source]["n"]:
            return source
    return "none"


def _stratified_inference(records: list[dict], truth: dict[str, dict]) -> dict:
    """Wrong-inference rate by propagation distance and by traversed edge.

    Reads `graph.inferred_records`, which carries the distance and the edge path the
    traversal used. Those were computed and discarded before this branch, which is why
    the pre-registration's claim that this analysis "costs nothing, the data already
    exists" had to be withdrawn (amendment 13.1) — and why run 3 cannot be re-stratified.
    """
    by_depth: dict[int, dict] = defaultdict(lambda: {"n": 0, "wrong": 0})
    by_edge: dict[tuple[str, str], dict] = defaultdict(lambda: {"n": 0, "wrong": 0})

    for record in records:
        nodes = truth.get(record["simulee_id"], {})
        if not nodes:
            continue
        for node, provenance in ((record.get("graph") or {}).get("inferred_records") or {}).items():
            if node not in nodes:
                continue
            wrong = int(nodes[node] == 0)
            depth = provenance.get("distance")
            if depth is not None:
                by_depth[int(depth)]["n"] += 1
                by_depth[int(depth)]["wrong"] += wrong
            # Blame every edge on the path. An inference that traversed three edges is
            # evidence about all three: the analysis cannot say which one was wrong from
            # one observation, and attributing it only to the first or the last would
            # invent a precision the data does not have. Aggregated over many
            # inferences, a genuinely bad edge separates from the ones it shares paths with.
            path = provenance.get("edge_path") or []
            for parent, child in zip(path[1:], path):
                by_edge[(parent, child)]["n"] += 1
                by_edge[(parent, child)]["wrong"] += wrong

    def block(counts: dict) -> dict:
        return {
            "events": counts["wrong"],
            "verified": counts["n"],
            "rate": round(counts["wrong"] / counts["n"], 5) if counts["n"] else None,
            "upper_95": (
                round(stats.clopper_pearson_upper(counts["wrong"], counts["n"]), 5)
                if counts["n"]
                else None
            ),
        }

    return {
        "C-DAG-03d_wrong_inference_by_depth": {
            str(depth): block(counts) for depth, counts in sorted(by_depth.items())
        },
        "C-DAG-03e_wrong_inference_by_edge": {
            f"{parent}->{child}": block(counts)
            for (parent, child), counts in sorted(by_edge.items())
        },
    }


SAFETY_KEYS = {
    "C-DAG-03": "C-DAG-03_wrong_inference",
    "C-DAG-04": "C-DAG-04_false_blocking",
    "C-DAG-05": "C-DAG-05_missed_blocking",
    "C-DAG-11": "C-DAG-11_duplicate_evidence",
    "U-02": "U-02_interval_non_coverage",
    "C-DAG-15": "C-DAG-15_over_convergence",
}


def safety_verdicts(safety: dict) -> list[dict]:
    rows = []
    for gate in gate_defs.SAFETY:
        block = safety.get(SAFETY_KEYS.get(gate.gate_id, ""), {})
        # Tier 1 gates are counts with no sampling error; tier 3 gates are upper bounds.
        # Judging a deterministic invariant on a confidence bound would fail a clean run.
        value = block.get("events") if gate.tier == 1 else block.get("upper_95")
        rows.append(
            {
                "gate": gate.gate_id,
                "metric": gate.metric,
                "rule": gate.rule,
                "tier": gate.tier,
                "upper_bound_95": block.get("upper_95"),
                "value": value,
                "verdict": (
                    "DESCRIPTIVE" if gate.tier == 4 else gate_defs.verdict(gate, value)
                ),
            }
        )
    return rows


# --- power (B1) --------------------------------------------------------------------------


def power_summary(psi: float) -> dict:
    """What the observed discordance implies about the sample size the study needs."""
    return {
        "observed_discordance_psi": round(psi, 5),
        "n_for_2pp_80pct_power": stats.n_for_paired_binary(0.02, psi, 0.80),
        "n_for_2pp_90pct_power": stats.n_for_paired_binary(0.02, psi, 0.90),
        "detectable_margin_pp_at_n": {
            str(n): round(100.0 * stats.detectable_margin(n, psi), 3)
            for n in (500, 2000, 4000, 5000)
        },
    }


# --- report -------------------------------------------------------------------------------


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}" if abs(value) < 1000 else f"{value:.1f}"
    return str(value)


def markdown_report(result: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# Approach B vs Approach C — evaluation results")
    add("")
    add(f"Cohort size per arm: {result['cohort_size']} simulees "
        f"({result['n_pairs_per_arm']} (candidate, main) units).")
    add("")
    add("Gate tiers follow the validation document's B4: one primary endpoint at "
        "alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. "
        f"At {result['gate_count_if_untiered']} untiered statistical gates the chance of a "
        f"spurious failure would be {100 * gate_defs.family_wise_error(result['gate_count_if_untiered']):.1f}%.")
    add("")

    # 1. the null arm first
    add("## 1. DGP-0 — the null control")
    add("")
    add("No prerequisite structure exists in this data. Any Approach C benefit here is an "
        "artefact of the generator, not of the graph.")
    add("")
    null = result["comparisons"].get("DGP-0", {})
    if null:
        add("| contrast | level accuracy ref | level accuracy test | degradation (pp) | questions ref | questions test | reduction |")
        add("|---|---:|---:|---:|---:|---:|---:|")
        for name, row in null.items():
            if "error" in row:
                continue
            p, e = row["primary_endpoint"], row["efficiency"]
            add(f"| {name} | {p['accuracy_reference']:.4f} | {p['accuracy_test']:.4f} | "
                f"{p['degradation_pp']:+.2f} | {e['questions_reference_mean']:.2f} | "
                f"{e['questions_test_mean']:.2f} | {e['question_reduction']:+.3f} |")
    add("")

    # 2. absolute gates
    add("## 2. Absolute quality gates (per arm, no comparison)")
    add("")
    add("The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C "
        "relative, so both approaches can pass by being equally unfit. These apply to "
        "whichever approach ships.")
    add("")
    for dgp, per_arm in sorted(result["absolute"].items()):
        add(f"### {dgp}")
        add("")
        add("| gate | rule | " + " | ".join(sorted(per_arm)) + " |")
        add("|---|---|" + "---:|" * len(per_arm))
        arms = sorted(per_arm)
        for i, gate in enumerate(gate_defs.ABSOLUTE):
            cells = []
            for arm in arms:
                row = per_arm[arm]["verdicts"][i]
                cells.append(f"{_fmt(row['value'])} {row['verdict']}")
            add(f"| {gate.gate_id} | {gate.rule} | " + " | ".join(cells) + " |")
        add("")

    # 3. primary endpoint
    add("## 3. Primary endpoint — exact-level accuracy, common band scale")
    add("")
    add("| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |")
    add("|---|---|---:|---:|---:|---:|---:|---|---:|---:|")
    for dgp, per_contrast in sorted(result["comparisons"].items()):
        for name, row in per_contrast.items():
            if "error" in row:
                continue
            p = row["primary_endpoint"]
            add(f"| {dgp} | {name} | {p['accuracy_reference']:.4f} | {p['accuracy_test']:.4f} | "
                f"{p['degradation_pp']:+.2f} | {p['upper_bound_pp_clustered_bca']:+.2f} | "
                f"{p['margin_pp']:.1f} | {'NON-INFERIOR' if p['non_inferior'] else 'NOT SHOWN'} | "
                f"{p['discordance_psi']:.3f} | {row['between_arm_theta_correlation']:.3f} |")
    add("")

    # 4. safety
    add("## 4. DAG safety — one-sided 95% upper bounds")
    add("")
    add("Verified against the cohort's known node truth, so every inference and every block "
        "is checked. A live study would need ~100-155 verified events per gate even at a "
        "true rate of zero (validation A1).")
    add("")
    for dgp, per_arm in sorted(result["safety"].items()):
        for arm, block in sorted(per_arm.items()):
            add(f"### {dgp} / {arm}")
            add("")
            add("| gate | tier | events / verified | rate | 95% UCB | rule | verdict |")
            add("|---|---:|---:|---:|---:|---|---|")
            for row, gate in zip(block["verdicts"], gate_defs.SAFETY):
                raw = block["raw"].get(SAFETY_KEYS.get(gate.gate_id, ""), {})
                add(f"| {row['gate']} | {gate.tier} | {raw.get('events', '—')} / "
                    f"{raw.get('verified', '—')} | {_fmt(raw.get('rate'))} | "
                    f"{_fmt(row['upper_bound_95'])} | {gate.rule} | {row['verdict']} |")
            add("")

    # 5. descriptive
    add("## 5. Descriptive — reported with no verdict")
    add("")
    for dgp, per_arm in sorted(result["absolute"].items()):
        add(f"### {dgp}")
        add("")
        arms = sorted(per_arm)
        add("| metric | " + " | ".join(arms) + " |")
        add("|---|" + "---:|" * len(arms))
        for metric in gate_defs.DESCRIPTIVE_METRICS:
            key = {
                "theta_rmse": "theta_rmse",
                "theta_mae": "theta_mae",
                "within_one_level": "within_one_level_common",
                "questions_per_candidate": "questions_per_candidate_mean",
                "duration_minutes": "duration_minutes_p50",
                "final_se_median": "final_se_median",
                "interval_coverage_95": "interval_coverage_95",
                "heldout_auc": "heldout_auc",
                "heldout_brier": "heldout_brier",
                "heldout_log_loss": "heldout_log_loss",
                "heldout_ece": "heldout_ece",
                "information_realization_ratio": "information_realization_ratio",
                "modality_blueprint_compliance": "modality_blueprint_compliance",
                "coverage_satisfied_rate": "coverage_satisfied_rate",
            }.get(metric)
            if key is None:
                continue
            add(f"| {metric} | " + " | ".join(_fmt(per_arm[a]["metrics"].get(key)) for a in arms) + " |")
        add("")

    # 6. power
    add("## 6. Power — what n this study actually needs")
    add("")
    add("| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for name, row in sorted(result["power"].items()):
        d = row["detectable_margin_pp_at_n"]
        add(f"| {name} | {row['observed_discordance_psi']:.4f} | {row['n_for_2pp_80pct_power']} | "
            f"{row['n_for_2pp_90pct_power']} | {d['500']:.2f}pp | {d['2000']:.2f}pp | {d['4000']:.2f}pp |")
    add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default="eval-results/runs")
    parser.add_argument("--cohorts", default="eval-results/cohorts")
    parser.add_argument("--out", default="eval-results/report")
    parser.add_argument(
        "--contrasts",
        nargs="*",
        default=["C-off:C-full", "C-off:C-shipped", "B:C-off", "B:C-full"],
        help="reference:test pairs",
    )
    args = parser.parse_args()

    runs = load_runs(Path(args.runs))
    cohorts = load_cohorts(Path(args.cohorts))
    if not runs:
        raise SystemExit(f"no run files in {args.runs}")

    frames = {key: Frame(records) for key, records in runs.items()}
    dgps = sorted({dgp for _arm, dgp in runs})

    absolute: dict[str, dict] = {}
    comparisons: dict[str, dict] = {}
    safety: dict[str, dict] = {}
    power: dict[str, dict] = {}

    for dgp in dgps:
        arms = sorted(arm for arm, d in runs if d == dgp)
        absolute[dgp] = {}
        for arm in arms:
            metrics = absolute_metrics(frames[(arm, dgp)])
            absolute[dgp][arm] = {"metrics": metrics, "verdicts": absolute_verdicts(metrics)}

        comparisons[dgp] = {}
        for contrast in args.contrasts:
            ref_name, test_name = contrast.split(":")
            if (ref_name, dgp) not in frames or (test_name, dgp) not in frames:
                continue
            row = compare(
                frames[(ref_name, dgp)],
                frames[(test_name, dgp)],
                ref_name=ref_name,
                test_name=test_name,
            )
            comparisons[dgp][contrast] = row
            if "primary_endpoint" in row:
                power[f"{dgp} {contrast}"] = power_summary(row["primary_endpoint"]["discordance_psi"])

        cohort = cohorts.get(dgp)
        if cohort is not None:
            safety[dgp] = {}
            for arm in arms:
                # Approach B too: S-02 (premature convergence) is not a DAG metric, it is a
                # stopping-rule metric, and the plan gates it for both approaches. The
                # graph rows simply come back unmeasured for an arm with no graph.
                raw = dag_safety(runs[(arm, dgp)], cohort)
                safety[dgp][arm] = {"raw": raw, "verdicts": safety_verdicts(raw)}

    any_frame = next(iter(frames.values()))
    result = {
        "cohort_size": len(any_frame.sessions()),
        "n_pairs_per_arm": len(any_frame.keys),
        "gate_count_if_untiered": 18,
        "arms": sorted({arm for arm, _ in runs}),
        "dgp_arms": dgps,
        "absolute": absolute,
        "comparisons": comparisons,
        "safety": safety,
        "power": power,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
    (out / "report.md").write_text(markdown_report(result), encoding="utf-8")
    print(f"wrote {out/'results.json'} and {out/'report.md'}")


if __name__ == "__main__":
    main()
