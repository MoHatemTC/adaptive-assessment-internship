"""Full LLM CAT controller for Approach 3.

In this branch the LLM updates theta/SE and chooses the next question. Code
still grades MCQs, validates the response, and enforces safety boundaries.
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
    SE_TARGET,
    eap_update,
    fisher_info,
    level_and_band,
    selection_score,
)
from llm_client import chat_json
from selection_pipeline import SelectionResult, deterministic_select
from tracing import trace_llm_response

LLM_FULL_CAT_SYSTEM = """You are the Masaar full CAT controller.
You control both:
1. the post-answer IRT-style math update (theta_hat, SE, certainty)
2. the next MCQ selection from the provided candidates

Code has already graded whether the answer was correct. Never grade answers.
Never invent item ids. If selecting a question, selected_id must be one of the
candidate ids. If no more questions are needed, set stop=true.

Use this 3PL probability model for reasoning:
P(correct|theta)=c+(1-c)/(1+exp(-a*(theta-b))).

Return JSON only:
{
  "theta_hat": number between -4 and 4,
  "se": positive number between 0.2 and 2.5,
  "certainty_pct": number between 0 and 100,
  "stop": true or false,
  "selected_id": "candidate id, or empty string if stop=true",
  "criterion_used": "KL" or "Fisher" or "LLM-CAT",
  "calculation_steps": ["short math step"],
  "selection_note": "why the next item or stop decision is appropriate",
  "rule_applied": "short rule name",
  "rephrased_stem": "selected question stem rewritten for the examinee, or empty if stop=true",
  "stop_reason": "short reason, or empty string"
}

Correct answers should generally raise theta; incorrect answers should generally
lower theta. Prefer items whose difficulty is informative near theta and whose
discrimination is useful. Stop when uncertainty is low enough, the max question
count is reached, or the remaining bank is insufficient.

REPHRASING RULES:
- If stop=false, rephrase only the selected item's stem.
- Preserve technical meaning, answer, options, and intended difficulty.
- Do not reveal hints or the correct answer.
- Use examinee_parameters.level_band and certainty_pct:
  lower certainty -> clearer wording; higher level -> normal technical phrasing.
- If the original stem is already ideal, return it unchanged.
"""


@dataclass
class FullCATResult:
    theta_hat: float
    se: float
    certainty_pct: float
    stop: bool
    selection: SelectionResult | None
    calculation_steps: list[str] = field(default_factory=list)
    selection_note: str = ""
    fallback_used: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def posterior_from_theta_se(theta_hat: float, se: float) -> np.ndarray:
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


def _candidate_payload(candidates: list[dict]) -> list[dict]:
    return [
        {
            "id": q["id"],
            "difficulty": q.get("difficulty", "medium"),
            "discrimination": q.get("discrimination", "medium"),
            "sub_competency": q.get("sub_competency", ""),
            "a": q["a"],
            "b": q["b"],
            "c": q["c"],
            "stem": q.get("stem", ""),
        }
        for q in candidates
    ]


def _selection_from_item(
    item: dict,
    *,
    theta_hat: float,
    q_count: int,
    criterion: str,
    llm_used: bool,
    note: str,
    rule: str,
    steps: list[str],
    candidate_ids: list[str],
    rephrased_stem: str = "",
) -> SelectionResult:
    return SelectionResult(
        item=item,
        info_score=float(selection_score(theta_hat, q_count, item)),
        criterion=criterion,
        fisher_i=float(fisher_info(theta_hat, item)),
        llm_used=llm_used,
        procedure_steps=steps,
        adaptation_note=note,
        rule_applied=rule,
        shortlist_ids=candidate_ids,
        rephrased_stem=rephrased_stem,
    )


def _fallback_result(
    state: dict,
    pool: list[dict],
    served_ids: list[str],
    *,
    theta_hat: float,
    se: float,
    certainty_pct: float,
    q_count: int,
    reason: str,
) -> FullCATResult:
    selection = deterministic_select(theta_hat, q_count, pool, served_ids)
    if selection is not None:
        selection.adaptation_note = f"Full LLM fallback: {reason}"
        selection.procedure_steps = [f"LLM full controller fallback: {reason}", *selection.procedure_steps]
    return FullCATResult(
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty_pct,
        stop=selection is None,
        selection=selection,
        calculation_steps=[reason],
        selection_note=reason,
        fallback_used=True,
        raw={"fallback": reason},
    )


def llm_full_step(
    state: dict,
    pool: list[dict],
    competency: str,
    *,
    answered_item: dict | None = None,
    is_correct: bool | None = None,
    use_llm: bool = True,
) -> FullCATResult:
    """Ask the LLM to update state and select the next item."""
    next_q_count = state["q_count"] + (1 if answered_item is not None else 0)
    served_ids = list(state["served_ids"])
    if answered_item is not None and answered_item["id"] not in served_ids:
        served_ids.append(answered_item["id"])

    candidates = [q for q in pool if q["id"] not in served_ids]
    if answered_item is not None and is_correct is not None:
        _fallback_post, fallback_theta, fallback_se = eap_update(
            state["posterior"],
            answered_item,
            is_correct,
        )
    else:
        fallback_theta = state["theta_hat"]
        fallback_se = state["se"]

    fallback_certainty = combined_certainty_pct(
        fallback_se,
        state.get("self_confidence", "low"),
        next_q_count,
        state.get("prior_sd", 2.0),
        se_start=state.get("se_start"),
    )
    fallback_level, fallback_pct, fallback_band, fallback_low_confidence = level_and_band(
        fallback_theta,
        fallback_se,
    )

    if not candidates:
        return FullCATResult(
            theta_hat=fallback_theta,
            se=fallback_se,
            certainty_pct=fallback_certainty,
            stop=True,
            selection=None,
            calculation_steps=["No unserved candidates remain."],
            selection_note="Bank exhausted.",
            fallback_used=not use_llm,
            raw={"stop_reason": "bank_exhausted"},
        )

    payload = {
        "competency": competency,
        "previous_state": {
            "theta_hat": round(state["theta_hat"], 4),
            "se": round(state["se"], 4),
            "q_count_before": state["q_count"],
            "certainty_pct": round(state.get("certainty_pct", 0.0), 2),
        },
        "answered_item": None
        if answered_item is None
        else {
            "id": answered_item["id"],
            "difficulty": answered_item.get("difficulty", "medium"),
            "discrimination": answered_item.get("discrimination", "medium"),
            "a": answered_item["a"],
            "b": answered_item["b"],
            "c": answered_item["c"],
            "correct": bool(is_correct),
        },
        "q_count_after_answer": next_q_count,
        "examinee_parameters": {
            "competency_level": fallback_level,
            "competency_pct": fallback_pct,
            "level_band": fallback_band,
            "low_confidence": fallback_low_confidence,
            "certainty_pct": round(fallback_certainty, 1),
        },
        "stop_thresholds": {
            "se_target": SE_TARGET,
            "max_questions": MAX_QUESTIONS,
        },
        "served_ids_after_answer": served_ids,
        "candidates": _candidate_payload(candidates),
    }

    if not use_llm:
        return _fallback_result(
            state,
            pool,
            served_ids,
            theta_hat=fallback_theta,
            se=fallback_se,
            certainty_pct=fallback_certainty,
            q_count=next_q_count,
            reason="LLM full controller disabled.",
        )

    try:
        data = chat_json(LLM_FULL_CAT_SYSTEM, json.dumps(payload, indent=2))
        trace_llm_response(
            "cat.llm.full_step",
            input_data=payload,
            output_data=data,
            metadata={"competency": competency, "phase": "llm_full_cat"},
        )
    except Exception as exc:
        return _fallback_result(
            state,
            pool,
            served_ids,
            theta_hat=fallback_theta,
            se=fallback_se,
            certainty_pct=fallback_certainty,
            q_count=next_q_count,
            reason=f"LLM full controller failed: {type(exc).__name__}: {exc}",
        )

    theta_hat = float(np.clip(_num(data.get("theta_hat"), fallback_theta), -4.0, 4.0))
    se = float(np.clip(_num(data.get("se"), fallback_se), 0.2, 2.5))
    certainty = float(np.clip(_num(data.get("certainty_pct"), fallback_certainty), 0.0, 100.0))
    hard_stop = se <= SE_TARGET or next_q_count >= MAX_QUESTIONS
    llm_stop = bool(data.get("stop", False))
    stop = hard_stop or llm_stop

    selected_id = str(data.get("selected_id", "")).strip()
    id_map = {q["id"]: q for q in candidates}
    selection = None
    if not stop:
        if selected_id not in id_map:
            return _fallback_result(
                state,
                pool,
                served_ids,
                theta_hat=theta_hat,
                se=se,
                certainty_pct=certainty,
                q_count=next_q_count,
                reason=f"LLM selected invalid id: {selected_id!r}",
            )
        rephrased_stem = str(data.get("rephrased_stem", "")).strip()
        if not rephrased_stem:
            rephrased_stem = id_map[selected_id].get("stem", "")
        selection = _selection_from_item(
            id_map[selected_id],
            theta_hat=theta_hat,
            q_count=next_q_count,
            criterion=str(data.get("criterion_used", "LLM-CAT")),
            llm_used=True,
            note=str(data.get("selection_note", "")),
            rule=str(data.get("rule_applied", "LLM full CAT")),
            steps=_steps(data.get("calculation_steps")),
            candidate_ids=[q["id"] for q in candidates],
            rephrased_stem=rephrased_stem,
        )

    return FullCATResult(
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty,
        stop=stop,
        selection=selection,
        calculation_steps=_steps(data.get("calculation_steps")),
        selection_note=str(data.get("selection_note", data.get("stop_reason", ""))),
        fallback_used=False,
        raw=data,
    )
