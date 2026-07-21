"""Per-step trace of competency levels and CAT parameters.

The audit record (`pipeline.audit_record`) answers "what happened at this step". This
answers "what did the learner model look like, step by step" — which is the view you need
to judge whether an adaptive test is behaving, and the one that is invisible in a table of
per-step events.

Two frames come out of it:

  competency_frame   one row per (step, competency): level, mastery, standard error,
                     evidence count, and the delta this step caused. This is the CSV a
                     reviewer actually wants, and it is what `updated_competencies` in
                     the audit record only summarises.
  cat_frame          one row per step: the parameters that drove the NEXT selection —
                     ability estimate, standard error, expected information of the
                     administered item, its rank, utility and regret.

Both are built from snapshots taken before and after each update, so nothing here can
drift from what the engine actually did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepTrace:
    """Everything one answered question changed."""

    step: int
    question_id: str
    target_competency: str

    # objective evidence
    passed_tests: int
    total_tests: int
    overall_score: float | None
    llm_scored: bool

    # selection that led here
    rank: int | None = None
    utility: float | None = None
    best_utility: float | None = None
    regret: float | None = None
    engine_choice: str | None = None
    chosen_by_llm: bool = False
    reason_code: str = ""

    # learner model either side of the update
    before: dict[str, dict] = field(default_factory=dict)
    after: dict[str, dict] = field(default_factory=dict)

    misconceptions: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def record(
    step: int,
    question_id: str,
    target: str,
    result,
    decision,
    before: dict[str, dict],
    after: dict[str, dict],
) -> StepTrace:
    return StepTrace(
        step=step,
        question_id=question_id,
        target_competency=target,
        passed_tests=result.execution.passed_tests,
        total_tests=result.execution.total_tests,
        overall_score=result.overall_score,
        llm_scored=result.llm.available,
        rank=getattr(decision, "rank", None),
        utility=getattr(decision, "utility", None),
        best_utility=getattr(decision, "best_utility", None),
        regret=getattr(decision, "normalized_regret", None),
        engine_choice=(decision.shortlist_ids[0] if decision and decision.shortlist_ids else None),
        chosen_by_llm=bool(getattr(decision, "chosen_by_llm", False)),
        reason_code=getattr(decision, "reason_code", "") or "",
        before=before,
        after=after,
        misconceptions=sorted({c for e in result.competency_evidence for c in e.misconception_codes}),
        flags=list(result.flags),
    )


def competency_frame(traces: list[StepTrace]) -> list[dict[str, Any]]:
    """One row per (step, competency). The trace a reviewer reads.

    Only competencies the step actually touched appear, so the table shows what moved
    rather than repeating every competency at every step — on a bank where one question
    carries four competencies that difference is the whole readability of the table.
    """
    rows: list[dict[str, Any]] = []
    for t in traces:
        for cid, after in sorted(t.after.items()):
            before = t.before.get(cid)
            if before is not None and before["mastery"] == after["mastery"]:
                continue
            rows.append(
                {
                    "step": t.step,
                    "question": t.question_id,
                    "competency": cid,
                    "target": "◀" if cid == t.target_competency else "",
                    "level": after["level"],
                    "band": after["band"],
                    "mastery": after["mastery"],
                    "Δ mastery": round(after["mastery"] - (before or {"mastery": 0.5})["mastery"], 4),
                    "std_error": after["standard_error"],
                    "Δ std_error": round(
                        after["standard_error"] - (before or {"standard_error": 0.0})["standard_error"], 4
                    ),
                    "evidence": after["evidence_count"],
                    "misconceptions": ", ".join(after["misconception_codes"]) or "",
                }
            )
    return rows


def cat_frame(traces: list[StepTrace]) -> list[dict[str, Any]]:
    """One row per step: the CAT parameters and the selection they produced."""
    rows: list[dict[str, Any]] = []
    for t in traces:
        target_after = t.after.get(t.target_competency, {})
        target_before = t.before.get(t.target_competency, {})
        rows.append(
            {
                "step": t.step,
                "question": t.question_id,
                "tests": f"{t.passed_tests}/{t.total_tests}",
                "overall": t.overall_score,
                "ability_before": target_before.get("mastery"),
                "ability_after": target_after.get("mastery"),
                "se_before": target_before.get("standard_error"),
                "se_after": target_after.get("standard_error"),
                "level": target_after.get("level"),
                "engine_top_pick": t.engine_choice,
                "administered": t.question_id,
                "rank": t.rank,
                "utility": t.utility,
                "best_utility": t.best_utility,
                "regret": t.regret,
                "chosen_by_llm": t.chosen_by_llm,
                "reason_code": t.reason_code,
                "llm_scored": t.llm_scored,
                "flags": "; ".join(t.flags),
            }
        )
    return rows


def level_series(traces: list[StepTrace]) -> list[dict[str, Any]]:
    """Wide frame for charting: one row per step, one column per competency's mastery.

    Carries the last known value forward, so a competency the current step did not touch
    holds its line instead of dropping to zero — a gap would read as the estimate
    collapsing rather than as no new evidence.
    """
    seen: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    for t in traces:
        for cid, state in t.after.items():
            if state["observed"]:
                seen[cid] = state["mastery"]
        rows.append({"step": t.step, **dict(sorted(seen.items()))})
    return rows


def divergence_summary(traces: list[StepTrace]) -> dict[str, Any]:
    """How far the administered questions drifted from the engine's own ranking.

    Worth surfacing rather than leaving in a column: a session where the model overrides
    rank 1 every time and the regret climbs is behaving very differently from one where
    it agrees, and the difference is invisible unless it is aggregated.
    """
    picked = [t for t in traces if t.rank is not None]
    if not picked:
        return {}
    overridden = [t for t in picked if t.rank > 1]
    regrets = [t.regret for t in picked if t.regret is not None]
    return {
        "steps": len(picked),
        "engine_top_taken": sum(1 for t in picked if t.rank == 1),
        "overridden": len(overridden),
        "mean_regret": round(sum(regrets) / len(regrets), 4) if regrets else 0.0,
        "max_regret": round(max(regrets), 4) if regrets else 0.0,
        "never_administered": sorted(
            {t.engine_choice for t in overridden if t.engine_choice}
            - {t.question_id for t in traces}
        ),
    }
