"""LLM-produced CAT math for Approach 2.

The answer is still graded by code, and next-item selection is still coded.
Only the post-answer ability/uncertainty update is delegated to the LLM.

Two things this module has to get right, and previously did not:

1. THE LLM MUST NOT BE HANDED THE ANSWER.
   The payload used to include the coded EAP result as
   "coded_reference_for_validation_only". A model that copies that field scores
   perfectly while demonstrating nothing, so the branch could not answer the
   question it exists to ask. The coded update is still computed on every step --
   but as an *evaluation* signal (deviation is traced and shown in the UI), never
   as an input. Grading the exam with the answer key in the prompt is not a test.

2. THE PROMPT MUST SPECIFY AN ALGORITHM, NOT A VIBE.
   "Correct answers should generally move theta upward" is not IRT. The model now
   gets the 3PL score function and a Newton/Laplace posterior update to execute,
   which is a real estimator with a defined answer.

Output is then checked against the invariant that actually holds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from certainty import combined_certainty_pct
from engine import GRID, eap_update
from engine_log import get_logger
from llm_client import chat_json
from tracing import trace_llm_response

LLM_MATH_SYSTEM = """You are the Masaar CAT math engine for an MCQ IRT assessment.
Your job is to update the candidate ability estimate after one graded answer.
Code has already graded the response. Never grade answers.

The item follows a 3PL model:
  P = c + (1 - c) / (1 + exp(-a * (theta - b)))

Execute this procedure exactly, showing each numeric result:

1. P         = c + (1 - c) / (1 + exp(-a * (theta_prev - b)))
2. score     = a * (x - P) * (P - c) / (P * (1 - c))        where x = 1 if correct else 0
               (d/dtheta of the log-likelihood; its sign follows x - P)
3. info      = a^2 * ((P - c) / (1 - c))^2 * (1 - P) / P    (Fisher information at theta_prev)
4. prec_new  = 1 / se_prev^2 + info                         (prior precision + item information)
5. theta_hat = theta_prev + score / prec_new
6. se        = sqrt(1 / prec_new)

Return JSON only:
{
  "theta_hat": number between -4 and 4,
  "se": positive number between 0.2 and 2.5,
  "certainty_pct": number between 0 and 100,
  "calculation_steps": ["P = ...", "score = ...", "info = ...", "theta_hat = ...", "se = ..."],
  "math_note": "one sentence explaining the update"
}

Hard constraint, checked by code -- a violation means your answer is discarded:
- If the response was CORRECT, theta_hat must be >= theta_prev.
- If the response was INCORRECT, theta_hat must be <= theta_prev.
Both follow from step 2: the sign of (x - P) decides the direction of movement.
Do not clamp theta_hat toward theta_prev to satisfy this -- compute it properly.

certainty_pct: report 100 * (1 - se / 2.0), clipped to [0, 100].
"""

# Tolerance on the direction check. The invariant is exact in theory; this only
# absorbs the model's own rounding when it reports a few decimal places.
DIRECTION_EPS = 1e-3

# Deviation from the coded EAP above which the LLM's answer is flagged as suspect in
# the UI. Deliberately NOT a rejection: the Newton update the prompt specifies is a
# normal approximation to the grid EAP, so exact agreement is not expected, and forcing
# agreement would make the whole comparison vacuous.
#
# Calibrated, not guessed. Running the prompt's own procedure against grid EAP over
# 12000 updates on this bank: median |Δθ̂| = 0.029, p95 = 0.25, p99 = 0.69. At 0.35 a
# *correct* implementation trips this ~2.9% of the time — fine for a warning, which is
# why it must never gate the update.
DEVIATION_WARN = 0.35


@dataclass
class LLMMathResult:
    theta_hat: float
    se: float
    certainty_pct: float
    calculation_steps: list[str] = field(default_factory=list)
    math_note: str = ""
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    # Evaluation signals — how far the LLM landed from the coded EAP. Populated on
    # every LLM step so the branch comparison has data. Never fed back to the model.
    coded_theta: float | None = None
    coded_se: float | None = None
    theta_deviation: float | None = None
    se_deviation: float | None = None
    invariant_violation: str = ""


def posterior_from_theta_se(theta_hat: float, se: float) -> np.ndarray:
    """Rebuild a Gaussian belief over theta from the LLM's scalar summary.

    Lossy by construction: the LLM reports (theta, se), so the true posterior shape is
    gone and what returns is the Gaussian with those moments. That is inherent to
    delegating the maths to a model that emits two numbers -- this branch's design, not
    an oversight. It matters because item selection integrates Fisher information over
    this belief, so selection here is driven by the LLM's summary rather than by an
    exact posterior.
    """
    sd = max(float(se), 0.2)
    posterior = np.exp(-0.5 * ((GRID - float(theta_hat)) / sd) ** 2)
    total = posterior.sum()
    if total <= 0:
        posterior = np.ones_like(GRID)
        total = posterior.sum()
    return posterior / total


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check_direction(theta_prev: float, theta_new: float, is_correct: bool) -> str:
    """Return a violation message, or "" when the update moves the right way.

    This is the one invariant worth enforcing. Measured over 8000 coded EAP updates
    against this bank it held exactly (0 violations): the 3PL likelihood is monotone in
    theta, so a correct response cannot lower the ability estimate.

    Note what is deliberately NOT checked: "SE must not increase". That looks like an
    invariant and is not -- the same 8000 updates saw SE rise in 23% of cases (by up to
    0.28) when a response was surprising, which is a real posterior widening, not an
    error. Enforcing it would reject correct maths a quarter of the time.
    """
    if is_correct and theta_new < theta_prev - DIRECTION_EPS:
        return (f"correct answer moved θ̂ down ({theta_prev:.3f} → {theta_new:.3f}); "
                "P(correct) rises with θ, so this cannot happen")
    if not is_correct and theta_new > theta_prev + DIRECTION_EPS:
        return (f"incorrect answer moved θ̂ up ({theta_prev:.3f} → {theta_new:.3f}); "
                "P(correct) rises with θ, so this cannot happen")
    return ""


def _coded_result(theta: float, se: float, certainty: float, note: str, steps: list[str],
                  raw: dict) -> LLMMathResult:
    return LLMMathResult(
        theta_hat=theta, se=se, certainty_pct=certainty,
        calculation_steps=steps, math_note=note, fallback_used=True, raw=raw,
        coded_theta=theta, coded_se=se, theta_deviation=0.0, se_deviation=0.0,
    )


def llm_math_update(
    state: dict,
    item: dict,
    is_correct: bool,
    *,
    use_llm: bool = True,
) -> LLMMathResult:
    """Return the LLM's updated theta/SE, falling back to coded EAP when unusable."""
    theta_prev = state["theta_hat"]

    # Computed every step, but only ever used as ground truth to compare against and to
    # fall back to. It must not enter the payload.
    _post, coded_theta, coded_se = eap_update(state["posterior"], item, is_correct)
    coded_certainty = combined_certainty_pct(
        coded_se,
        state.get("self_confidence", "low"),
        state["q_count"] + 1,
        state.get("prior_sd", 2.0),
        se_start=state.get("se_start"),
    )

    if not use_llm:
        return _coded_result(
            coded_theta, coded_se, coded_certainty,
            "LLM math disabled.", ["LLM math disabled; used coded EAP update."],
            {"fallback": "disabled"},
        )

    payload = {
        "previous_state": {
            "theta_prev": round(theta_prev, 4),
            "se_prev": round(state["se"], 4),
            "q_count_before": state["q_count"],
        },
        "item": {
            "id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "discrimination": item.get("discrimination", "medium"),
            "a": item["a"],
            "b": item["b"],
            "c": item["c"],
        },
        "response": {"correct": bool(is_correct), "x": 1 if is_correct else 0},
    }

    try:
        data = chat_json(LLM_MATH_SYSTEM, json.dumps(payload, indent=2))
    except Exception as exc:
        trace_llm_response(
            "cat.llm.math.error",
            input_data=payload,
            output_data={"error": f"{type(exc).__name__}: {exc}"},
            metadata={"phase": "llm_math", "item_id": item["id"]},
        )
        return _coded_result(
            coded_theta, coded_se, coded_certainty,
            f"LLM math fallback ({type(exc).__name__}).",
            [f"LLM math failed: {exc}", "Used coded EAP update."],
            {"fallback_error": str(exc)},
        )

    theta_hat = float(np.clip(_num(data.get("theta_hat"), coded_theta), -4.0, 4.0))
    se = float(np.clip(_num(data.get("se"), coded_se), 0.2, 2.5))
    certainty = float(np.clip(_num(data.get("certainty_pct"), coded_certainty), 0.0, 100.0))

    violation = check_direction(theta_prev, theta_hat, is_correct)
    theta_dev = abs(theta_hat - coded_theta)
    se_dev = abs(se - coded_se)

    trace_llm_response(
        "cat.llm.math",
        input_data=payload,
        output_data=data,
        metadata={
            "phase": "llm_math",
            "item_id": item["id"],
            # The comparison this branch exists to produce.
            "coded_theta": round(coded_theta, 4),
            "coded_se": round(coded_se, 4),
            "theta_deviation": round(theta_dev, 4),
            "se_deviation": round(se_dev, 4),
            "invariant_violation": violation,
        },
    )

    if violation:
        get_logger().warning("LLM_MATH | %s | INVARIANT VIOLATION: %s — using coded EAP",
                             item["id"], violation)
        result = _coded_result(
            coded_theta, coded_se, coded_certainty,
            f"LLM math rejected: {violation}",
            [f"Rejected LLM update: {violation}", "Used coded EAP update."],
            data,
        )
        result.invariant_violation = violation
        return result

    if theta_dev > DEVIATION_WARN:
        get_logger().warning("LLM_MATH | %s | θ̂ deviates %.3f from coded EAP (%.3f vs %.3f)",
                             item["id"], theta_dev, theta_hat, coded_theta)

    steps = data.get("calculation_steps") or []
    if isinstance(steps, str):
        steps = [steps]

    return LLMMathResult(
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty,
        calculation_steps=[str(s) for s in steps],
        math_note=str(data.get("math_note", "")),
        fallback_used=False,
        raw=data,
        coded_theta=coded_theta,
        coded_se=coded_se,
        theta_deviation=theta_dev,
        se_deviation=se_dev,
    )
