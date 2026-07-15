"""Full LLM CAT controller for Approach 3.

The LLM makes every decision: it updates theta/SE, chooses the next question, and
words it. Code grades MCQs, computes the information those choices are made on, and
enforces the stopping rule.

WHY THIS IS TWO PHASES

A CAT step is ordered: a response updates theta, and the *updated* theta determines
which item is most informative next. The previous single-call design asked the model to
do both at once, which makes that ordering impossible to honour -- the engine cannot
score candidates at a theta it has not been told yet. So the call shipped raw a/b/c for
all ~24 unserved items and let the model free-pick. That is not adaptive testing:
nothing computed how much information any candidate carried, and "prefer items whose
difficulty is informative near theta" left the model eyeballing |b - theta|, which
ignores discrimination and guessing entirely.

Split into the two phases the algorithm actually has:

  Phase 1  LLM updates theta/SE from the graded response (3PL score + Newton update)
  code     checks the direction invariant; applies the stopping rule
  code     ranks unserved items by KL/Fisher *at the LLM's new theta*
  Phase 2  LLM picks from that scored shortlist and rewords the stem

The model still makes every decision a person would call a decision. What it no longer
does is guess at quantities the engine can compute exactly.

STOPPING IS A RULE, NOT AN OPINION

The LLM used to end the assessment by returning stop=true, and app.py obeyed it
(`stop = stop or controller.stop`). A stopping rule is part of the measurement: stop
early and the SE guarantee the report rests on is void. The model's opinion is still
collected and traced -- disagreement is a useful signal -- but code decides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from certainty import combined_certainty_pct
from engine import (
    CONFIDENCE_TARGET,
    GRID,
    MAX_QUESTIONS,
    SE_TARGET,
    check_convergence,
    eap_update,
    fisher_info,
    level_and_band,
    rank_candidates,
    selection_score,
)
from engine_log import get_logger
from llm_client import chat_json
from rephrase_guard import check_rephrase
from selection_pipeline import SelectionResult, deterministic_select
from tracing import trace_llm_response

# ---------------------------------------------------------------------------
# Phase 1 — ability update
# ---------------------------------------------------------------------------

MATH_SYSTEM = """You are the Masaar CAT math engine. Code has already graded the
response; never grade answers. Update the ability estimate after one graded answer.

The item follows a 3PL model:
  P = c + (1 - c) / (1 + exp(-a * (theta - b)))

Execute this procedure exactly, showing each numeric result:

1. P         = c + (1 - c) / (1 + exp(-a * (theta_prev - b)))
2. score     = a * (x - P) * (P - c) / (P * (1 - c))        where x = 1 if correct else 0
3. info      = a^2 * ((P - c) / (1 - c))^2 * (1 - P) / P
4. prec_new  = 1 / se_prev^2 + info
5. theta_hat = theta_prev + score / prec_new
6. se        = sqrt(1 / prec_new)

Return JSON only:
{
  "theta_hat": number between -4 and 4,
  "se": number between 0.2 and 2.5,
  "calculation_steps": ["P = ...", "score = ...", "info = ...", "theta_hat = ...", "se = ..."],
  "math_note": "one sentence explaining the update",
  "should_stop": true or false,
  "stop_reason": "short reason, or empty string"
}

Hard constraint, checked by code -- a violation means your update is discarded:
- CORRECT response   -> theta_hat must be >= theta_prev
- INCORRECT response -> theta_hat must be <= theta_prev
Do not clamp toward theta_prev to satisfy this; compute it properly.

should_stop is advisory only: code applies the stopping rule. Report what you would do.
Your `se` is recorded and compared, but code computes the SE the assessment uses and the
stopping rule runs on. Report your honest value; do not tune it.
"""

# ---------------------------------------------------------------------------
# Phase 2 — item selection, on an engine-scored shortlist
# ---------------------------------------------------------------------------

SELECT_SYSTEM = """You are the Masaar adaptive testing selector.
Execute the procedure exactly. You may ONLY choose an id from the shortlist.

Return JSON only:
{
  "selected_id": "<id from shortlist>",
  "procedure_steps": ["Step 1: ...", "Step 2: ..."],
  "selection_note": "<1-2 sentences on why this item best refines the estimate now>",
  "rule_applied": "<tie-break rule used, else 'highest information'>",
  "rephrased_stem": "<the selected stem reworded for this examinee, or the original>"
}

PROCEDURE (execute in order):
1. Read the `criterion` field in the payload — the engine has already decided it
   (KL early in the test, otherwise Fisher). Do not choose it yourself.
2. Pick the shortlist item with the highest info_score.
3. If two are within 1% relative info_score, pick the smaller b_distance.
4. If still tied, pick the least-served sub_competency.
5. If still tied, pick the higher discrimination (a).
6. selected_id MUST be one of the shortlist ids.

REPHRASING RULES:
- Reword only the selected item's stem. Never touch the options.
- Keep EVERY identifier, code fragment, literal and number exactly as written
  (e.g. `sorted(nums)`, `df.shape`, (3, 4), 1_000_000). The item is calibrated on
  them; a rewrite that drops one is rejected and discarded.
- Never restate any option's wording in the stem, especially the correct one.
- Match examinee_parameters.level_band and certainty_pct: lower certainty -> plainer
  wording; higher level -> normal technical register.
- If the original stem is already ideal, return it unchanged.
"""

DIRECTION_EPS = 1e-3
SHORTLIST_N = 5

# |Δθ̂| against the coded EAP above which the LLM's update is flagged suspect in the UI,
# and above which it is rejected outright.
#
# THESE NUMBERS ARE THIS BRANCH'S. Approach 2 runs the same comparison with the same names
# and lands on 0.30/0.50 from a distribution whose correct-maths max is 0.347. Ported here
# unmeasured, that reasoning is simply false: on this controller a *correct* Newton
# implementation reaches 1.904. Measured on both competency pools, so it is the branch and
# not the bank.
#
# Session conditions: 160 candidates/pool through the real controller, θ in [-2,+2],
# 12 steps/session, correct Newton vs a model that only nudges theta_prev by 1e-4:
#
#   correct   mean 0.119   p95 0.312   p99 0.445   max 1.904
#   lazy      mean 0.419   p95 1.009   p99 1.465   max 2.096
#
#   threshold   fires on correct   fires on lazy
#      0.35            2.8%            50.2%
#      0.45            1.0%            40.8%     <- WARN
#      0.50            0.7%            33.0%     <- REJECT
#      1.00            0.4%             5.1%
#
# THE DISTRIBUTIONS OVERLAP AND NO PER-STEP THRESHOLD SEPARATES THEM. Approach 2's tidy
# "0% of correct maths rejected" is not available here, and pretending otherwise by reusing
# its constant would be a claim the data refuses. ~0.5% of correct steps exceed 1.0 no
# matter where the gate sits: this branch lets the model own θ̂ *and* pick the item at its
# own θ̂, so a drifted estimate selects items suited to the drift, and the drift feeds
# itself. That compounding, not arithmetic error, is the tail.
#
# 0.50 is chosen knowing it trips ~9% of correct sessions, because on this branch a false
# rejection is close to free: it falls back to the coded EAP, and the coded EAP is the
# *better* estimator anyway (engine RMSE ~0.648 vs this branch's ~0.66 with the maths
# done correctly). The gate cannot cost accuracy it is not protecting. What it buys is
# 100% of degenerate sessions caught. The honest reading is the session mean -- 0.12 vs
# 0.42 -- which is what the audit panel leads with; the per-step gate is a floor under the
# damage, not proof of anything about a single step.
DEVIATION_WARN = 0.45
DEVIATION_REJECT = 0.50


@dataclass
class FullCATResult:
    theta_hat: float
    se: float
    certainty_pct: float
    stop: bool
    selection: SelectionResult | None
    # The exact grid posterior after this response, carried rather than re-derived from
    # (theta, se) so the belief keeps its shape across the session.
    posterior: np.ndarray | None = None
    stop_rule_reason: str = ""
    converged: bool = False
    calculation_steps: list[str] = field(default_factory=list)
    selection_note: str = ""
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    # Evaluation signals — never fed back to the model.
    coded_theta: float | None = None
    coded_se: float | None = None
    theta_deviation: float | None = None
    # A direction breach is a *proof* the update is wrong — the 3PL likelihood is monotone
    # in theta, so a correct answer cannot lower it. A magnitude rejection is a judgement
    # that the procedure is not being executed. Counting both under one label would report
    # a number that is not what its label says.
    invariant_violation: str = ""
    deviation_rejected: bool = False
    se_llm: float | None = None
    llm_wanted_stop: bool = False
    stop_disagreement: bool = False
    stop_reason: str = ""


def posterior_from_theta_se(theta_hat: float, se: float) -> np.ndarray:
    """Gaussian belief rebuilt from two moments.

    Retained for callers holding only (theta, se). The step no longer uses it: it carries
    the exact grid posterior instead, because rebuilding the belief from two moments every
    step discarded the posterior's shape — 3PL posteriors are genuinely skewed near the
    guessing floor — and left the E[Fisher] selection integral running over a Gaussian
    fiction. It also meant this branch's "coded fallback" was never approach 1's coded
    EAP, so the comparison was against a moving baseline.
    """
    sd = max(float(se), 0.2)
    posterior = np.exp(-0.5 * ((GRID - float(theta_hat)) / sd) ** 2)
    total = posterior.sum()
    if total <= 0:
        posterior = np.ones_like(GRID)
        total = posterior.sum()
    return posterior / total


def posterior_se_about(posterior: np.ndarray, theta_hat: float) -> float:
    """Posterior SD about `theta_hat`: sqrt(E[(theta - theta_hat)^2]).

    Code computes the SE, not the model, and on this branch that matters more than on
    approach 2 — here the *stopping rule* consumes it. The docstring above says stopping
    is a rule and not an opinion, but code owning the comparison while the model owns the
    input is only half a rule: the prompt's Newton SE, sqrt(1/(1/se_prev^2 + info)), is
    monotonically non-increasing since info >= 0 and never widened once in 48,000
    measured updates, while the grid EAP SE rises ~10% of the time after a surprising
    response. A `se <= SE_TARGET` test fed by a quantity that cannot move against it does
    not guarantee what the final report claims.

    Measured about theta_hat rather than the posterior mean, this equals
    sqrt(Var + (mean - theta_hat)^2): it reduces to the exact EAP SE when the LLM lands on
    the mean and grows when it does not. The LLM still owns theta_hat -- every decision a
    person would call a decision -- but a bad estimate now reports as uncertain instead of
    confidently wrong, and stops later rather than sooner.
    """
    return float(np.sqrt(np.sum((GRID - float(theta_hat)) ** 2 * posterior)))


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _steps(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def check_direction(theta_prev: float, theta_new: float, is_correct: bool) -> str:
    """The one invariant worth enforcing on the LLM's maths.

    The 3PL likelihood is monotone in theta, so a correct response cannot lower the
    estimate. Measured over 8000 coded EAP updates on this bank: 0 violations.

    Deliberately NOT checked: "SE must not increase". It is not an invariant — the same
    8000 updates saw SE rise in 23% of cases (up to +0.28) after a surprising response,
    which is a real posterior widening. Enforcing it would reject correct maths.
    """
    if is_correct and theta_new < theta_prev - DIRECTION_EPS:
        return (f"correct answer moved θ̂ down ({theta_prev:.3f} → {theta_new:.3f}); "
                "P(correct) rises with θ, so this cannot happen")
    if not is_correct and theta_new > theta_prev + DIRECTION_EPS:
        return (f"incorrect answer moved θ̂ up ({theta_prev:.3f} → {theta_new:.3f}); "
                "P(correct) rises with θ, so this cannot happen")
    return ""


@dataclass
class _MathOut:
    """Phase 1's result. A dataclass rather than a tuple: with the magnitude gate this
    carries eight values, and positional unpacking that long is a silent-swap waiting to
    happen."""
    theta_hat: float
    se: float
    certainty: float
    raw: dict[str, Any] = field(default_factory=dict)
    violation: str = ""
    deviation_rejected: bool = False
    fallback_used: bool = False
    se_llm: float | None = None
    # |model's θ̂ − coded EAP|, kept even when the update is rejected and theta_hat below
    # is the coded EAP's. Deriving it from theta_hat instead would record 0.0 for exactly
    # the steps where the model failed worst. None means no usable θ̂ came back at all.
    theta_deviation: float | None = None


def _llm_math(state: dict, item: dict, is_correct: bool, competency: str,
              coded_theta: float, coded_se: float, coded_certainty: float,
              post: np.ndarray, certainty_of,
              ) -> _MathOut:
    """Phase 1: the LLM's ability update, or the coded EAP when it cannot be trusted.

    The LLM supplies theta_hat. `post` is the exact grid posterior code already computed
    (Bayes, not a judgement call), and se/certainty are derived from it here rather than
    taken from the model — see posterior_se_about.

    coded_* are passed in for comparison and fallback only; they never enter the
    payload. Handing the model the answer would make this branch measure copying.
    """
    theta_prev = state["theta_hat"]
    payload = {
        "previous_state": {"theta_prev": round(theta_prev, 4),
                           "se_prev": round(state["se"], 4),
                           "q_count_before": state["q_count"]},
        "item": {"id": item["id"], "a": item["a"], "b": item["b"], "c": item["c"],
                 "difficulty": item.get("difficulty", "medium"),
                 "discrimination": item.get("discrimination", "medium")},
        "response": {"correct": bool(is_correct), "x": 1 if is_correct else 0},
        # Advertised so `should_stop` is an informed opinion rather than a guess. Code
        # applies these; the model never does.
        "stop_thresholds": {"confidence_target": CONFIDENCE_TARGET,
                            "se_target": SE_TARGET,
                            "max_questions": MAX_QUESTIONS},
    }

    try:
        data = chat_json(MATH_SYSTEM, json.dumps(payload, indent=2))
    except Exception as exc:
        trace_llm_response("cat.llm.full.math.error", input_data=payload,
                           output_data={"error": f"{type(exc).__name__}: {exc}"},
                           metadata={"competency": competency, "phase": "llm_full_math"})
        get_logger().warning("LLM_FULL_MATH | error=%s — coded EAP fallback", exc)
        return _MathOut(coded_theta, coded_se, coded_certainty,
                        {"fallback_error": str(exc)}, fallback_used=True)

    # A response with no usable theta_hat is a failed step, not one that happens to agree
    # with the engine. Defaulting it to coded_theta while reporting fallback_used=False
    # would book garbage as a successful LLM step with deviation 0.0.
    raw_theta = _num(data.get("theta_hat"), float("nan"))
    if not np.isfinite(raw_theta):
        get_logger().warning("LLM_FULL_MATH | %s | no usable theta_hat — coded EAP fallback",
                             item["id"])
        return _MathOut(coded_theta, coded_se, coded_certainty, data, fallback_used=True)

    theta_hat = float(np.clip(raw_theta, -4.0, 4.0))
    # Recorded for comparison, never used: the stopping rule must not run on a number
    # that structurally cannot widen.
    se_llm = float(np.clip(_num(data.get("se"), coded_se), 0.2, 2.5))
    se = posterior_se_about(post, theta_hat)
    certainty = certainty_of(se)
    violation = check_direction(theta_prev, theta_hat, is_correct)
    theta_dev = abs(theta_hat - coded_theta)
    deviation_rejected = not violation and theta_dev > DEVIATION_REJECT

    trace_llm_response(
        "cat.llm.full.math", input_data=payload, output_data=data,
        metadata={"competency": competency, "phase": "llm_full_math",
                  "coded_theta": round(coded_theta, 4), "coded_se": round(coded_se, 4),
                  "theta_deviation": round(theta_dev, 4),
                  # se_llm is the model's claim; se_used drives the stopping rule.
                  "se_llm": round(se_llm, 4), "se_used": round(se, 4),
                  "invariant_violation": violation,
                  "deviation_rejected": deviation_rejected},
    )

    if violation or deviation_rejected:
        get_logger().warning(
            "LLM_FULL_MATH | %s | REJECTED (%s): %s — using coded EAP", item["id"],
            "direction" if violation else "magnitude",
            violation or f"θ̂ deviates {theta_dev:.2f} from the coded EAP on the same "
                         f"evidence, past the {DEVIATION_REJECT} gate that a correct "
                         f"Newton update never reaches")
        return _MathOut(coded_theta, coded_se, coded_certainty, data,
                        violation=violation, deviation_rejected=deviation_rejected,
                        fallback_used=True, se_llm=se_llm, theta_deviation=theta_dev)

    return _MathOut(theta_hat, se, certainty, data, se_llm=se_llm,
                    theta_deviation=theta_dev)


def _llm_select(pool: list[dict], served_ids: list[str], competency: str,
                theta_hat: float, se: float, certainty: float, q_count: int,
                posterior: np.ndarray, allow_rephrase: bool) -> SelectionResult | None:
    """Phase 2. Shortlist scored by the engine at the LLM's updated theta."""
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior,
                                        top_n=SHORTLIST_N)
    if not ranked:
        return None

    level, pct, band, low_conf = level_and_band(theta_hat, se)
    id_to_sub = {q["id"]: q.get("sub_competency", "") for q in pool}
    sub_counts: dict[str, int] = {}
    for sid in served_ids:
        sub = id_to_sub.get(sid, "")
        sub_counts[sub] = sub_counts.get(sub, 0) + 1

    shortlist = [
        {
            "id": q["id"], "difficulty": q.get("difficulty", "medium"),
            "discrimination": q.get("discrimination", "medium"),
            "sub_competency": q.get("sub_competency", ""),
            "a": round(q["a"], 2), "b": round(q["b"], 2),
            "info_score": round(info, 4), "fisher_i": round(fi, 4), "kl_i": round(kl, 4),
            "b_distance": round(abs(q["b"] - theta_hat), 2),
            "sub_competency_served_count": sub_counts.get(q.get("sub_competency", ""), 0),
            "stem": q.get("stem", ""),
        }
        for q, info, fi, kl in ranked
    ]
    payload = {
        "competency": competency, "criterion": criterion,
        "theta_hat": round(theta_hat, 3), "se": round(se, 3),
        "questions_answered": q_count,
        "examinee_parameters": {"competency_level": level, "competency_pct": pct,
                                "level_band": band, "low_confidence": low_conf,
                                "certainty_pct": round(certainty, 1)},
        "served_ids": served_ids,
        "shortlist": shortlist,
    }

    try:
        data = chat_json(SELECT_SYSTEM, json.dumps(payload, indent=2))
    except Exception as exc:
        get_logger().warning("LLM_FULL_SELECT | error=%s — deterministic fallback", exc)
        fb = deterministic_select(theta_hat, q_count, pool, served_ids, posterior)
        if fb is not None:
            fb.adaptation_note = f"LLM selection unavailable ({type(exc).__name__}: {exc})."
        return fb

    trace_llm_response("cat.llm.full.select", input_data=payload, output_data=data,
                       metadata={"competency": competency, "phase": "llm_full_select"})

    id_map = {q["id"]: q for q, *_ in ranked}
    selected_id = str(data.get("selected_id", "")).strip()
    if selected_id not in id_map:
        get_logger().warning("LLM_FULL_SELECT | invalid id=%s — deterministic fallback",
                             selected_id)
        return deterministic_select(theta_hat, q_count, pool, served_ids, posterior)

    q = id_map[selected_id]

    rephrase_reason = rephrase_code = ""
    if allow_rephrase:
        checked = check_rephrase(q.get("stem", ""), str(data.get("rephrased_stem", "")).strip(),
                                 q["options"], q["answer_index"])
        rephrased_stem = checked.stem
        if not checked.ok:
            rephrase_reason, rephrase_code = checked.reason, checked.code
            get_logger().warning("REPHRASE_REJECTED | %s | id=%s | code=%s | %s",
                                 competency, q["id"], checked.code, checked.reason)
            trace_llm_response(
                "cat.llm.rephrase.rejected",
                input_data={"item_id": q["id"], "original_stem": q.get("stem", ""),
                            "proposed_stem": str(data.get("rephrased_stem", ""))},
                output_data={"reason": checked.reason, "code": checked.code},
                metadata={"competency": competency, "phase": "rephrase_guard"})
    else:
        rephrased_stem = q.get("stem", "")

    return SelectionResult(
        item=q,
        info_score=float(selection_score(theta_hat, q_count, q, posterior)),
        criterion=criterion,  # the engine's, not the model's claim
        fisher_i=float(fisher_info(theta_hat, q)),
        llm_used=True,
        procedure_steps=_steps(data.get("procedure_steps")),
        adaptation_note=str(data.get("selection_note", "")),
        rule_applied=str(data.get("rule_applied", "")),
        shortlist_ids=[s["id"] for s in shortlist],
        rephrased_stem=rephrased_stem,
        rephrase_rejected_reason=rephrase_reason,
        rephrase_rejected_code=rephrase_code,
    )


def llm_full_step(
    state: dict,
    pool: list[dict],
    competency: str,
    *,
    answered_item: dict | None = None,
    is_correct: bool | None = None,
    use_llm: bool = True,
    allow_rephrase: bool = True,
) -> FullCATResult:
    """Run one full CAT step: LLM math, code-enforced stop, LLM selection."""
    next_q_count = state["q_count"] + (1 if answered_item is not None else 0)
    served_ids = list(state["served_ids"])
    if answered_item is not None and answered_item["id"] not in served_ids:
        served_ids.append(answered_item["id"])

    # --- Phase 1: ability update ------------------------------------------------
    math = _MathOut(state["theta_hat"], state["se"], state.get("certainty_pct", 0.0))
    if answered_item is not None and is_correct is not None:
        # Bayes, not a judgement call: code always multiplies prior by likelihood and
        # carries the exact grid belief forward. The LLM supplies theta_hat only.
        posterior, coded_theta, coded_se = eap_update(
            state["posterior"], answered_item, is_correct)

        def certainty_of(se_value: float) -> float:
            return combined_certainty_pct(
                se_value, state.get("self_confidence", "low"), next_q_count,
                state.get("prior_sd", 2.0), se_start=state.get("se_start"))

        coded_certainty = certainty_of(coded_se)
        if use_llm:
            math = _llm_math(state, answered_item, is_correct, competency,
                             coded_theta, coded_se, coded_certainty, posterior, certainty_of)
        else:
            math = _MathOut(coded_theta, coded_se, coded_certainty, fallback_used=True)
    else:
        coded_theta, coded_se = math.theta_hat, math.se
        posterior = state["posterior"]

    theta_hat, se, certainty = math.theta_hat, math.se, math.certainty
    math_raw = math.raw

    # --- Stopping: a rule, applied by code, on a quantity code computed ----------
    # `se` here is posterior_se_about, not the model's Newton se. Code owning the
    # comparison while the model owned the input was only half a rule.
    llm_wanted_stop = bool(math_raw.get("should_stop", False))
    unserved = [q for q in pool if q["id"] not in served_ids]
    level_history = list(state.get("level_history", []))
    if answered_item is not None and is_correct is not None:
        level_history.append(level_and_band(theta_hat, se)[0])
    conv = check_convergence(certainty, level_history, next_q_count)
    rule_stop = conv.stop or not unserved
    stop_disagreement = llm_wanted_stop != rule_stop
    if stop_disagreement:
        get_logger().info(
            "LLM_FULL_STOP | model wanted stop=%s, rule says stop=%s (rule wins) | "
            "reason=%s certainty=%.1f%% se=%.3f q=%d/%d",
            llm_wanted_stop, rule_stop, conv.reason or "bank_exhausted",
            certainty, se, next_q_count, MAX_QUESTIONS)

    result = FullCATResult(
        theta_hat=theta_hat, se=se, certainty_pct=certainty, stop=rule_stop, selection=None,
        posterior=posterior, stop_rule_reason=conv.reason, converged=conv.converged,
        calculation_steps=_steps(math_raw.get("calculation_steps")),
        selection_note=str(math_raw.get("math_note", "")),
        fallback_used=math.fallback_used, raw=math_raw,
        coded_theta=coded_theta, coded_se=coded_se,
        theta_deviation=math.theta_deviation,
        invariant_violation=math.violation,
        deviation_rejected=math.deviation_rejected,
        se_llm=math.se_llm,
        llm_wanted_stop=llm_wanted_stop,
        stop_disagreement=stop_disagreement,
        stop_reason=str(math_raw.get("stop_reason", "")),
    )
    if rule_stop:
        return result

    # --- Phase 2: selection on an engine-scored shortlist -----------------------
    if use_llm:
        selection = _llm_select(pool, served_ids, competency, theta_hat, se,
                                certainty, next_q_count, posterior, allow_rephrase)
    else:
        selection = deterministic_select(theta_hat, next_q_count, pool, served_ids, posterior)
        if selection is not None:
            selection.adaptation_note = "LLM controller disabled — engine selection."

    result.selection = selection
    result.stop = selection is None
    return result
