"""Pure LLM CAT controller for Approach 3.

The model owns the CAT decisions in this branch: ability update, uncertainty,
stop decision, and next item. Code grades MCQs, parses JSON, bounds numeric
outputs, prevents duplicate or invented items, checks rephrases for answer-key
leaks, and records a coded EAP comparator for audit only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from certainty import combined_certainty_pct
from engine import (
    GRID,
    MAX_QUESTIONS,
    eap_update,
    fisher_info,
    level_and_band,
    selection_score,
)
from engine_log import get_logger
from llm_client import chat_json
from rephrase_guard import check_rephrase
from selection_pipeline import SelectionResult
from tracing import trace_llm_response

CONTROLLER_SYSTEM = """You are the Masaar full CAT controller.
You control the adaptive testing process after code has graded any MCQ response.

Code does only hard validation:
- parses your JSON
- clamps theta_hat, se, and certainty_pct to allowed ranges
- rejects invented or already-served selected_id values
- rejects answer-leaking or meaning-flipping rephrases
- grades MCQs by exact answer_index

You must do the CAT work:
1. Update theta_hat and se from the previous state and latest graded response.
2. Decide whether the competency should stop now.
3. If not stopping, choose the next item from available_items.
4. Rephrase only the selected stem when helpful.

Use IRT/CAT reasoning. Each item includes a, b, c, difficulty, discrimination,
sub_competency, and stem. Prefer items that will refine ability near your theta_hat,
while also keeping sub-competency coverage reasonable. Do not rely only on difficulty.

Return JSON only:
{
  "theta_hat": number between -4 and 4,
  "se": number between 0.2 and 2.5,
  "certainty_pct": number between 0 and 100,
  "should_stop": true or false,
  "converged": true or false,
  "stop_reason": "confidence | stable_level | max_questions | bank_exhausted | other short reason",
  "selected_id": "id from available_items, or empty string if stopping",
  "calculation_steps": ["brief numeric update steps"],
  "selection_reason": "why this item is best next, or why no item is needed",
  "rule_applied": "main CAT rule you used",
  "rephrased_stem": "selected stem rewritten for the examinee, or unchanged"
}

Hard constraints:
- If should_stop is false, selected_id must be one of available_items.
- Never select an id from served_ids.
- Never invent an id.
- Never reveal or imply the correct answer in rephrased_stem.
- Preserve code fragments, identifiers, literals, numbers, and negation in rephrased_stem.
"""

DIRECTION_EPS = 1e-3
DEVIATION_REJECT = 0.50


@dataclass
class FullCATResult:
    theta_hat: float
    se: float
    certainty_pct: float
    stop: bool
    selection: SelectionResult | None
    posterior: np.ndarray | None = None
    stop_rule_reason: str = ""
    converged: bool = False
    calculation_steps: list[str] = field(default_factory=list)
    selection_note: str = ""
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    coded_theta: float | None = None
    coded_se: float | None = None
    theta_deviation: float | None = None
    invariant_violation: str = ""
    deviation_rejected: bool = False
    se_llm: float | None = None
    llm_wanted_stop: bool = False
    stop_disagreement: bool = False
    stop_reason: str = ""
    invalid_llm_step: bool = False
    invalid_reason: str = ""


def posterior_from_theta_se(theta_hat: float, se: float) -> np.ndarray:
    """Display/selection belief rebuilt from the LLM-owned scalar state."""
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


def _steps(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def check_direction(theta_prev: float, theta_new: float, is_correct: bool) -> str:
    """Audit-only direction invariant for the LLM's math."""
    if is_correct and theta_new < theta_prev - DIRECTION_EPS:
        return f"correct answer moved theta down ({theta_prev:.3f} -> {theta_new:.3f})"
    if not is_correct and theta_new > theta_prev + DIRECTION_EPS:
        return f"incorrect answer moved theta up ({theta_prev:.3f} -> {theta_new:.3f})"
    return ""


def _candidate_payload(pool: list[dict], served_ids: list[str]) -> list[dict]:
    served = set(served_ids)
    return [
        {
            "id": q["id"],
            "difficulty": q.get("difficulty", "medium"),
            "discrimination": q.get("discrimination", "medium"),
            "sub_competency": q.get("sub_competency", ""),
            "a": round(float(q["a"]), 3),
            "b": round(float(q["b"]), 3),
            "c": round(float(q["c"]), 3),
            "stem": q.get("stem", ""),
        }
        for q in pool
        if q["id"] not in served
    ]


def _invalid_result(state: dict, reason: str, raw: dict[str, Any] | None = None) -> FullCATResult:
    get_logger().warning("LLM_FULL_INVALID | %s", reason)
    return FullCATResult(
        theta_hat=float(state.get("theta_hat", 0.0)),
        se=float(state.get("se", 2.0)),
        certainty_pct=float(state.get("certainty_pct", 0.0)),
        stop=True,
        selection=None,
        posterior=state.get("posterior"),
        stop_rule_reason="invalid_llm_step",
        converged=False,
        fallback_used=False,
        raw=raw or {},
        invalid_llm_step=True,
        invalid_reason=reason,
        stop_reason=reason,
    )


def _selection_from_llm(
    data: dict[str, Any],
    pool: list[dict],
    served_ids: list[str],
    theta_hat: float,
    se: float,
    q_count: int,
    posterior: np.ndarray,
    allow_rephrase: bool,
) -> SelectionResult | str | None:
    selected_id = str(data.get("selected_id", "")).strip()
    if not selected_id:
        return "selected_id missing while should_stop is false"

    served = set(served_ids)
    if selected_id in served:
        return f"selected_id {selected_id!r} was already served"

    id_map = {q["id"]: q for q in pool if q["id"] not in served}
    q = id_map.get(selected_id)
    if q is None:
        return f"selected_id {selected_id!r} is not an available item"

    rephrase_reason = rephrase_code = ""
    if allow_rephrase:
        checked = check_rephrase(
            q.get("stem", ""),
            str(data.get("rephrased_stem", "")).strip(),
            q["options"],
            q["answer_index"],
        )
        rephrased_stem = checked.stem
        if not checked.ok:
            rephrase_reason, rephrase_code = checked.reason, checked.code
            get_logger().warning(
                "REPHRASE_REJECTED | id=%s | code=%s | %s",
                q["id"], checked.code, checked.reason,
            )
    else:
        rephrased_stem = q.get("stem", "")

    criterion = "LLM full-controller choice"
    return SelectionResult(
        item=q,
        info_score=float(selection_score(theta_hat, q_count, q, posterior)),
        criterion=criterion,
        fisher_i=float(fisher_info(theta_hat, q)),
        llm_used=True,
        procedure_steps=_steps(data.get("calculation_steps")),
        adaptation_note=str(data.get("selection_reason", "")),
        rule_applied=str(data.get("rule_applied", "LLM full-controller choice")),
        shortlist_ids=list(id_map.keys()),
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
    """Run one pure LLM CAT step with hard code validation only."""
    if not use_llm:
        return _invalid_result(state, "LLM controller disabled")

    served_ids = list(state.get("served_ids", []))
    next_q_count = int(state.get("q_count", 0)) + (1 if answered_item is not None else 0)
    if answered_item is not None and answered_item["id"] not in served_ids:
        served_ids.append(answered_item["id"])

    available = _candidate_payload(pool, served_ids)
    if not available:
        return FullCATResult(
            theta_hat=float(state.get("theta_hat", 0.0)),
            se=float(state.get("se", 2.0)),
            certainty_pct=float(state.get("certainty_pct", 0.0)),
            stop=True,
            selection=None,
            posterior=state.get("posterior"),
            stop_rule_reason="bank_exhausted",
            stop_reason="bank_exhausted",
        )

    payload = {
        "competency": competency,
        "previous_state": {
            "theta_hat": round(float(state.get("theta_hat", 0.0)), 4),
            "se": round(float(state.get("se", 2.0)), 4),
            "certainty_pct": round(float(state.get("certainty_pct", 0.0)), 2),
            "q_count": int(state.get("q_count", 0)),
            "level_history": list(state.get("level_history", [])),
        },
        "latest_response": None,
        "questions_answered_after_update": next_q_count,
        "served_ids": served_ids,
        "available_items": available,
        "examinee_parameters": {},
        "hard_limits": {
            "theta_range": [-4, 4],
            "se_range": [0.2, 2.5],
            "max_questions": MAX_QUESTIONS,
        },
    }
    level, pct, band, low_conf = level_and_band(
        float(state.get("theta_hat", 0.0)),
        float(state.get("se", 2.0)),
    )
    payload["examinee_parameters"] = {
        "competency_level": level,
        "competency_pct": pct,
        "level_band": band,
        "low_confidence": low_conf,
    }
    if answered_item is not None and is_correct is not None:
        payload["latest_response"] = {
            "item": {
                "id": answered_item["id"],
                "a": answered_item["a"],
                "b": answered_item["b"],
                "c": answered_item["c"],
                "difficulty": answered_item.get("difficulty", "medium"),
                "discrimination": answered_item.get("discrimination", "medium"),
            },
            "correct": bool(is_correct),
            "x": 1 if is_correct else 0,
        }

    try:
        data = chat_json(CONTROLLER_SYSTEM, json.dumps(payload, indent=2))
    except Exception as exc:
        trace_llm_response(
            "cat.llm.full_controller.error",
            input_data=payload,
            output_data={"error": f"{type(exc).__name__}: {exc}"},
            metadata={"competency": competency, "phase": "llm_full_controller"},
        )
        return _invalid_result(state, f"{type(exc).__name__}: {exc}")

    trace_llm_response(
        "cat.llm.full_controller",
        input_data=payload,
        output_data=data,
        metadata={"competency": competency, "phase": "llm_full_controller"},
    )

    raw_theta = _num(data.get("theta_hat"), float("nan"))
    raw_se = _num(data.get("se"), float("nan"))
    raw_certainty = _num(data.get("certainty_pct"), float("nan"))
    if not np.isfinite(raw_theta):
        return _invalid_result(state, "missing or invalid theta_hat", data)
    if not np.isfinite(raw_se):
        return _invalid_result(state, "missing or invalid se", data)
    if not np.isfinite(raw_certainty):
        return _invalid_result(state, "missing or invalid certainty_pct", data)

    theta_hat = float(np.clip(raw_theta, -4.0, 4.0))
    se = float(np.clip(raw_se, 0.2, 2.5))
    certainty = float(np.clip(raw_certainty, 0.0, 100.0))
    posterior = posterior_from_theta_se(theta_hat, se)

    coded_theta = coded_se = theta_dev = None
    invariant = ""
    if answered_item is not None and is_correct is not None:
        _post, coded_theta, coded_se = eap_update(state["posterior"], answered_item, is_correct)
        theta_dev = abs(theta_hat - coded_theta)
        invariant = check_direction(float(state.get("theta_hat", 0.0)), theta_hat, is_correct)
        if invariant:
            return _invalid_result(state, invariant, data)

    should_stop = bool(data.get("should_stop", False)) or next_q_count >= MAX_QUESTIONS
    stop_reason = str(data.get("stop_reason", "") or ("max_questions" if next_q_count >= MAX_QUESTIONS else ""))
    selection = None
    if not should_stop:
        selected = _selection_from_llm(
            data, pool, served_ids, theta_hat, se, next_q_count, posterior, allow_rephrase
        )
        if isinstance(selected, str):
            return _invalid_result(state, selected, data)
        selection = selected

    return FullCATResult(
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty,
        stop=should_stop,
        selection=selection,
        posterior=posterior,
        stop_rule_reason=stop_reason or ("llm_stop" if should_stop else ""),
        converged=bool(data.get("converged", False)),
        calculation_steps=_steps(data.get("calculation_steps")),
        selection_note=str(data.get("selection_reason", "")),
        fallback_used=False,
        raw=data,
        coded_theta=coded_theta,
        coded_se=coded_se,
        theta_deviation=theta_dev,
        invariant_violation="",
        deviation_rejected=False,
        se_llm=se,
        llm_wanted_stop=should_stop,
        stop_disagreement=False,
        stop_reason=stop_reason,
    )
