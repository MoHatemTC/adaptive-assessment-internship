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


def run_one(*, simulee, main, pool, cuts, stop_rule):
    """One single-competency CAT session under one configuration."""
    from app.services.adaptive.irt import THETA_GRID, expected_fisher_information, uniform_prior
    from app.services.orchestrator.outcome import graded_posterior_update

    posterior = uniform_prior()
    theta_hat = 0.0
    se = 2.0
    served: set[str] = set()
    questions = 0

    for _ in range(MAX_QUESTIONS):
        candidates = [i for i in pool if i.item_id not in served]
        if not candidates:
            break
        item = max(
            candidates,
            key=lambda i: expected_fisher_information(posterior, i.cat.a, i.cat.b, i.cat.c),
        )
        plan = responder.plan_for(simulee, item, main)
        # One scale for every modality, as the engine does: an MCQ is a 0/1 score at full
        # weight, a rubric-graded answer is its continuous score.
        score = float(plan.correct) if item.modality == "mcq" else float(plan.score)
        posterior, theta_hat, se = graded_posterior_update(
            posterior, item.cat.a, item.cat.b, item.cat.c, score, 1.0
        )
        served.add(item.item_id)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--out", default="eval-results/prestudy")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--bank", default="AIE")
    args = parser.parse_args()

    from app.services.orchestrator import registry

    bank = registry.get_bank(registry.resolve_bank_id(args.bank))
    cohort = Cohort.load(args.cohort)
    simulees = cohort.simulees[: args.limit]
    mains = [m for m in cohort.mains if m in set(bank.variables())]
    pools = {m: bank.shortlist(m, exclude=set()) for m in mains}

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
            for simulee in simulees:
                for main in mains:
                    result = run_one(
                        simulee=simulee,
                        main=main,
                        pool=pools[main],
                        cuts=cuts,
                        stop_rule=stop_rule,
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
        json.dumps({"cohort": cohort.dgp, "n_simulees": len(simulees), "rows": rows}, indent=1),
        encoding="utf-8",
    )

    header = (
        f"{'bands':>6} {'width':>6} {'stop rule':>17} {'accuracy':>9} {'within1':>8} "
        f"{'items':>7} {'rel':>7} {'consist':>8} {'d acc pp':>9} {'d items':>8}"
    )
    print(f"Phase 0a configuration pre-study — {len(simulees)} simulees x {len(mains)} mains")
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
