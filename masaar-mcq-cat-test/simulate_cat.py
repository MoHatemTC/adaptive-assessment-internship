"""
Drive simulated candidates through the real engine to prove the bank supports CAT.

Static bank statistics (a nice b ladder, uniform keys) do not prove the bank
works — what matters is whether theta actually converges when the engine's own
KL-then-Fisher selection runs against it. So this replays the real
engine.select_item / engine.eap_update path against synthetic candidates whose
true ability is known, and scores recovery.

Reported per true theta:
  bias / RMSE  — is theta_hat recovering the truth, or shrinking to the prior?
  SE, conv%    — does it reach engine.SE_TARGET inside MAX_QUESTIONS?
  meanI        — mean Fisher information per administered item. This is the
                 number that collapsed in the old bank at |theta| > 2.

Usage: python simulate_cat.py <bank.json> [n_reps]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from certainty import combined_certainty_pct
from engine import (
    CONFIDENCE_TARGET,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    STABLE_WINDOW,
    check_convergence,
    eap_update,
    fisher_info,
    level_and_band,
    p_correct,
    prior_from_level,
)
from selection_pipeline import select_next_item

TRUE_THETAS = [-2.5, -2.0, -1.0, 0.0, 1.0, 2.0, 2.5]
SEED = 20260715


def run_one(pool: list[dict], true_theta: float, rng: np.random.Generator,
            prior_sd: float = 2.0, self_rating: int | None = None) -> dict:
    """One simulated candidate: 3PL responses at true_theta, items chosen by the
    pipeline the app actually administers.

    This drives `selection_pipeline.select_next_item` with `use_llm=False`, not
    `engine.select_item`. The latter is pure greedy argmax — no shortlist, no 1% band,
    no tie-breaks, no exposure control — so it validated a path no branch ships, and
    the RMSE it reported was evidence for a program nobody runs. `use_llm=False` keeps
    the run offline and deterministic; it measures the coded procedure the LLM is held
    against, which is the baseline every branch comparison needs.

    Exposure control is pinned to top_k=1 so recovery is measured without randomesque
    noise; `rng` is threaded anyway so an exposure sweep can pass top_k>1.

    prior_sd/self_rating model the two real operating conditions. The app always
    asks for a self-rating, so SD=2.0 (uninformed) is the pessimistic bound, not
    the expected case — reporting only it would understate the bank.
    """
    level = 3 if self_rating is None else self_rating
    posterior = prior_from_level(level, prior_sd)
    theta_hat, se = 0.0, prior_sd
    confidence = "low"
    served: list[str] = []
    infos: list[float] = []
    level_history: list[int] = []
    certainty = combined_certainty_pct(se, confidence, 0, prior_sd, se_start=se)
    conv = check_convergence(certainty, level_history, 0)

    for q_count in range(MAX_QUESTIONS):
        sel = select_next_item(
            theta_hat, se, q_count, "", pool, served, [],
            posterior=posterior, use_llm=False, rng=rng, top_k=1,
        )
        if sel is None:
            break
        item = sel.item
        infos.append(fisher_info(theta_hat, item))
        p = p_correct(true_theta, item["a"], item["b"], item["c"])
        correct = bool(rng.random() < p)
        posterior, theta_hat, se = eap_update(posterior, item, correct)
        served.append(item["id"])
        level_history.append(level_and_band(theta_hat, se)[0])
        certainty = combined_certainty_pct(
            se, confidence, q_count + 1, prior_sd, se_start=prior_sd
        )
        conv = check_convergence(certainty, level_history, q_count + 1)
        if conv.stop:
            break

    return {
        "theta_hat": theta_hat,
        "se": se,
        "n": len(served),
        "converged": conv.converged,
        "stop_reason": conv.reason,
        "mean_info": float(np.mean(infos)) if infos else 0.0,
    }


def run_scenario(by_comp: dict[str, list[dict]], reps: int, label: str,
                 prior_sd: float, honest_rating: bool) -> tuple[float, float, float]:
    print("=" * 68)
    print(f"SCENARIO: {label}   (prior SD={prior_sd})")
    print("=" * 68)

    overall_sq, overall_n, overall_conv = 0.0, 0, 0
    worst_info = (1e9, "", 0.0)

    for comp in sorted(by_comp):
        pool = by_comp[comp]
        print(f"{comp}  (pool={len(pool)})")
        print(f"  {'true θ':>7} {'mean θ̂':>8} {'bias':>7} {'RMSE':>6} {'SE':>6} "
              f"{'conv%':>6} {'items':>6} {'meanI':>6}")
        for tt in TRUE_THETAS:
            rng = np.random.default_rng(SEED + int(tt * 100))
            # An honest self-rating maps true theta back to the 1-5 scale the app asks for.
            rating = int(np.clip(round(3 + tt), 1, 5)) if honest_rating else None
            runs = [run_one(pool, tt, rng, prior_sd, rating) for _ in range(reps)]
            est = np.array([r["theta_hat"] for r in runs])
            bias = float(est.mean() - tt)
            rmse = float(np.sqrt(((est - tt) ** 2).mean()))
            se = float(np.mean([r["se"] for r in runs]))
            conv = float(np.mean([r["converged"] for r in runs]))
            nq = float(np.mean([r["n"] for r in runs]))
            mi = float(np.mean([r["mean_info"] for r in runs]))
            print(f"  {tt:+7.1f} {est.mean():+8.2f} {bias:+7.2f} {rmse:6.2f} {se:6.2f} "
                  f"{conv:6.0%} {nq:6.1f} {mi:6.3f}")
            overall_sq += float(((est - tt) ** 2).sum())
            overall_n += len(est)
            overall_conv += int(conv * len(est))
            if mi < worst_info[0]:
                worst_info = (mi, f"{comp} @ θ={tt:+.1f}", tt)
        print()

    rmse_all = float(np.sqrt(overall_sq / overall_n))
    print(f"  RMSE(θ̂) {rmse_all:.3f}   convergence {overall_conv / overall_n:.0%} "
          f"within {MAX_QUESTIONS} items   weakest I {worst_info[0]:.3f} ({worst_info[1]})\n")
    return rmse_all, overall_conv / overall_n, worst_info[0]


def main() -> int:
    bank = json.loads(Path(sys.argv[1]).read_text())
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    by_comp: dict[str, list[dict]] = defaultdict(list)
    for q in bank:
        by_comp[q["competency"]].append(q)

    print(f"CAT simulation — {reps} candidates per (competency, true theta)")
    print(f"stop: certainty>={CONFIDENCE_TARGET:.0%} | same level {STABLE_WINDOW}x "
          f"(after {MIN_QUESTIONS} q) | {MAX_QUESTIONS} questions")
    print("path: selection_pipeline.select_next_item(use_llm=False), exposure top_k=1  "
          "selection=KL(first 3) then Fisher\n")

    # Pessimistic bound: candidate skipped the self-rating.
    rmse_u, conv_u, info_u = run_scenario(
        by_comp, reps, "no self-rating (pessimistic bound)", 2.0, honest_rating=False)
    # Expected case: the app asks for a self-rating and the candidate answers honestly.
    rmse_c, conv_c, info_c = run_scenario(
        by_comp, reps, "honest self-rating, low confidence (expected case)", 1.7, honest_rating=True)

    print("=" * 68)
    print(f"{'':44s} {'RMSE':>6} {'conv%':>7}")
    print(f"{'no self-rating (pessimistic)':44s} {rmse_u:6.3f} {conv_u:7.0%}")
    print(f"{'honest self-rating (expected)':44s} {rmse_c:6.3f} {conv_c:7.0%}")

    # This harness gates ABILITY RECOVERY only. The information floor is gated in
    # validate_bank.py against the bank's intrinsic property (best available item at
    # each theta). The meanI printed above is a different quantity — info of items as
    # actually administered, which is necessarily lower because the sharpest item is
    # consumed after one use and the first 3 picks optimise KL, not Fisher. Gating on
    # it here would confound bank quality with engine policy and double-count a check
    # that already has a cleaner home.
    #
    # Convergence % likewise depends on SE_TARGET/MAX_QUESTIONS, which are engine
    # settings the bank cannot fix: reported, not gated.
    ok = True
    if rmse_u > 0.85:
        print(f"\n✗ RMSE {rmse_u:.2f} in the pessimistic case — θ̂ is not recovering true ability.")
        ok = False
    if rmse_c > 0.75:
        print(f"\n✗ RMSE {rmse_c:.2f} in the expected case — θ̂ is not recovering true ability.")
        ok = False
    if ok:
        print("\n✓ θ̂ recovers true ability across the full range in both operating conditions.")
        print("  (Information floor is gated separately in validate_bank.py.)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
