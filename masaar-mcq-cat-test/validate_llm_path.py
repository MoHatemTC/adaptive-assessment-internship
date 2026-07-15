"""Measure the CAT as the LLM actually administers it, against the real API.

WHY THIS EXISTS

`simulate_cat.py` drives `select_next_item(use_llm=False)`. That is the right *baseline*
-- it measures the coded procedure the LLM is held against -- but it is not the thing any
of these branches ship. Every psychometric number in the README describes a program with
no model in it. The stubbed suites in `test_approach.py` close part of the gap by proving
the architecture recovers theta when the LLM behaves, but a stub that implements the
procedure correctly is not evidence that gpt-4o-mini does: it does arithmetic with no
tools, at temperature 0, from a prompt.

So this drives simulated candidates of known ability through the branch's real controller
and the real API, and reports what actually came back.

WHAT IT MEASURES

  RMSE(theta_hat)     recovery against known true theta -- the number that says whether
                      the instrument measures ability at all
  bias                per true-theta, so a systematic pull is visible rather than averaged
                      away into RMSE
  mean/p95 |dtheta|   the LLM's theta against the coded EAP on the same evidence. This is
                      a per-step comparison, not trajectory error: code owns the posterior,
                      so both estimates see identical evidence every step
  fallback rate       steps that landed on coded EAP. A high number here means the run
                      measured the engine; the RMSE above would then be the engine's, and
                      reporting it as the LLM's would be the exact substitution this
                      harness exists to prevent
  violations          direction-invariant breaches (correct answer moving theta down)
  items / stop reason what the convergence rule actually did

It auto-detects the branch the same way measure_llm_cost.py does, so one file works on all
three without edits.

COST

Real calls, real money. Roughly $0.003-0.006 per candidate-competency on gpt-4o-mini, so
100 candidates is well under a dollar per branch. --dry-run prints the plan and the
estimate without spending anything.

Usage:
    python validate_llm_path.py --candidates 100
    python validate_llm_path.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from importlib import util as importlib_util
from pathlib import Path

import numpy as np

from certainty import combined_certainty_pct
from engine import (
    MAX_QUESTIONS,
    check_convergence,
    eap_update,
    level_and_band,
    p_correct,
    prior_from_level,
    resolve_a,
    resolve_b,
    resolve_c,
)
from llm_client import (
    get_model,
    get_usage,
    llm_configured,
    model_pricing,
    probe_gateway,
    reset_usage,
)

BANK = Path(__file__).parent / "enriched_bank_cat.json"
TRUE_THETAS = [-2.0, -1.0, 0.0, 1.0, 2.0]


def _has(module: str) -> bool:
    return importlib_util.find_spec(module) is not None


def approach_name() -> str:
    if _has("llm_full_cat"):
        return "approach-3-llm-full-cat"
    if _has("llm_math"):
        return "approach-2-llm-math-code-pick"
    return "approach-1-code-math-llm-pick"


def load_pool(competency: str | None = None) -> list[dict]:
    bank = json.loads(BANK.read_text())
    for q in bank:
        q["a"], q["b"], q["c"] = resolve_a(q), resolve_b(q), resolve_c(q)
    comp = competency or sorted({q["competency"] for q in bank})[0]
    return [q for q in bank if q["competency"] == comp], comp


def _fresh(prior_sd: float = 2.0) -> dict:
    post = prior_from_level(3, prior_sd)
    return {
        "posterior": post,
        "theta_hat": 0.0,
        "se": prior_sd,
        "se_start": prior_sd,
        "prior_sd": prior_sd,
        "self_confidence": "low",
        "q_count": 0,
        "served_ids": [],
        "history": [],
        "level_history": [],
        "certainty_pct": combined_certainty_pct(prior_sd, "low", 0, prior_sd, se_start=prior_sd),
    }


def run_candidate(pool: list[dict], competency: str, true_theta: float,
                  rng: np.random.Generator, name: str) -> dict:
    """One candidate through the branch's real controller. Returns per-step evidence."""
    state = _fresh()
    devs: list[float] = []
    steps = fallbacks = violations = 0
    reason = "max_questions"

    if name == "approach-3-llm-full-cat":
        from llm_full_cat import llm_full_step

        res = llm_full_step(state, pool, competency, use_llm=True, allow_rephrase=False)
        for _ in range(MAX_QUESTIONS):
            if res.selection is None:
                break
            item = res.selection.item
            correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))
            res = llm_full_step(state, pool, competency, answered_item=item,
                                is_correct=correct, use_llm=True, allow_rephrase=False)
            steps += 1
            fallbacks += bool(res.fallback_used)
            violations += bool(res.invariant_violation)
            if not res.fallback_used and res.theta_deviation is not None:
                devs.append(res.theta_deviation)
            state["posterior"] = res.posterior
            state["theta_hat"], state["se"] = res.theta_hat, res.se
            state["certainty_pct"] = res.certainty_pct
            state["q_count"] += 1
            state["served_ids"].append(item["id"])
            state["level_history"].append(level_and_band(res.theta_hat, res.se)[0])
            if res.stop:
                reason = res.stop_rule_reason or "bank_exhausted"
                break
        return _summary(state, true_theta, devs, steps, fallbacks, violations, reason)

    # Approaches 1 and 2 share the coded selection loop; only the maths differs.
    from selection_pipeline import select_next_item

    use_llm_select = name == "approach-1-code-math-llm-pick"
    llm_math_update = None
    if name == "approach-2-llm-math-code-pick":
        from llm_math import llm_math_update  # noqa: F401

    for q in range(MAX_QUESTIONS):
        sel = select_next_item(state["theta_hat"], state["se"], state["q_count"], competency,
                               pool, state["served_ids"], state["history"],
                               posterior=state["posterior"], use_llm=use_llm_select)
        if sel is None:
            reason = "bank_exhausted"
            break
        item = sel.item
        correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))

        if llm_math_update is not None:
            res = llm_math_update(state, item, correct, use_llm=True)
            steps += 1
            fallbacks += bool(res.fallback_used)
            violations += bool(res.invariant_violation)
            if not res.fallback_used and res.theta_deviation is not None:
                devs.append(res.theta_deviation)
            state["posterior"] = res.posterior
            state["theta_hat"], state["se"] = res.theta_hat, res.se
            state["certainty_pct"] = res.certainty_pct
        else:
            steps += 1
            fallbacks += (not sel.llm_used)
            post, th, se = eap_update(state["posterior"], item, correct)
            state["posterior"], state["theta_hat"], state["se"] = post, th, se
            state["certainty_pct"] = combined_certainty_pct(
                se, "low", q + 1, state["prior_sd"], se_start=state["se_start"])

        state["q_count"] += 1
        state["served_ids"].append(item["id"])
        state["history"].append({"id": item["id"], "correct": correct,
                                 "difficulty": item.get("difficulty"),
                                 "theta_hat": state["theta_hat"], "se": state["se"]})
        state["level_history"].append(level_and_band(state["theta_hat"], state["se"])[0])
        conv = check_convergence(state["certainty_pct"], state["level_history"], state["q_count"])
        if conv.stop:
            reason = conv.reason
            break

    return _summary(state, true_theta, devs, steps, fallbacks, violations, reason)


def _summary(state, true_theta, devs, steps, fallbacks, violations, reason) -> dict:
    return {
        "true_theta": true_theta,
        "theta_hat": state["theta_hat"],
        "se": state["se"],
        "n": state["q_count"],
        "devs": devs,
        "steps": steps,
        "fallbacks": fallbacks,
        "violations": violations,
        "stop_reason": reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", type=int, default=100,
                    help="total candidates, spread evenly over the true-theta grid")
    ap.add_argument("--dry-run", action="store_true", help="plan and cost estimate; no spend")
    args = ap.parse_args()

    name = approach_name()
    pool, competency = load_pool()
    per_theta = max(1, args.candidates // len(TRUE_THETAS))
    total = per_theta * len(TRUE_THETAS)
    calls_per_q = 2 if name == "approach-3-llm-full-cat" else 1

    print(f"Approach   : {name}")
    print(f"Model      : {get_model()}")
    print(f"Competency : {competency}  ({len(pool)} items)")
    print(f"Candidates : {total}  ({per_theta} per θ in {TRUE_THETAS})")
    print(f"LLM calls  : ~{calls_per_q}/question, ≤{MAX_QUESTIONS} questions/candidate\n")

    if args.dry_run:
        price = model_pricing() or {"input": 0.15, "output": 0.60}
        # ~9 questions/candidate at the current convergence rule; ~1.6k in / ~350 out per
        # call measured on gpt-4o-mini. Rough by construction — the real number comes from
        # metering a real run, which is the whole point of not estimating it.
        est = total * 9 * calls_per_q * (1600 * price["input"] + 350 * price["output"]) / 1e6
        print(f"Estimated cost: ~${est:.2f}  (no calls made)")
        return 0

    # Probe before spending anything. `llm_configured()` only asks whether a key is
    # *present*, which a rejected key satisfies: a first run against an expired key
    # cheerfully executed 881 steps, fell back to coded logic on every one, and produced a
    # full table of engine numbers. The fallback guard at the bottom caught it, but a
    # harness whose entire job is "measure the real path" should not get that far before
    # noticing there is no real path.
    if not llm_configured():
        print("OPENAI_API_KEY is not set — this harness measures the real path or nothing.")
        return 1
    ok, detail = probe_gateway()
    if not ok:
        print(f"Cannot reach the model, so there is nothing to measure:\n  {detail}")
        return 1

    reset_usage()
    results: list[dict] = []
    for i, true_theta in enumerate(TRUE_THETAS):
        for j in range(per_theta):
            r = run_candidate(pool, competency, true_theta,
                              np.random.default_rng(1000 * i + j), name)
            results.append(r)
            done = len(results)
            print(f"\r  {done}/{total} candidates…", end="", flush=True)
    print()

    errs = np.array([r["theta_hat"] - r["true_theta"] for r in results])
    all_devs = [d for r in results for d in r["devs"]]
    steps = sum(r["steps"] for r in results)
    fallbacks = sum(r["fallbacks"] for r in results)
    violations = sum(r["violations"] for r in results)

    print(f"\n{'true θ':>7} {'mean θ̂':>8} {'bias':>7} {'RMSE':>7} {'items':>6} {'conv%':>6}")
    print("-" * 46)
    for t in TRUE_THETAS:
        rs = [r for r in results if r["true_theta"] == t]
        e = np.array([r["theta_hat"] - t for r in rs])
        conv = np.mean([r["stop_reason"] in ("confidence", "stable_level") for r in rs])
        print(f"{t:>+7.1f} {np.mean([r['theta_hat'] for r in rs]):>8.2f} {np.mean(e):>+7.2f} "
              f"{np.sqrt(np.mean(e ** 2)):>7.3f} {np.mean([r['n'] for r in rs]):>6.1f} "
              f"{100 * conv:>5.0f}%")

    usage = get_usage()
    cost = usage.cost_usd()
    print(f"\n  RMSE(θ̂) overall        {np.sqrt(np.mean(errs ** 2)):.3f}")
    print(f"  bias overall           {np.mean(errs):+.3f}")
    if all_devs:
        print(f"  |Δθ̂| vs coded EAP      mean {np.mean(all_devs):.3f}  "
              f"p95 {np.percentile(all_devs, 95):.3f}  max {max(all_devs):.3f}")
    print(f"  LLM steps              {steps - fallbacks}/{steps} "
          f"({100 * (1 - fallbacks / max(steps, 1)):.0f}% ran on the model)")
    print(f"  invariant violations   {violations}")
    print(f"  stop reasons           {dict(Counter(r['stop_reason'] for r in results))}")
    print(f"  metered cost           {usage.calls} calls · "
          f"{usage.input_tokens:,} in / {usage.output_tokens:,} out · "
          f"{'$%.4f' % cost if cost is not None else 'unpriced model'}")

    # A run that mostly fell back measured the engine. Saying so is the point: the RMSE
    # above would be the engine's, and reporting it as the LLM's is the substitution this
    # harness exists to prevent.
    if fallbacks > steps * 0.2:
        print(f"\n  ⚠ {100 * fallbacks / max(steps, 1):.0f}% of steps fell back to coded "
              f"logic — treat the numbers above as the engine's, not the model's.")
        return 1
    print("\n  ✓ the numbers above describe the LLM-administered path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
