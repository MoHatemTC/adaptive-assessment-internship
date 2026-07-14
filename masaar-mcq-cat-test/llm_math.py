"""LLM-produced CAT math for Approach 2.

The answer is still graded by code, and next-item selection is still coded.
Only the post-answer ability/uncertainty update is delegated to the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from certainty import combined_certainty_pct
from engine import GRID, eap_update
from llm_client import chat_json
from tracing import trace_llm_response

LLM_MATH_SYSTEM = """You are the Masaar CAT math engine for an MCQ IRT assessment.
Your job is to update the candidate ability estimate after one answer.

Use the provided previous theta_hat, previous SE, item parameters, and whether
the response was correct. The item uses a 3PL model:
P(correct|theta)=c+(1-c)/(1+exp(-a*(theta-b))).

Return JSON only:
{
  "theta_hat": number between -4 and 4,
  "se": positive number between 0.2 and 2.5,
  "certainty_pct": number between 0 and 100,
  "calculation_steps": ["short step 1", "short step 2"],
  "math_note": "one sentence explaining the update"
}

Correct answers should generally move theta upward; incorrect answers should
generally move theta downward. Higher discrimination items should reduce SE more
than low discrimination items when they are informative.
"""


@dataclass
class LLMMathResult:
    theta_hat: float
    se: float
    certainty_pct: float
    calculation_steps: list[str] = field(default_factory=list)
    math_note: str = ""
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def posterior_from_theta_se(theta_hat: float, se: float) -> np.ndarray:
    """Approximate a display posterior from the LLM-provided theta/SE."""
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


def _validated_result(
    data: dict[str, Any],
    *,
    fallback_theta: float,
    fallback_se: float,
    fallback_certainty: float,
    fallback_used: bool = False,
) -> LLMMathResult:
    theta_hat = float(np.clip(_num(data.get("theta_hat"), fallback_theta), -4.0, 4.0))
    se = float(np.clip(_num(data.get("se"), fallback_se), 0.2, 2.5))
    certainty = float(np.clip(_num(data.get("certainty_pct"), fallback_certainty), 0.0, 100.0))
    steps = data.get("calculation_steps") or []
    if isinstance(steps, str):
        steps = [steps]
    return LLMMathResult(
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty,
        calculation_steps=[str(step) for step in steps],
        math_note=str(data.get("math_note", "")),
        fallback_used=fallback_used,
        raw=data,
    )


def llm_math_update(
    state: dict,
    item: dict,
    is_correct: bool,
    *,
    use_llm: bool = True,
) -> LLMMathResult:
    """Return the LLM's updated theta/SE, with coded fallback if unavailable."""
    _fallback_posterior, fallback_theta, fallback_se = eap_update(
        state["posterior"],
        item,
        is_correct,
    )
    fallback_certainty = combined_certainty_pct(
        fallback_se,
        state.get("self_confidence", "low"),
        state["q_count"] + 1,
        state.get("prior_sd", 2.0),
        se_start=state.get("se_start"),
    )

    payload = {
        "previous_state": {
            "theta_hat": round(state["theta_hat"], 4),
            "se": round(state["se"], 4),
            "q_count_before": state["q_count"],
            "certainty_pct": round(state.get("certainty_pct", 0.0), 2),
        },
        "item": {
            "id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "discrimination": item.get("discrimination", "medium"),
            "a": item["a"],
            "b": item["b"],
            "c": item["c"],
        },
        "response": {"correct": bool(is_correct)},
        "coded_reference_for_validation_only": {
            "theta_hat": round(fallback_theta, 4),
            "se": round(fallback_se, 4),
            "certainty_pct": round(fallback_certainty, 2),
        },
    }

    if not use_llm:
        return LLMMathResult(
            theta_hat=fallback_theta,
            se=fallback_se,
            certainty_pct=fallback_certainty,
            calculation_steps=["LLM math disabled; used coded fallback."],
            math_note="LLM math disabled.",
            fallback_used=True,
            raw={"fallback": "disabled"},
        )

    try:
        data = chat_json(LLM_MATH_SYSTEM, json.dumps(payload, indent=2))
        trace_llm_response(
            "cat.llm.math",
            input_data=payload,
            output_data=data,
            metadata={"phase": "llm_math", "item_id": item["id"]},
        )
        return _validated_result(
            data,
            fallback_theta=fallback_theta,
            fallback_se=fallback_se,
            fallback_certainty=fallback_certainty,
        )
    except Exception as exc:
        trace_llm_response(
            "cat.llm.math.error",
            input_data=payload,
            output_data={"error": f"{type(exc).__name__}: {exc}"},
            metadata={"phase": "llm_math", "item_id": item["id"]},
        )
        return LLMMathResult(
            theta_hat=fallback_theta,
            se=fallback_se,
            certainty_pct=fallback_certainty,
            calculation_steps=[f"LLM math failed: {exc}", "Used coded fallback update."],
            math_note=f"LLM math fallback ({type(exc).__name__}).",
            fallback_used=True,
            raw={"fallback_error": str(exc)},
        )
