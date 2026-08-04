#!/usr/bin/env python3
"""Phase 0a — the configuration pre-study. Band count x stopping rule.

    python -m evaluation.prestudy --cohort <cohort.json> --out eval-results/prestudy

WHY THIS RUNS BEFORE THE B/C EXPERIMENT AND NOT INSIDE IT

Section 6 freezes level boundaries and convergence defaults so only the adaptation
architecture varies. That is correct hygiene for isolating the DAG, and it is exactly what
makes the experiment blind to the two changes that dominate it: the validation document's
A7 puts the band-count change at +14.8pp of decision accuracy for zero extra items, which
is larger than any plausible DAG benefit and free.

Un-freezing them inside the B/C experiment would confound it. So they are varied HERE,
once, cheaply, with no graph and no candidates — and whatever wins gets frozen into the
main experiment's configuration. Running B versus C first risks spending a quarter proving
a 15% question reduction on a configuration a two-day simulation would have improved by
more.

WHY THIS DOES NOT USE THE ORCHESTRATOR

Two reasons, and the second is a finding.

    1. The pre-study needs to vary the band cut points, which are module constants in
       `adaptive.irt`, not settings. Patching a constant under a running orchestrator is
       the kind of test that passes for reasons nobody can reconstruct.
    2. `cat_band_probability_stop_enabled` cannot fire through the orchestrator at all.
       `convergence.evaluate` accepts a `band_probability` argument and implements the
       rule; `variables.evaluate_finalisation` — the orchestrator's only caller — never
       passes it, so the parameter defaults to None and the rule is unreachable. The flag
       is live, documented, and dead. Measuring the rule therefore requires calling
       `convergence.evaluate` directly, which is what this file does.

So this is a single-competency CAT loop built from the engine's own `irt`, `outcome` and
`convergence` functions: same 3PL, same posterior update, same stopping rules, same bank.
Selection is expected Fisher information over the posterior, which is the criterion the
real picker falls back to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

from evaluation import responder, stats  # noqa: E402
from evaluation.bands import band_of, cuts_for  # noqa: E402
from evaluation.dgp import Cohort  # noqa: E402

SE_TARGET = 0.55
BAND_PROBABILITY_TARGET = 0.80
MIN_QUESTIONS = 6
MAX_QUESTIONS = 12

BAND_COUNTS = (4, 5, 8)
STOP_RULES = ("se", "band_probability")


def _band_probabilities(posterior: np.ndarray, grid: np.ndarray, cuts) -> dict[int, float]:
    levels = np.digitize(grid, cuts) + 1
    normalised = posterior / posterior.sum()
    return {int(v): float(normalised[levels == v].sum()) for v in sorted(set(levels.tolist()))}


def information_matrix(pool) -> np.ndarray:
    """Fisher information of every item at every grid point: shape (items, grid).

    Precomputed once per competency, so choosing an item is one matrix-vector product
    instead of `len(pool) * len(THETA_GRID)` Python-level calls. The engine's
    `expected_fisher_information` does the latter, which is right for one live session and
    ruinous for a pre-study that runs tens of thousands.
    """
    from app.services.adaptive.irt import THETA_GRID, fisher_information

    return np.array(
        [
            [fisher_information(float(t), i.cat.a, i.cat.b, i.cat.c) for t in THETA_GRID]
            for i in pool
        ]
    )


def run_one(*, simulee, main, pool, information, cuts, stop_rule):
    """One single-competency CAT session under one configuration."""
    from app.services.adaptive.irt import THETA_GRID, uniform_prior
    from app.services.orchestrator.outcome import graded_posterior_update

    posterior = uniform_prior()
    theta_hat = 0.0
    se = 2.0
    questions = 0
    available = np.ones(len(pool), dtype=bool)

    for _ in range(MAX_QUESTIONS):
        if not available.any():
            break
        # Expected Fisher information over the posterior — the criterion the real picker
        # falls back to — for every remaining item at once.
        expected = information @ posterior
        expected[~available] = -np.inf
        index = int(np.argmax(expected))
        item = pool[index]
        available[index] = False
        plan = responder.plan_for(simulee, item, main)
        # One scale for every modality, as the engine does: an MCQ is a 0/1 score at full
        # weight, a rubric-graded answer is its continuous score.
        score = float(plan.correct) if item.modality == "mcq" else float(plan.score)
        posterior, theta_hat, se = graded_posterior_update(
            posterior, item.cat.a, item.cat.b, item.cat.c, score, 1.0
        )
        questions += 1

        if questions < MIN_QUESTIONS:
            continue
        if stop_rule == "se" and se <= SE_TARGET:
            break
        if stop_rule == "band_probability":
            probabilities = _band_probabilities(posterior, THETA_GRID, cuts)
            level = band_of(theta_hat, cuts)
            if probabilities.get(level, 0.0) >= BAND_PROBABILITY_TARGET:
                break

    probabilities = _band_probabilities(posterior, THETA_GRID, cuts)
    return {
        "theta_hat": theta_hat,
        "se": se,
        "questions": questions,
        "level": band_of(theta_hat, cuts),
        "true_level": band_of(float(simulee.theta[main]), cuts),
        "band_probabilities": {str(k): v for k, v in probabilities.items()},
    }


def spread_theta(simulees, seed: int = 20260804):
    """Re-draw each simulee's true ability UNIFORMLY over the scale.

    THE BUG THIS FIXES, AND IT WAS MINE.

    The cohort places true ability at eight fixed strata: -2.8, -2.0, ..., +2.8. Measured
    against each scale's cut points, those strata are not neutral:

        5 bands (cuts -2.4, -0.8, 0.8, 2.4)   every stratum exactly 0.40 from a cut
        4 bands (cuts -2.0, 0.0, 2.0)         mean 0.50, two strata ON a cut
        8 bands (cuts -3 .. 3)                mean 0.20, two strata ON a cut

    An estimator with SE 0.55 misclassifies a candidate sitting on a cut point about half
    the time and one sitting 0.4 away far less often. So the first version of this
    pre-study compared band counts on a cohort constructed to favour the 5-band scale and
    to punish the 8-band one, and reported the difference as a property of band width. It
    gave 8 bands 0.2917 exact accuracy where an unbiased estimator at that SE gives about
    0.58 — and reported 0.980 within-one alongside it, which cannot come from the same
    estimator and was the tell.

    Drawing ability uniformly removes the alignment: no scale's cut points are privileged,
    because there is no discrete truth for them to be privileged against.

    NODE TRUTH IS DROPPED, and that matters. Node mastery was generated against the
    simulee's ORIGINAL ability, and `responder` shifts the response probability by
    +/-0.8 logits according to it. Re-drawing ability while keeping the old node map
    therefore detaches the two: a candidate moved from -2.8 to +3.0 keeps the unmastered
    nodes of a weak candidate and answers 0.8 logits below their stated ability, every
    item, in one direction. The first uniform run did exactly that and produced a shifted
    diagonal — 44-63% of candidates reported one band LOW against 0.6-1.3% reported high.
    That is a bias, not imprecision, and it would have been read as a property of the
    band scale.

    Dropping the map is the right fix rather than regenerating it: this pre-study is
    explicitly the no-graph configuration, so responses should come from the 3PL alone.
    """
    import copy

    rng = np.random.default_rng(seed)
    spread = []
    for simulee in simulees:
        clone = copy.deepcopy(simulee)
        offset = float(rng.uniform(-3.2, 3.2))
        base = float(np.mean(list(simulee.theta.values())))
        clone.theta = {main: value - base + offset for main, value in simulee.theta.items()}
        clone.nodes = {}
        spread.append(clone)
    return spread


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--out", default="eval-results/prestudy")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--bank", default="AIE")
    parser.add_argument(
        "--strata",
        action="store_true",
        help="keep the cohort's eight fixed ability strata (reproduces the confounded run)",
    )
    parser.add_argument(
        "--nodes",
        choices=("consistent", "dropped"),
        default=None,
        help=(
            "node-level mastery. 'consistent' keeps it (responses vary WITHIN a "
            "competency, so one theta is a summary of several skills); 'dropped' removes "
            "it (responses come from the 3PL alone). Defaults to consistent with "
            "--strata and dropped otherwise, because re-drawing ability detaches a node "
            "map generated against the old ability and biases every response one way."
        ),
    )
    args = parser.parse_args()

    from app.services.orchestrator import registry

    bank = registry.get_bank(registry.resolve_bank_id(args.bank))
    cohort = Cohort.load(args.cohort)
    simulees = cohort.simulees[: args.limit]
    nodes_mode = args.nodes or ("consistent" if args.strata else "dropped")
    if not args.strata:
        if nodes_mode == "consistent":
            raise SystemExit(
                "--nodes consistent needs --strata: re-drawing ability detaches the node "
                "map it was generated from, which biases every response downward."
            )
        simulees = spread_theta(simulees)
    elif nodes_mode == "dropped":
        import copy

        stripped = []
        for simulee in simulees:
            clone = copy.deepcopy(simulee)
            clone.nodes = {}
            stripped.append(clone)
        simulees = stripped
    mains = [m for m in cohort.mains if m in set(bank.variables())]
    pools = {m: bank.shortlist(m, exclude=set()) for m in mains}
    # One information matrix per competency, shared by every configuration and every
    # simulee: the bank does not change between them.
    informations = {m: information_matrix(pools[m]) for m in mains}

    rows = []
    for n_bands in BAND_COUNTS:
        cuts = cuts_for(n_bands)
        for stop_rule in STOP_RULES:
            correct: list[bool] = []
            within_one: list[bool] = []
            questions: list[int] = []
            thetas: list[float] = []
            ses: list[float] = []
            band_rows: list[dict] = []
            confusion: dict[tuple[int, int], int] = {}
            for simulee in simulees:
                for main in mains:
                    result = run_one(
                        simulee=simulee,
                        main=main,
                        pool=pools[main],
                        information=informations[main],
                        cuts=cuts,
                        stop_rule=stop_rule,
                    )
                    confusion[(result["true_level"], result["level"])] = (
                        confusion.get((result["true_level"], result["level"]), 0) + 1
                    )
                    correct.append(result["level"] == result["true_level"])
                    within_one.append(abs(result["level"] - result["true_level"]) <= 1)
                    questions.append(result["questions"])
                    thetas.append(result["theta_hat"])
                    ses.append(result["se"])
                    band_rows.append(result["band_probabilities"])

            rows.append(
                {
                    "n_bands": n_bands,
                    "band_width": round(8.0 / n_bands, 3),
                    "stop_rule": stop_rule,
                    "exact_level_accuracy": round(float(np.mean(correct)), 5),
                    "within_one_level": round(float(np.mean(within_one)), 5),
                    "mean_questions": round(float(np.mean(questions)), 4),
                    "median_questions": float(np.median(questions)),
                    "marginal_reliability": round(
                        stats.marginal_reliability(np.array(thetas), np.array(ses)), 5
                    ),
                    "decision_consistency": round(stats.decision_consistency(band_rows), 5),
                    "median_se": round(float(np.median(ses)), 5),
                    "n": len(correct),
                    # (true band, reported band) counts. Mass on a SHIFTED diagonal is an
                    # alignment bug; mass spread symmetrically is a measurement result.
                    # This is the diagnostic that found the strata confound.
                    "confusion": {f"{t}->{e}": n for (t, e), n in sorted(confusion.items())},
                }
            )

    baseline = next(r for r in rows if r["n_bands"] == 5 and r["stop_rule"] == "se")
    for row in rows:
        row["accuracy_vs_5band_se_pp"] = round(
            100.0 * (row["exact_level_accuracy"] - baseline["exact_level_accuracy"]), 3
        )
        row["questions_vs_5band_se"] = round(row["mean_questions"] - baseline["mean_questions"], 3)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prestudy.json").write_text(
        json.dumps(
            {
                "cohort": cohort.dgp,
                "n_simulees": len(simulees),
                "ability_distribution": "eight fixed strata" if args.strata else "uniform [-3.2, 3.2]",
                "node_heterogeneity": nodes_mode == "consistent",
                "rows": rows,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    header = (
        f"{'bands':>6} {'width':>6} {'stop rule':>17} {'accuracy':>9} {'within1':>8} "
        f"{'items':>7} {'rel':>7} {'consist':>8} {'d acc pp':>9} {'d items':>8}"
    )
    print(
        f"Phase 0a configuration pre-study — {len(simulees)} simulees x {len(mains)} mains, "
        f"ability {'at eight fixed strata' if args.strata else 'uniform over [-3.2, 3.2]'}, "
        f"node heterogeneity {nodes_mode}"
    )
    print(header)
    print("-" * len(header))
    for row in sorted(rows, key=lambda r: (-r["exact_level_accuracy"],)):
        print(
            f"{row['n_bands']:>6} {row['band_width']:>6.2f} {row['stop_rule']:>17} "
            f"{row['exact_level_accuracy']:>9.4f} {row['within_one_level']:>8.4f} "
            f"{row['mean_questions']:>7.2f} {row['marginal_reliability']:>7.4f} "
            f"{row['decision_consistency']:>8.4f} {row['accuracy_vs_5band_se_pp']:>+9.2f} "
            f"{row['questions_vs_5band_se']:>+8.2f}"
        )
    print(f"\nwrote {out / 'prestudy.json'}")


if __name__ == "__main__":
    main()
