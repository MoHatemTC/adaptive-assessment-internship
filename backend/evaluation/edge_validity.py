#!/usr/bin/env python3
"""Phase 0b — offline prerequisite-edge validity, and the Layer 4 density precondition.

    python -m evaluation.edge_validity --cohort <cohort.json> --runs <runs dir> --out <dir>

TWO CHECKS, BOTH CHEAP, BOTH BEFORE THE MAIN EXPERIMENT

C-DAG-21 — EDGE VALIDITY FROM A LINEAR FORM. `C-DAG-20` measures an edge at runtime, after
it has already affected candidates. The offline version asks the same question of a dense
response matrix — P(pass child | fail parent) — costs nothing, needs no verification arm,
and lets a bad edge be pruned before the experiment rather than priced after it. The
repository already ships `scripts/validate_prerequisite_edges.py`, which computes this from
a corpus of finished sessions; it cannot run before there are sessions. This does the same
arithmetic against the simulated linear form, so the edge set can be judged on day zero.

An edge is INFORMATIVE only where the parent was failed. That is the whole difficulty: a
cohort in which nobody fails the prerequisite carries no evidence about the edge at all,
however many candidates it contains, which is why the count of informative pairs is
reported beside every rate rather than behind it.

A3 — THE REPLAY PRECONDITION. Section 5.4 says a broad response matrix is "preferable".
It is not preferable, it is a hard precondition: to replay an adaptive path you need the
historical candidate to have answered whichever item the CAT selects. This reports the
ON-MATRIX RATE — what share of the adaptive arms' actual selections a linear form of a
given size would have covered — so the required per-candidate coverage is a number rather
than an adjective.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for key, value in {
    "LITELLM_BASE_URL": "http://localhost:4000",
    "LITELLM_API_KEY": "placeholder",
    "LITELLM_MODEL": "placeholder-model",
    "E2B_API_KEY": "placeholder",
}.items():
    os.environ.setdefault(key, value)

import numpy as np  # noqa: E402

from evaluation import responder  # noqa: E402
from evaluation.dgp import Cohort  # noqa: E402
from evaluation.stats import clopper_pearson  # noqa: E402

# The engine's own propagation thresholds. Read as constants rather than from settings so
# the offline check states the bar it applied, whatever an operator has since moved.
STRONG_SUCCESS = 0.80
STRONG_FAILURE = 0.20

# Below this many informative pairs an edge has not been measured, whatever its rate says.
MINIMUM_INFORMATIVE_PAIRS = 100

# An edge may enforce blocking only if a candidate who fails the parent rarely passes the
# child. Set at the false-blocking gate: if more than 2% of blocked descendants would have
# been mastered, enforcement breaches C-DAG-04 by construction.
ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT = 0.02

# How far failing the parent must depress the child's score, on the 0-1 scale, once
# ability is held fixed, before the edge is believed. 0.20 is a fifth of the range — a real
# effect, not a rounding error — and the bar is applied to the one-sided UPPER bound rather
# than to the point estimate.
SCORE_EFFECT_ENABLE_BAR = 0.20

# Above this correlation between "failed the parent" and ability, the prerequisite effect
# is not identified: there is no candidate who fails the parent for a reason other than
# being weak, so nothing distinguishes the edge from the ability it rides on. A coefficient
# estimated through that much collinearity is not evidence in either direction.
COLLINEARITY_CEILING = 0.70


def node_scores(cohort: Cohort, bank) -> dict[str, dict[str, float]]:
    """simulee -> node -> mean simulated score over every item measuring that node.

    The dense linear form, generated rather than collected. Every simulee answers every
    item, which is exactly the calibration matrix item parameters would need anyway — run
    it once, use it twice, as A3 says.
    """
    by_node: dict[str, list] = defaultdict(list)
    for item in bank.all_items():
        if item.status != "active":
            continue
        by_node[responder.primary_node(item)].append(item)

    scores: dict[str, dict[str, float]] = {}
    for simulee in cohort.simulees:
        row: dict[str, float] = {}
        for node, items in by_node.items():
            main = node.split(".", 1)[0]
            if main not in simulee.theta:
                continue
            values = []
            for item in items:
                plan = responder.plan_for(simulee, item, main)
                values.append(float(plan.correct) if item.modality == "mcq" else plan.score)
            if values:
                row[node] = float(np.mean(values))
        scores[simulee.simulee_id] = row
    return scores


def _stratified_contrast(
    theta: np.ndarray, failed_parent: np.ndarray, child_score: np.ndarray, *, bins: int = 8, minimum_cell: int = 15
) -> dict:
    """Mean within-stratum difference in child score, over strata that HAVE both groups.

    The quantity a prerequisite claim actually makes: at the same ability, does failing
    the parent depress the child? Averaged only over ability strata where both a failing
    and a passing group exist, because a stratum with an empty cell contributes no
    comparison — and at low ability there is no passing group at all, since everyone fails
    everything.

    That emptiness is why the first version of this check found nothing. It regressed over
    the whole ability range at once, so the large real effect in the contrast-bearing
    upper strata was averaged against a floor region carrying no information, and real
    edges came back indistinguishable from spurious ones.
    """
    if theta.size < bins * minimum_cell:
        return {"effect": None, "strata_used": 0, "n_compared": 0}

    edges = np.quantile(theta, np.linspace(0.0, 1.0, bins + 1))
    differences: list[float] = []
    weights: list[int] = []
    for i in range(bins):
        low, high = edges[i], edges[i + 1]
        inside = (theta >= low) & (theta <= high if i == bins - 1 else theta < high)
        failing = inside & (failed_parent > 0.5)
        passing = inside & (failed_parent <= 0.5)
        if failing.sum() < minimum_cell or passing.sum() < minimum_cell:
            continue
        differences.append(float(child_score[failing].mean() - child_score[passing].mean()))
        weights.append(int(inside.sum()))

    if not differences:
        return {"effect": None, "strata_used": 0, "n_compared": 0}
    return {
        "effect": round(float(np.average(differences, weights=weights)), 5),
        "strata_used": len(differences),
        "n_compared": int(sum(weights)),
    }


def _ridge_logistic(X: np.ndarray, y: np.ndarray, penalty: float = 1e-3, iterations: int = 60):
    """Ridge-penalised logistic regression by IRLS. Returns (coefficients, standard errors).

    Penalised because separation is the normal case here, not the exception: a candidate
    who fails a genuine prerequisite essentially never passes the dependent skill, so the
    unpenalised coefficient runs to minus infinity and carries no interval. A small ridge
    keeps the estimate finite and the comparison between edges meaningful; it shrinks every
    coefficient toward zero equally, so it cannot manufacture a difference between a real
    edge and a spurious one.
    """
    n, k = X.shape
    beta = np.zeros(k)
    for _ in range(iterations):
        eta = np.clip(X @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1 - p), 1e-6, None)
        gradient = X.T @ (y - p) - penalty * beta
        hessian = (X * w[:, None]).T @ X + penalty * np.eye(k)
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:  # pragma: no cover — singular design
            break
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    eta = np.clip(X @ beta, -30, 30)
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1 - p), 1e-6, None)
    hessian = (X * w[:, None]).T @ X + penalty * np.eye(k)
    try:
        covariance = np.linalg.inv(hessian)
        se = np.sqrt(np.clip(np.diag(covariance), 0, None))
    except np.linalg.LinAlgError:  # pragma: no cover
        se = np.full(k, np.nan)
    return beta, se


def ability_conditioned_effect(
    cohort: Cohort,
    scores: dict[str, dict[str, float]],
    parent: str,
    child: str,
) -> dict:
    """The residual prerequisite effect, after ability is held fixed.

    WHY THE UNCONDITIONED RATE IS NOT A TEST OF AN EDGE

    P(pass child | fail parent) is what the architecture's own
    `validate_prerequisite_edges.py` computes and what the validation document proposes as
    the Phase 0b gate. Measured on the DGP-2 cohort it clears the enable bar for EVERY edge
    the graph asserts — including the eight deliberately removed from the world. It has
    zero discriminating power.

    The reason is ability. A candidate who fails C1.1 is a weak candidate and will probably
    fail C1.2 as well, whether or not C1.1 is a prerequisite for C1.2. Any two nodes under
    one main competency are correlated through theta, so the conditional is near zero for
    every pair in the graph.

    The statistic that does discriminate is the coefficient on "failed the parent" in

        child_score = a + b * theta + c * failed_parent

    A real prerequisite makes `c` strongly negative: failing the parent depresses the child
    BEYOND what ability already explains. A spurious edge leaves `c` near zero, because
    within an ability level the two nodes are conditionally independent.

    Two earlier forms were tried and discarded, both for the same reason — a node score is
    a mean over the ~10 items measuring it, so thresholding it throws away almost
    everything. Stratified cell means left both cells unpopulated inside every ability
    stratum; a logistic model on a pass indicator left too few passes to estimate at all,
    and returned undefined for all thirty edges. Regressing the score itself keeps the
    data.

    Simulation uses true theta. A live study would substitute the CAT estimate, whose
    measurement error attenuates the correction — so the live version is conservative
    rather than exact, and any report using it should say so.
    """
    main = parent.split(".", 1)[0]
    theta: list[float] = []
    failed_parent: list[float] = []
    child_score: list[float] = []
    for simulee in cohort.simulees:
        row = scores.get(simulee.simulee_id, {})
        if parent not in row or child not in row or main not in simulee.theta:
            continue
        theta.append(float(simulee.theta[main]))
        failed_parent.append(float(row[parent] <= STRONG_FAILURE))
        child_score.append(float(row[child]))

    n = len(theta)
    y = np.array(child_score)
    fails = np.array(failed_parent)
    # The outcome is the child's CONTINUOUS score, not a pass indicator. Thresholding it
    # was tried and is unusable: a node score is a mean over the ~10 items measuring it, so
    # requiring >= 0.80 leaves too few passes to estimate anything, and the coefficient
    # came back undefined for all thirty edges. The continuous version asks the same
    # question — does failing the parent depress the child beyond what ability explains —
    # without discarding most of the data to a cut point.
    if n < 100 or fails.sum() < 20 or fails.sum() == n or y.std() < 1e-9:
        return {"coefficient": None, "se": None, "n": n, "upper_95": None}

    # THE CONTRAST-BEARING REGION. Failing a prerequisite is itself mostly a statement
    # about ability: at low theta everyone fails everything, so those candidates carry no
    # information about the edge however many of them there are. The first version of this
    # check regressed over the whole range and found nothing for real edges OR spurious
    # ones — not because the effect was absent, but because it was averaged against a
    # region where the comparison cell is empty.
    #
    # So the estimate is restricted to theta bins holding at least `MINIMUM_CELL` of BOTH
    # groups, and the count of usable bins is reported beside it. An edge measured in two
    # bins is a weaker claim than one measured in six, and hiding that behind a single
    # coefficient is how the first version reached a wrong conclusion.
    theta_array = np.array(theta)
    stratified = _stratified_contrast(theta_array, fails, y)
    # IS THE EFFECT IDENTIFIED AT ALL? Failing a prerequisite is itself mostly a statement
    # about ability: only weak candidates fail. When that indicator is nearly collinear
    # with theta there is no independent variation left to attribute a prerequisite effect
    # to, and the regression will return approximately zero for a real edge and a spurious
    # one alike. Reported rather than silently returned as an estimate — a coefficient of
    # zero from a collinear design is not evidence that the edge is spurious.
    collinearity = abs(float(np.corrcoef(theta_array, fails)[0, 1]))

    X = np.column_stack([np.ones(n), theta_array, fails])
    coefficients, _residuals, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    if rank < X.shape[1]:
        return {
            "coefficient": None,
            "se": None,
            "n": n,
            "upper_95": None,
            "collinearity": round(collinearity, 4),
            "identified": False,
            **{f"stratified_{k}": v for k, v in stratified.items()},
        }

    fitted = X @ coefficients
    sigma_squared = float(np.sum((y - fitted) ** 2) / max(n - X.shape[1], 1))
    covariance = sigma_squared * np.linalg.inv(X.T @ X)
    coefficient = float(coefficients[2])
    standard_error = float(np.sqrt(max(covariance[2, 2], 0.0)))
    return {
        "coefficient": round(coefficient, 4),
        "se": round(standard_error, 4),
        "n": n,
        # One-sided 95% UPPER bound on the effect. An edge is believed only if even the
        # least favourable plausible value is a real suppression.
        "upper_95": round(coefficient + 1.645 * standard_error, 4),
        "collinearity": round(collinearity, 4),
        "identified": bool(collinearity < COLLINEARITY_CEILING),
        # The estimate that survives the empty-cell problem. This, not the pooled
        # regression, is what the verdict reads.
        **{f"stratified_{k}": v for k, v in stratified.items()},
    }


def edge_report(cohort: Cohort, scores: dict[str, dict[str, float]]) -> list[dict]:
    rows = []
    truth = {s.simulee_id: s.nodes for s in cohort.simulees}
    true_edges = set(cohort.true_prerequisites)

    for parent, child in cohort.graph_prerequisites:
        failed_parent = passed_child_given_fail = 0
        passed_child = passed_parent_given_pass_child = 0
        for simulee_id, row in scores.items():
            p, c = row.get(parent), row.get(child)
            if p is None or c is None:
                continue
            if p <= STRONG_FAILURE:
                failed_parent += 1
                passed_child_given_fail += int(c >= STRONG_SUCCESS)
            if c >= STRONG_SUCCESS:
                passed_child += 1
                passed_parent_given_pass_child += int(p >= STRONG_SUCCESS)

        block_interval = clopper_pearson(passed_child_given_fail, failed_parent)
        informative = failed_parent >= MINIMUM_INFORMATIVE_PAIRS
        effect = ability_conditioned_effect(cohort, scores, parent, child)
        # The upper bound, not the point estimate: an edge is enabled on what the data
        # can rule out, not on what it happens to have shown.
        may_block = bool(
            informative and block_interval.upper < ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT
        )
        rows.append(
            {
                "parent": parent,
                "child": child,
                "in_true_structure": (parent, child) in true_edges,
                "n_parent_failures": failed_parent,
                "p_pass_child_given_fail_parent": (
                    round(block_interval.point, 5) if failed_parent else None
                ),
                "upper_95": round(block_interval.upper, 5) if failed_parent else None,
                "n_child_passes": passed_child,
                "p_pass_parent_given_pass_child": (
                    round(passed_parent_given_pass_child / passed_child, 5) if passed_child else None
                ),
                "informative": informative,
                "ability_conditioned_coefficient": effect["coefficient"],
                "ability_conditioned_upper_95": effect["upper_95"],
                "ability_conditioned_n": effect["n"],
                "ability_failure_collinearity": effect.get("collinearity"),
                "ability_conditioned_identified": effect.get("identified"),
                "stratified_effect": effect.get("stratified_effect"),
                "stratified_strata_used": effect.get("stratified_strata_used"),
                "verdict": (
                    "ENABLE BLOCKING"
                    if may_block
                    else ("KEEP INERT — not measured" if not informative else "KEEP INERT — bar not met")
                ),
                # The verdict the unconditioned rate cannot reach. An edge earns blocking
                # only if failing the parent suppresses the child WITHIN an ability
                # stratum; otherwise the apparent dependency is theta, which every pair of
                # nodes under one main shares.
                # Read off the STRATIFIED estimate, over strata that hold both a failing
                # and a passing group. An edge measured in no such stratum is not
                # measured, and saying so is different from saying it is spurious.
                "verdict_ability_conditioned": (
                    "NOT MEASURED — no ability stratum holds both groups"
                    if effect.get("stratified_effect") is None
                    else (
                        "ENABLE BLOCKING"
                        if effect["stratified_effect"] <= -SCORE_EFFECT_ENABLE_BAR
                        else "KEEP INERT — no residual effect beyond ability"
                    )
                ),
            }
        )
    return rows


def on_matrix_rate(runs_dir: Path, bank) -> dict:
    """What share of adaptive selections a linear form of size K would have covered.

    Answers A3 numerically: the replay is only interpretable above an 80% on-matrix rate,
    and this says how large a form that costs on the observed selection distribution.
    """
    served = Counter()
    sessions = 0
    for path in sorted(runs_dir.glob("*.jsonl")):
        for line in path.open(encoding="utf-8"):
            record = json.loads(line)
            served.update(record.get("served_item_ids") or [])
            sessions += 1
    if not served:
        return {}

    total = sum(served.values())
    ordered = [count for _item, count in served.most_common()]
    cumulative = np.cumsum(ordered) / total
    bank_size = len([i for i in bank.all_items() if i.status == "active"])

    def form_size_for(target: float) -> int:
        hit = np.searchsorted(cumulative, target) + 1
        return int(min(hit, len(ordered)))

    return {
        "sessions": sessions,
        "distinct_items_ever_served": len(served),
        "bank_size": bank_size,
        "bank_coverage": round(len(served) / bank_size, 4),
        "on_matrix_rate_by_form_size": {
            str(k): round(float(cumulative[min(k, len(cumulative)) - 1]), 4)
            for k in (10, 20, 30, 40, 60, 80, 100, 150, 200)
            if k <= len(cumulative)
        },
        "form_size_for_80pct_on_matrix": form_size_for(0.80),
        "form_size_for_90pct_on_matrix": form_size_for(0.90),
        "form_size_as_share_of_bank_at_80pct": round(form_size_for(0.80) / bank_size, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True, help="a DGP-1 or DGP-2 cohort")
    parser.add_argument("--runs", default="", help="run directory, for the on-matrix rate")
    parser.add_argument("--out", default="eval-results/edge-validity")
    parser.add_argument("--bank", default="AIE")
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    from app.services.orchestrator import registry

    bank = registry.get_bank(registry.resolve_bank_id(args.bank))
    cohort = Cohort.load(args.cohort)
    cohort.simulees = cohort.simulees[: args.limit]

    scores = node_scores(cohort, bank)
    rows = edge_report(cohort, scores)
    matrix = on_matrix_rate(Path(args.runs), bank) if args.runs else {}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "edge_validity.json").write_text(
        json.dumps(
            {
                "cohort": cohort.dgp,
                "n_simulees": len(cohort.simulees),
                "minimum_informative_pairs": MINIMUM_INFORMATIVE_PAIRS,
                "enable_bar": ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT,
                "edges": rows,
                "replay_precondition": matrix,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    enable = [r for r in rows if r["verdict"] == "ENABLE BLOCKING"]
    enable_conditioned = [r for r in rows if r["verdict_ability_conditioned"] == "ENABLE BLOCKING"]
    unmeasured = [r for r in rows if not r["informative"]]
    real = [r for r in rows if r["in_true_structure"]]
    spurious = [r for r in rows if not r["in_true_structure"]]

    print(f"Phase 0b — offline edge validity on {cohort.dgp}, {len(cohort.simulees)} simulees")
    print(f"  edges asserted by the graph          : {len(rows)}  ({len(real)} real, {len(spurious)} absent from the world)")
    print(f"  edges with >= {MINIMUM_INFORMATIVE_PAIRS} informative pairs     : {len(rows) - len(unmeasured)}")
    print(f"  enabled by P(pass child | fail parent): {len(enable)}")
    print(f"  enabled by ability-conditioned effect : {len(enable_conditioned)}")
    print()
    print(f"  {'parent':>8} {'child':>8} {'real?':>6} {'fails':>7} {'P(pass|fail)':>13} {'UCB':>8} {'effect UB':>10} {'r':>6}  unconditioned -> conditioned")
    for row in sorted(rows, key=lambda r: (r["in_true_structure"], r.get("stratified_effect") or 0)):
        p = row["p_pass_child_given_fail_parent"]
        u = row["upper_95"]
        effect_bound = row.get("stratified_effect")
        collinear = row.get("ability_failure_collinearity")
        conditioned = {
            "ENABLE BLOCKING": "ENABLE",
            "KEEP INERT — no residual effect beyond ability": "inert",
        }.get(row["verdict_ability_conditioned"], "not measured")
        conditioned = f"{conditioned} ({row.get('stratified_strata_used', 0)} strata)"
        print(
            f"  {row['parent']:>8} {row['child']:>8} {str(row['in_true_structure']):>6} "
            f"{row['n_parent_failures']:>7} {('—' if p is None else f'{p:.4f}'):>13} "
            f"{('—' if u is None else f'{u:.4f}'):>8} "
            f"{('—' if effect_bound is None else f'{effect_bound:+.3f}'):>10} "
            f"{('—' if collinear is None else f'{collinear:.2f}'):>6}  "
            f"{'ENABLE' if row['verdict'] == 'ENABLE BLOCKING' else 'inert':>6} -> {conditioned}"
        )

    if spurious:
        caught_raw = sum(1 for r in spurious if r["verdict"] != "ENABLE BLOCKING")
        kept_raw = sum(1 for r in real if r["verdict"] == "ENABLE BLOCKING")
        not_identified = sum(1 for r in rows if r.get("ability_conditioned_identified") is False)
        print()
        print("  Discrimination — can either check tell a real edge from a spurious one?")
        print(f"    unconditioned: {caught_raw}/{len(spurious)} spurious refused, "
              f"{kept_raw}/{len(real)} real retained")
        caught_strat = sum(
            1 for r in spurious if r["verdict_ability_conditioned"] != "ENABLE BLOCKING"
        )
        kept_strat = sum(
            1 for r in real if r["verdict_ability_conditioned"] == "ENABLE BLOCKING"
        )
        unmeasured_strat = sum(
            1 for r in rows if r.get("stratified_effect") is None
        )
        print(f"    ability-stratified : {caught_strat}/{len(spurious)} spurious refused, "
              f"{kept_strat}/{len(real)} real retained "
              f"({unmeasured_strat}/{len(rows)} not measured in any stratum)")
    if matrix:
        print()
        print("  Layer 4 replay precondition (A3):")
        print(f"    distinct items ever served : {matrix['distinct_items_ever_served']} of {matrix['bank_size']}")
        print(f"    linear form for 80% on-matrix : {matrix['form_size_for_80pct_on_matrix']} items "
              f"({100 * matrix['form_size_as_share_of_bank_at_80pct']:.1f}% of the bank)")
        print(f"    linear form for 90% on-matrix : {matrix['form_size_for_90pct_on_matrix']} items")
    print(f"\nwrote {out / 'edge_validity.json'}")


if __name__ == "__main__":
    main()
