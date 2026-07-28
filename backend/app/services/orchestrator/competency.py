"""Main vs sub-competency helpers for orchestrated assessment.

Candidates choose main competencies (T1..T5). Items and graders still speak in
sub-competencies (T1.1, T2.3); this module is the rollup boundary.
"""

from __future__ import annotations

from collections import defaultdict

from app.schemas.orchestration import BankItem
from app.services.orchestrator.outcome import GradedOutcome


def main_competency(variable: str) -> str:
    """T1.1 -> T1; T1 -> T1."""
    return variable.split(".")[0]


def is_main_competency(variable: str) -> bool:
    return "." not in variable


def affected_mains(item: BankItem, session_variables: set[str]) -> set[str]:
    """Main competencies this item evidences that the session is measuring."""
    mains: set[str] = set()
    for entry in item.measures:
        main = main_competency(entry.variable)
        if main in session_variables:
            mains.add(main)
    return mains


def rollup_outcomes(
    raw_outcomes: list[dict],
    session_variables: set[str],
) -> list[GradedOutcome]:
    """Fold sub-competency grader outcomes into one update per main competency."""
    groups: dict[str, list[GradedOutcome]] = defaultdict(list)
    for raw in raw_outcomes:
        outcome = GradedOutcome(**raw)
        main = main_competency(outcome.variable)
        if main not in session_variables:
            continue
        groups[main].append(outcome)

    rolled: list[GradedOutcome] = []
    for main, group in groups.items():
        moving = [g for g in group if g.moves_the_estimate]
        if not moving:
            rolled.append(
                GradedOutcome(
                    variable=main,
                    score=0.0,
                    weight=0.0,
                    source_item_id=group[0].source_item_id,
                    modality=group[0].modality,
                )
            )
            continue
        total_w = sum(g.weight for g in moving)
        score = sum(g.score * g.weight for g in moving) / total_w
        rolled.append(
            GradedOutcome(
                variable=main,
                score=score,
                weight=min(total_w, 1.0),
                confidence=max(g.confidence for g in moving),
                source_item_id=moving[0].source_item_id,
                modality=moving[0].modality,
            )
        )
    return rolled
