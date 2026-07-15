"""Measure real LLM call volume and cost for this approach.

Estimating tokens by eyeballing prompts is guesswork: the shortlist payload carries
full stems, and output length depends on how chatty the model is about its steps. So
this drives the branch's real controller against the real API with a simulated
candidate and reads `usage` off each response.

Detects which approach it is by which controller module exists, so one file works on
every branch.

Usage:
  python measure_llm_cost.py                 # 1 competency, simulated theta=0.5
  python measure_llm_cost.py --competencies 5 --reps 3
  python measure_llm_cost.py --dry-run       # count calls only, no API spend
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import llm_client
from engine import MAX_QUESTIONS, SE_TARGET, eap_update, p_correct, prior_from_level

BANK = Path(__file__).parent / "enriched_bank_cat.json"


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def approach_name() -> str:
    if _has("llm_full_cat"):
        return "approach-3-llm-full-cat"
    if _has("llm_math"):
        return "approach-2-llm-math-code-pick"
    return "approach-1-code-math-llm-pick"


def _state(theta: float, se: float) -> dict:
    return {
        "posterior": prior_from_level(int(np.clip(round(3 + theta), 1, 5)), se),
        "theta_hat": 0.0, "se": se, "q_count": 0, "self_confidence": "low",
        "prior_sd": se, "se_start": se, "served_ids": [], "certainty_pct": 0.0,
        "current_item": None,
    }


def run_session(pool: list[dict], true_theta: float, rng, competency: str) -> dict:
    """One full competency for this branch's controller. Returns per-session counters."""
    name = approach_name()
    st = _state(true_theta, 1.7)
    served: list[str] = []
    answered = 0

    if name == "approach-3-llm-full-cat":
        from llm_full_cat import llm_full_step, posterior_from_theta_se

        res = llm_full_step(st, pool, competency, use_llm=True)
        while res.selection is not None and not res.stop and answered < MAX_QUESTIONS:
            it = res.selection.item
            correct = bool(rng.random() < p_correct(true_theta, it["a"], it["b"], it["c"]))
            st["current_item"] = it
            res = llm_full_step(st, pool, competency, answered_item=it,
                                is_correct=correct, use_llm=True)
            st["theta_hat"], st["se"] = res.theta_hat, res.se
            st["posterior"] = posterior_from_theta_se(res.theta_hat, res.se)
            st["q_count"] += 1
            st["served_ids"].append(it["id"])
            served.append(it["id"])
            answered += 1

    elif name == "approach-2-llm-math-code-pick":
        from engine import select_item
        from llm_math import llm_math_update, posterior_from_theta_se

        while answered < MAX_QUESTIONS and st["se"] > SE_TARGET:
            it = select_item(st["theta_hat"], st["q_count"], pool, served, st["posterior"])
            if it is None:
                break
            correct = bool(rng.random() < p_correct(true_theta, it["a"], it["b"], it["c"]))
            st["current_item"] = it
            r = llm_math_update(st, it, correct, use_llm=True)
            st["theta_hat"], st["se"] = r.theta_hat, r.se
            st["posterior"] = posterior_from_theta_se(r.theta_hat, r.se)
            st["q_count"] += 1
            served.append(it["id"])
            st["served_ids"] = served
            answered += 1

    else:  # approach 1 — coded math, LLM selection
        from selection_pipeline import select_next_item

        while answered < MAX_QUESTIONS and st["se"] > SE_TARGET:
            sel = select_next_item(st["theta_hat"], st["se"], st["q_count"], competency,
                                   pool, served, [], st["posterior"], use_llm=True)
            if sel is None:
                break
            it = sel.item
            correct = bool(rng.random() < p_correct(true_theta, it["a"], it["b"], it["c"]))
            st["posterior"], st["theta_hat"], st["se"] = eap_update(st["posterior"], it, correct)
            st["q_count"] += 1
            served.append(it["id"])
            st["served_ids"] = served
            answered += 1

    return {"questions": answered, "final_se": st["se"], "theta_hat": st["theta_hat"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--competencies", type=int, default=1,
                    help="How many competencies to bill for (a full run is 5).")
    ap.add_argument("--reps", type=int, default=1, help="Sessions per competency to average.")
    ap.add_argument("--theta", type=float, default=0.5, help="Simulated true ability.")
    ap.add_argument("--dry-run", action="store_true", help="No API calls; report structure only.")
    args = ap.parse_args()

    bank = json.loads(BANK.read_text())
    by_comp: dict[str, list[dict]] = defaultdict(list)
    for q in bank:
        by_comp[q["competency"]].append(q)
    comps = sorted(by_comp)[: args.competencies]

    name = approach_name()
    model = llm_client.get_model()
    price = llm_client.model_pricing(model)

    print(f"Approach : {name}")
    print(f"Model    : {model}")
    if price:
        print(f"Pricing  : ${price['input']:.2f}/1M input · ${price['output']:.2f}/1M output")
    else:
        print(f"Pricing  : unknown for {model!r} — add it to MODEL_PRICING_USD_PER_1M")
    print(f"Bank     : {len(bank)} items across {len(by_comp)} competencies\n")

    if args.dry_run:
        print("--dry-run: no API calls made.")
        return 0

    if not llm_client.llm_configured():
        print("OPENAI_API_KEY not set — cannot measure. Add it to .env")
        return 1

    llm_client.reset_usage()
    rng = np.random.default_rng(4242)
    rows = []
    for comp in comps:
        for rep in range(args.reps):
            before = llm_client.get_usage()
            r = run_session(by_comp[comp], args.theta, rng, comp)
            after = llm_client.get_usage()
            rows.append({
                "competency": comp, "rep": rep, "questions": r["questions"],
                "calls": after.calls - before.calls,
                "in": after.input_tokens - before.input_tokens,
                "out": after.output_tokens - before.output_tokens,
                "final_se": r["final_se"],
            })
            print(f"  {comp:34s} q={r['questions']:2d}  calls={rows[-1]['calls']:3d}  "
                  f"in={rows[-1]['in']:6d}  out={rows[-1]['out']:5d}  SE={r['final_se']:.2f}")

    total = llm_client.get_usage()
    n = len(rows)
    mean_calls = sum(r["calls"] for r in rows) / n
    mean_q = sum(r["questions"] for r in rows) / n
    cost = total.cost_usd(model)

    print(f"\n{'=' * 66}")
    print(f"sessions measured        {n}  ({mean_q:.1f} questions each on average)")
    print(f"LLM calls                {total.calls} total · {mean_calls:.1f} per competency · "
          f"{total.calls / max(sum(r['questions'] for r in rows), 1):.2f} per question")
    print(f"tokens                   {total.input_tokens:,} in · {total.output_tokens:,} out")
    if cost is not None:
        print(f"cost                     ${cost:.4f} total · ${cost / n:.4f} per competency")
        print(f"\nfull assessment (5 competencies, 1 candidate): ${cost / n * 5:.4f}")
        print(f"100 candidates x 5 competencies:               ${cost / n * 5 * 100:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
