"""Score one approach branch against the coded CAT algorithm, by measurement.

WHY THIS EXISTS
---------------
`APPROACH_COMPARISON.md` scores the three branches 90/84/72 and their KL->FI fidelity
93/88/62. Those numbers were assigned by reading the code, not produced by running it:
no script in this repository — on any branch, in any commit, stash, or dangling tree —
computes them. The same is true of the paired tables cited in `llm_math.py` (n=1350/arm,
RMSE 0.7387 vs 0.7537, bootstrap CIs). They may well be right. They are not evidence,
and "which approach do we ship" is not a question to answer from a rubric.

What was already here and why none of it settles the question:

  simulate_cat.py       runs `use_llm=False`. Correct as a baseline, but it is the same
                        program on all three branches, so it cannot rank them.
  measure_llm_cost.py   cost only. Its `theta_hat` is returned and never read, and its
                        single shared RNG cannot support a paired comparison.
  validate_llm_path.py  the real instrument, and the closest thing to this file: LLM in
                        the loop, RMSE, |Δθ̂|, fallback rate. But one arm, one
                        competency, no coded counterfactual, and — decisively — no
                        measure of whether the administered ITEM was the one the CAT
                        rule calls for, which is the entire content of "KL -> FI
                        fidelity".

WHAT THIS MEASURES
------------------
Two arms per candidate, on common random numbers:

  llm     the branch's real controller against the real API
  coded   the same candidate, same seed, `use_llm=False` — the algorithm as written

Both arms draw their response uniforms from a generator seeded per candidate, so the
two runs see the same luck and differ only through the decisions under test.

  recovery      RMSE / bias vs known true θ, and the PAIRED ΔMSE (llm − coded) with a
                bootstrap CI. Paired because the arms share seeds: the per-candidate
                difference has far less variance than either arm's absolute RMSE, so a
                real effect is visible at n a single-arm comparison could not resolve.
  selection     at every step, what the coded procedure would have administered from
                the SAME state. Reported two ways, because either alone misleads:
                  agreement    how often the administered id is the coded choice
                  info regret  mean (I_coded - I_administered) / I_coded
                An approach that takes the #2 item when it carries 99.6% of the
                information has not damaged the instrument; one that agrees 80% of the
                time and drops half the information on the other 20% has. Regret is the
                quantity with psychometric meaning; agreement is the headline.
  math          |Δθ̂| against the coded EAP on identical evidence — mean/p95/max — plus
                direction violations and acceptance rate. Empty on approach 1, which
                does not let the model touch the maths.
  stopping      what `check_convergence` would have ruled at the state where the branch
                actually stopped. Over-stop (quit while the coded rule says continue)
                is the expensive error: it ships an ability estimate the psychometrics
                do not support. Only approach 3 owns this decision.
  coverage      distinct sub-competencies touched, out of 8. The blueprint constraint.
  robustness    fallbacks (1, 2), invalid steps (3), malformed responses.
  cost          metered calls and tokens per competency, and wall clock.

EXPOSURE CONTROL IS PINNED OFF (top_k=1) in both arms. Randomesque starts the shortlist
at a uniform random rank, so with it on, a disagreement between the arms is partly the
engine's own dice and the selection numbers would measure noise. Exposure is a
deployment property; it is not what distinguishes these three branches.

THE COMPOSITE SCORE IS A ROLL-UP, NOT A MEASUREMENT. `WEIGHTS` below is a judgement
about what matters and is stated in one place so it can be argued with and changed. The
measured table is the evidence; the single number is a convenience. Read both.

Usage:
    python score_approach.py --candidates 40 --out scores/
    python score_approach.py --model kimi-k2.5 --candidates 40
    python score_approach.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
    rank_candidates,
    resolve_a,
    resolve_b,
    resolve_c,
)
from llm_client import (
    get_model,
    get_usage,
    llm_configured,
    probe_gateway,
    provider_label,
    reset_usage,
    set_model,
)
from selection_pipeline import deterministic_select, select_next_item

BANK = Path(__file__).parent / "enriched_bank_cat.json"
TRUE_THETAS = [-2.0, -1.0, 0.0, 1.0, 2.0]
PRIOR_SD = 2.0

# Roll-up weights. Stated here rather than buried in the printing code so the composite
# can be re-argued without re-running anything. Recovery dominates: an assessment that
# does not recover ability has failed regardless of how procedurally tidy it was.
WEIGHTS = {
    "recovery": 0.40,     # RMSE against true theta — does it measure ability at all
    "selection": 0.25,    # information regret vs the coded KL->FI choice
    "math": 0.15,         # |dtheta| against the coded EAP
    "stopping": 0.10,     # agreement with the coded convergence rule
    "robustness": 0.10,   # fallback / invalid-step rate
}


def _has(module: str) -> bool:
    return importlib_util.find_spec(module) is not None


def approach_name() -> str:
    if _has("llm_full_cat"):
        return "approach-3-llm-full-cat"
    if _has("llm_math"):
        return "approach-2-llm-math-code-pick"
    return "approach-1-code-math-llm-pick"


def load_pool(competency: str | None = None) -> tuple[list[dict], str]:
    bank = json.loads(BANK.read_text())
    for q in bank:
        q["a"], q["b"], q["c"] = resolve_a(q), resolve_b(q), resolve_c(q)
    comp = competency or sorted({q["competency"] for q in bank})[0]
    return [q for q in bank if q["competency"] == comp], comp


def _fresh(prior_sd: float = PRIOR_SD) -> dict:
    return {
        "posterior": prior_from_level(3, prior_sd),
        "theta_hat": 0.0,
        "se": prior_sd,
        "se_start": prior_sd,
        "prior_sd": prior_sd,
        "self_confidence": "low",
        "q_count": 0,
        "served_ids": [],
        "history": [],
        "level_history": [],
        "certainty_pct": combined_certainty_pct(prior_sd, "low", 0, prior_sd,
                                                se_start=prior_sd),
    }


# --- the counterfactual ----------------------------------------------------------
def coded_choice(state: dict, pool: list[dict], competency: str) -> tuple[str, float]:
    """(id, information) the coded procedure would administer from THIS state.

    Calls `deterministic_select`, the same function the branches fall back to, rather
    than reimplementing the rule — a hand-rolled copy would drift from the shipped
    procedure and this file would then measure fidelity to a rule nobody runs.

    top_k=1 pins exposure control off; see the module docstring.
    """
    sel = deterministic_select(
        state["theta_hat"], state["q_count"], pool, state["served_ids"],
        state["posterior"], top_k=1,
    )
    if sel is None:
        return "", 0.0
    return sel.item["id"], float(sel.info_score)


def _info_of(state: dict, pool: list[dict], item_id: str) -> float:
    """Selection-criterion information of one item, scored from the same state.

    Read off `rank_candidates` so it is the same criterion (KL early, E[Fisher] later)
    that ranking used — recomputing it independently is how the numerator and
    denominator of a regret ratio end up on different scales.
    """
    ranked, _ = rank_candidates(state["theta_hat"], state["q_count"], pool,
                                state["served_ids"], state["posterior"],
                                top_n=len(pool), top_k=1)
    for q, info, _fi, _kl in ranked:
        if q["id"] == item_id:
            return float(info)
    return 0.0


class StepLog:
    """Per-step evidence for one candidate. Plain accumulator, no analysis."""

    def __init__(self) -> None:
        self.sel_total = 0
        self.sel_agree = 0
        self.regrets: list[float] = []
        self.devs: list[float] = []
        self.math_steps = 0
        self.fallbacks = 0
        self.violations = 0
        self.invalid_steps = 0

    def selection(self, state: dict, pool: list[dict], competency: str,
                  administered_id: str) -> None:
        want_id, want_info = coded_choice(state, pool, competency)
        if not want_id:
            return
        self.sel_total += 1
        if want_id == administered_id:
            self.sel_agree += 1
            self.regrets.append(0.0)
            return
        got_info = _info_of(state, pool, administered_id)
        # Relative, not absolute: information varies by an order of magnitude across the
        # theta range, so an absolute gap would let disagreements at high-information
        # states dominate a mean that is supposed to describe the whole session.
        self.regrets.append(
            float((want_info - got_info) / want_info) if want_info > 1e-9 else 0.0
        )


def _summary(state: dict, true_theta: float, log: StepLog, pool: list[dict],
             stop_reason: str, elapsed: float) -> dict:
    sub_by_id = {q["id"]: q.get("sub_competency", "") for q in pool}
    covered = {sub_by_id.get(i, "") for i in state["served_ids"]}
    coded_stop = check_convergence(state["certainty_pct"], state["level_history"],
                                   state["q_count"])
    return {
        "true_theta": true_theta,
        "theta_hat": state["theta_hat"],
        "se": state["se"],
        "n": state["q_count"],
        "stop_reason": stop_reason,
        # What the coded rule would have said at the state this session stopped in.
        # Only meaningful where the branch owns stopping (approach 3); on 1 and 2 the
        # coded rule IS the stop rule, so these agree by construction and the printed
        # section says so rather than presenting 100% as a finding.
        "coded_would_stop": bool(coded_stop.stop),
        "coded_stop_reason": coded_stop.reason,
        "sub_covered": len([c for c in covered if c]),
        "sel_total": log.sel_total,
        "sel_agree": log.sel_agree,
        "regrets": log.regrets,
        "devs": log.devs,
        "math_steps": log.math_steps,
        # Any step that landed on coded logic instead of the model: a rejected/absent
        # selection on approach 1, a rejected/absent maths update on approach 2.
        "fallbacks": log.fallbacks,
        "violations": log.violations,
        "invalid_steps": log.invalid_steps,
        "elapsed_s": elapsed,
    }


# --- arms ------------------------------------------------------------------------
def run_coded(pool: list[dict], competency: str, true_theta: float,
              rng: np.random.Generator) -> dict:
    """The algorithm as written, no model. The arm every branch is compared against."""
    state = _fresh()
    log = StepLog()
    reason = "max_questions"
    t0 = time.time()

    for q in range(MAX_QUESTIONS):
        sel = select_next_item(state["theta_hat"], state["se"], state["q_count"],
                               competency, pool, state["served_ids"], state["history"],
                               posterior=state["posterior"], use_llm=False, top_k=1)
        if sel is None:
            reason = "bank_exhausted"
            break
        item = sel.item
        correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))
        post, th, se = eap_update(state["posterior"], item, correct)
        state["posterior"], state["theta_hat"], state["se"] = post, th, se
        state["q_count"] += 1
        state["served_ids"].append(item["id"])
        state["history"].append({"id": item["id"], "correct": correct,
                                 "difficulty": item.get("difficulty"),
                                 "theta_hat": th, "se": se})
        state["level_history"].append(level_and_band(th, se)[0])
        state["certainty_pct"] = combined_certainty_pct(
            se, "low", q + 1, state["prior_sd"], se_start=state["se_start"])
        conv = check_convergence(state["certainty_pct"], state["level_history"],
                                 state["q_count"])
        if conv.stop:
            reason = conv.reason
            break

    return _summary(state, true_theta, log, pool, reason, time.time() - t0)


def run_llm(pool: list[dict], competency: str, true_theta: float,
            rng: np.random.Generator, name: str) -> dict:
    """The branch's real controller against the real API."""
    if name == "approach-3-llm-full-cat":
        return _run_full_cat(pool, competency, true_theta, rng)
    return _run_coded_loop(pool, competency, true_theta, rng, name)


def _run_coded_loop(pool: list[dict], competency: str, true_theta: float,
                    rng: np.random.Generator, name: str) -> dict:
    """Approaches 1 and 2: coded selection loop, differing only in who does the maths."""
    state = _fresh()
    log = StepLog()
    reason = "max_questions"
    t0 = time.time()

    use_llm_select = name == "approach-1-code-math-llm-pick"
    llm_math_update = None
    if name == "approach-2-llm-math-code-pick":
        from llm_math import llm_math_update  # noqa: F401

    for q in range(MAX_QUESTIONS):
        sel = select_next_item(state["theta_hat"], state["se"], state["q_count"],
                               competency, pool, state["served_ids"], state["history"],
                               posterior=state["posterior"],
                               use_llm=use_llm_select, top_k=1)
        if sel is None:
            reason = "bank_exhausted"
            break
        item = sel.item
        # Scored BEFORE the state advances — the counterfactual has to be evaluated at
        # the state the decision was made in, not the one it produced.
        log.selection(state, pool, competency, item["id"])
        if use_llm_select and not sel.llm_used:
            log.fallbacks += 1
        correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))

        if llm_math_update is not None:
            res = llm_math_update(state, item, correct, use_llm=True)
            log.math_steps += 1
            log.fallbacks += bool(res.fallback_used)
            log.violations += bool(res.invariant_violation)
            # Rejected steps included. Gating on `not fallback_used` drops exactly the
            # largest deviations and censors this metric at DEVIATION_REJECT — the bug
            # llm_math.py and validate_llm_path.py both document having made.
            if res.theta_deviation is not None:
                log.devs.append(res.theta_deviation)
            state["posterior"] = res.posterior
            state["theta_hat"], state["se"] = res.theta_hat, res.se
            state["certainty_pct"] = res.certainty_pct
        else:
            log.math_steps += 1
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
        conv = check_convergence(state["certainty_pct"], state["level_history"],
                                 state["q_count"])
        if conv.stop:
            reason = conv.reason
            break

    return _summary(state, true_theta, log, pool, reason, time.time() - t0)


def _run_full_cat(pool: list[dict], competency: str, true_theta: float,
                  rng: np.random.Generator) -> dict:
    """Approach 3: the model owns maths, stopping, and selection in one call."""
    from llm_full_cat import fresh_audit_posterior, llm_full_step

    state = _fresh()
    # The comparator's own belief: same prior the session starts at, updated by code from
    # graded responses alone and never shown to the model. It must be persisted across
    # steps — otherwise llm_full_step restarts it from the prior every call and |dtheta|
    # reports single-step noise instead of the cumulative drift it exists to detect.
    state["audit_posterior"] = fresh_audit_posterior(prior_sd=PRIOR_SD)
    log = StepLog()
    reason = "max_questions"
    t0 = time.time()

    res = llm_full_step(state, pool, competency, use_llm=True, allow_rephrase=False)
    for _ in range(MAX_QUESTIONS):
        if res.invalid_llm_step:
            log.invalid_steps += 1
            reason = "invalid_llm_step"
            break
        if res.selection is None:
            reason = res.stop_rule_reason or "bank_exhausted"
            break
        item = res.selection.item
        log.selection(state, pool, competency, item["id"])
        correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))
        res = llm_full_step(state, pool, competency, answered_item=item,
                            is_correct=correct, use_llm=True, allow_rephrase=False)
        log.math_steps += 1
        log.violations += bool(res.invariant_violation)
        if res.theta_deviation is not None:
            log.devs.append(res.theta_deviation)
        # Carried even on an invalid step: the response was graded regardless of whether
        # the model's reply was usable, so code's belief has to move with the evidence.
        if res.audit_posterior is not None:
            state["audit_posterior"] = res.audit_posterior
        if res.invalid_llm_step:
            log.invalid_steps += 1
            # The step is invalid, but the ITEM was administered and answered. Advancing
            # the served list keeps the session's question count honest; the controller
            # aborts on the next loop iteration.
            state["served_ids"].append(item["id"])
            state["q_count"] += 1
            reason = "invalid_llm_step"
            break
        state["posterior"] = res.posterior
        state["theta_hat"], state["se"] = res.theta_hat, res.se
        state["certainty_pct"] = res.certainty_pct
        state["q_count"] += 1
        state["served_ids"].append(item["id"])
        state["level_history"].append(level_and_band(res.theta_hat, res.se)[0])
        if res.stop:
            reason = res.stop_rule_reason or "llm_stop"
            break

    return _summary(state, true_theta, log, pool, reason, time.time() - t0)


# --- statistics ------------------------------------------------------------------
def paired_delta_mse(llm: list[dict], coded: list[dict],
                     boots: int = 10000, seed: int = 7) -> tuple[float, float, float]:
    """(ΔMSE, lo, hi) for llm − coded, bootstrapped over candidate pairs.

    Resampling PAIRS rather than each arm independently is the whole point: the arms
    share a seed, so their errors are correlated and the paired difference has much
    lower variance than the difference of two independent means. A CI built from
    independent resamples would be far too wide and would call a real effect noise.
    """
    d = np.array([(l["theta_hat"] - l["true_theta"]) ** 2
                  - (c["theta_hat"] - c["true_theta"]) ** 2
                  for l, c in zip(llm, coded)])
    if len(d) == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(boots, len(d)))
    means = d[idx].mean(axis=1)
    return float(d.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _rmse(rows: list[dict]) -> float:
    e = np.array([r["theta_hat"] - r["true_theta"] for r in rows])
    return float(np.sqrt(np.mean(e ** 2))) if len(e) else float("nan")


def build_report(name: str, model: str, competency: str, llm: list[dict],
                 coded: list[dict], wall_s: float) -> dict:
    usage = get_usage()
    regrets = [x for r in llm for x in r["regrets"]]
    devs = [x for r in llm for x in r["devs"]]
    sel_total = sum(r["sel_total"] for r in llm)
    sel_agree = sum(r["sel_agree"] for r in llm)
    steps = sum(r["math_steps"] for r in llm)
    fallbacks = sum(r["fallbacks"] for r in llm)
    invalid = sum(r["invalid_steps"] for r in llm)
    dmse, lo, hi = paired_delta_mse(llm, coded)

    owns_stop = name == "approach-3-llm-full-cat"
    # Stopped while the coded rule said keep going: the expensive direction, because it
    # ships an estimate the psychometrics do not support.
    over = sum(1 for r in llm if not r["coded_would_stop"]
               and r["stop_reason"] not in ("bank_exhausted", "invalid_llm_step"))

    return {
        "approach": name,
        "model": model,
        "provider": provider_label(),
        "competency": competency,
        "candidates": len(llm),
        "wall_clock_s": round(wall_s, 1),
        "recovery": {
            "rmse_llm": _rmse(llm),
            "rmse_coded": _rmse(coded),
            "bias_llm": float(np.mean([r["theta_hat"] - r["true_theta"] for r in llm])),
            "delta_mse": dmse, "ci_lo": lo, "ci_hi": hi,
            "significant": bool(lo > 0 or hi < 0),
            "mean_items_llm": float(np.mean([r["n"] for r in llm])),
            "mean_items_coded": float(np.mean([r["n"] for r in coded])),
        },
        "selection": {
            "steps": sel_total,
            "agreement": sel_agree / sel_total if sel_total else float("nan"),
            "mean_regret": float(np.mean(regrets)) if regrets else 0.0,
            "p95_regret": float(np.percentile(regrets, 95)) if regrets else 0.0,
            "max_regret": float(max(regrets)) if regrets else 0.0,
        },
        "math": {
            "steps": steps,
            "mean_dev": float(np.mean(devs)) if devs else None,
            "p95_dev": float(np.percentile(devs, 95)) if devs else None,
            "max_dev": float(max(devs)) if devs else None,
            "violations": sum(r["violations"] for r in llm),
        },
        "stopping": {
            "owns_decision": owns_stop,
            "over_stop_rate": over / len(llm) if llm else 0.0,
            "reasons": dict(Counter(r["stop_reason"] for r in llm)),
            "coded_reasons": dict(Counter(r["stop_reason"] for r in coded)),
        },
        "coverage": {
            "mean_subs_llm": float(np.mean([r["sub_covered"] for r in llm])),
            "mean_subs_coded": float(np.mean([r["sub_covered"] for r in coded])),
        },
        "robustness": {
            "steps": steps,
            "fallback_rate": fallbacks / steps if steps else 0.0,
            "invalid_step_rate": invalid / max(steps, 1),
            "sessions_aborted": sum(1 for r in llm if r["stop_reason"] == "invalid_llm_step"),
        },
        "cost": {
            "calls": usage.calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": usage.cost_usd(),
            "calls_per_competency": usage.calls / len(llm) if llm else 0.0,
            "tokens_per_competency": (usage.input_tokens + usage.output_tokens) / len(llm)
            if llm else 0.0,
        },
    }


def composite(rep: dict) -> dict:
    """Roll the measured table into 0-100 per dimension. See WEIGHTS.

    Each sub-score is an explicit, bounded transform of a measured quantity. They are
    stated as formulas rather than buckets so that a change of 0.01 in a measurement
    moves the score by a defined amount instead of jumping a tier.
    """
    r, s, m, st, rb = (rep["recovery"], rep["selection"], rep["math"],
                       rep["stopping"], rep["robustness"])

    # Recovery: the coded arm is the reference, scored 100 when the LLM arm matches it.
    # Ratio of RMSE, so being 10% worse costs 10 points regardless of the absolute level.
    ratio = r["rmse_llm"] / r["rmse_coded"] if r["rmse_coded"] > 0 else 1.0
    recovery = float(np.clip(100 * (2 - ratio), 0, 100))

    # Selection: mean information regret against the coded KL->FI choice. 0 regret is
    # 100; losing a quarter of the available information on average is 0.
    selection = float(np.clip(100 * (1 - s["mean_regret"] / 0.25), 0, 100))

    # Math: |dtheta| vs the coded EAP, scaled by DEVIATION_REJECT (0.50) — the gate the
    # branch itself uses to call an update unusable. None (approach 1) means the model
    # never touches the maths, which is full marks for fidelity by construction.
    math = 100.0 if m["mean_dev"] is None else float(
        np.clip(100 * (1 - m["mean_dev"] / 0.50), 0, 100))

    # Stopping: over-stop rate. Not scored where the coded rule owns the decision.
    stopping = 100.0 if not st["owns_decision"] else float(
        np.clip(100 * (1 - st["over_stop_rate"]), 0, 100))

    # Robustness: fallbacks and aborts both mean the run did not measure what it claims.
    robustness = float(np.clip(
        100 * (1 - rb["fallback_rate"] - rb["invalid_step_rate"]), 0, 100))

    parts = {"recovery": recovery, "selection": selection, "math": math,
             "stopping": stopping, "robustness": robustness}
    parts["TOTAL"] = float(sum(parts[k] * WEIGHTS[k] for k in WEIGHTS))
    return parts


# --- reporting -------------------------------------------------------------------
def print_report(rep: dict, parts: dict) -> None:
    r, s, m, st, cv, rb, c = (rep["recovery"], rep["selection"], rep["math"],
                              rep["stopping"], rep["coverage"], rep["robustness"],
                              rep["cost"])
    P = print
    P(f"\n{'=' * 72}")
    P(f"{rep['approach']}   model={rep['model']}   n={rep['candidates']} candidates")
    P(f"competency={rep['competency']}   wall clock {rep['wall_clock_s']:.0f}s")
    P("=" * 72)

    P("\nRECOVERY (vs known true theta; coded arm on the same seeds)")
    P(f"  RMSE  llm {r['rmse_llm']:.3f}   coded {r['rmse_coded']:.3f}   "
      f"bias {r['bias_llm']:+.3f}")
    P(f"  items llm {r['mean_items_llm']:.1f}     coded {r['mean_items_coded']:.1f}")
    verdict = ("WORSE than coded" if r["ci_lo"] > 0 else
               "BETTER than coded" if r["ci_hi"] < 0 else
               "indistinguishable from coded")
    P(f"  paired dMSE {r['delta_mse']:+.4f}  95% CI [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}]"
      f"  -> {verdict}")

    P("\nSELECTION FIDELITY (vs the coded KL->FI choice from the same state)")
    if s["steps"]:
        P(f"  agreement    {s['agreement']:.1%} of {s['steps']} steps")
        P(f"  info regret  mean {s['mean_regret']:.4f}  p95 {s['p95_regret']:.4f}  "
          f"max {s['max_regret']:.4f}")
        P("  (regret is the fraction of available information given up when the "
          "administered")
        P("   item differs from the coded choice; agreement alone cannot tell a "
          "0.1% miss")
        P("   from a 60% one)")
    else:
        P("  n/a — code owns selection on this branch")

    P("\nMATH FIDELITY (|dtheta| vs coded EAP on identical evidence)")
    if m["mean_dev"] is None:
        P("  n/a — code owns the maths on this branch")
    else:
        P(f"  mean {m['mean_dev']:.3f}  p95 {m['p95_dev']:.3f}  max {m['max_dev']:.3f}"
          f"   over {m['steps']} steps")
        P(f"  direction-invariant violations: {m['violations']}")

    P("\nSTOPPING")
    if st["owns_decision"]:
        P(f"  over-stop rate {st['over_stop_rate']:.1%} "
          "(quit while the coded rule said continue)")
    else:
        P("  coded rule owns stopping on this branch — agreement is by construction")
    P(f"  reasons  llm   {st['reasons']}")
    P(f"           coded {st['coded_reasons']}")

    P("\nCONTENT BALANCE")
    P(f"  sub-competencies covered / 8   llm {cv['mean_subs_llm']:.2f}   "
      f"coded {cv['mean_subs_coded']:.2f}")

    P("\nROBUSTNESS")
    P(f"  fallback rate {rb['fallback_rate']:.1%}   "
      f"invalid-step rate {rb['invalid_step_rate']:.1%}   "
      f"sessions aborted {rb['sessions_aborted']}")

    P("\nCOST (metered)")
    cost = f"${c['cost_usd']:.4f}" if c["cost_usd"] is not None else (
        "unpriced model — set LLM_PRICE_{INPUT,OUTPUT}_PER_1M")
    P(f"  {c['calls']} calls · {c['input_tokens']:,} in / {c['output_tokens']:,} out "
      f"· {cost}")
    P(f"  per competency: {c['calls_per_competency']:.1f} calls · "
      f"{c['tokens_per_competency']:,.0f} tokens")

    P(f"\n{'-' * 72}")
    P("COMPOSITE (roll-up of the above; weights are a judgement, see WEIGHTS)")
    for k, w in WEIGHTS.items():
        P(f"  {k:<12} {parts[k]:6.1f}   x{w:.2f}")
    P(f"  {'TOTAL':<12} {parts['TOTAL']:6.1f} / 100")
    P("-" * 72)

    if rb["fallback_rate"] > 0.2:
        P(f"\n  !! {rb['fallback_rate']:.0%} of steps fell back to coded logic — the "
          "numbers above")
        P("     describe the engine, not the model.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", type=int, default=40,
                    help="total candidates, spread evenly over the true-theta grid")
    ap.add_argument("--model", default=None, help="override the configured model")
    ap.add_argument("--competency", default=None, help="default: first alphabetically")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent candidates; a kimi CAT step runs ~20s, so serial "
                         "is hours")
    ap.add_argument("--out", default=None, help="directory to write <approach>.json into")
    ap.add_argument("--dry-run", action="store_true", help="plan only, no calls")
    args = ap.parse_args()

    if args.model:
        set_model(args.model)

    name = approach_name()
    pool, competency = load_pool(args.competency)
    per_theta = max(1, args.candidates // len(TRUE_THETAS))
    total = per_theta * len(TRUE_THETAS)
    calls_per_q = 2 if name == "approach-3-llm-full-cat" else 1

    print(f"Approach   : {name}")
    print(f"Provider   : {provider_label()}")
    print(f"Model      : {get_model()}")
    print(f"Competency : {competency}  ({len(pool)} items)")
    print(f"Candidates : {total}  ({per_theta} per theta in {TRUE_THETAS})")
    print(f"Arms       : llm (real API) + coded (offline), common random numbers")
    print(f"LLM calls  : ~{calls_per_q}/question, <={MAX_QUESTIONS} questions/candidate "
          f"-> up to ~{total * MAX_QUESTIONS * calls_per_q} calls")

    if args.dry_run:
        print("\n(dry run — no calls made)")
        return 0

    if not llm_configured():
        print("\nNo LLM configured — this harness measures the real path or nothing.")
        return 1
    ok, msg = probe_gateway()
    print(f"Probe      : {msg}")
    if not ok:
        return 1

    jobs = [(i, j, t) for i, t in enumerate(TRUE_THETAS) for j in range(per_theta)]
    reset_usage()
    t0 = time.time()
    done = 0

    def one(job):
        i, j, true_theta = job
        # Same seed for both arms: common random numbers. The two runs therefore see the
        # same luck, and the paired difference isolates the decisions under test.
        return (
            run_llm(pool, competency, true_theta, np.random.default_rng(1000 * i + j), name),
            run_coded(pool, competency, true_theta, np.random.default_rng(1000 * i + j)),
        )

    llm_rows: list[dict] = []
    coded_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for lrow, crow in ex.map(one, jobs):
            llm_rows.append(lrow)
            coded_rows.append(crow)
            done += 1
            print(f"\r  {done}/{total} candidates… "
                  f"({time.time() - t0:.0f}s elapsed)", end="", flush=True)
    print()

    rep = build_report(name, get_model(), competency, llm_rows, coded_rows,
                       time.time() - t0)
    parts = composite(rep)
    rep["composite"] = parts
    print_report(rep, parts)

    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / f"{name}.json"
        path.write_text(json.dumps(rep, indent=2))
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
