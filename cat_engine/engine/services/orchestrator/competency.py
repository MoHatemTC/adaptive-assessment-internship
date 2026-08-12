"""Main vs sub-competency helpers for orchestrated assessment.

Candidates choose main competencies (T1..T5). Items and graders still speak in
sub-competencies (T1.1, T2.3); this module is the rollup boundary.
"""

from __future__ import annotations

from collections import defaultdict

from cat_engine.engine.schemas.orchestration import BankItem
from cat_engine.engine.services.orchestrator.outcome import GradedOutcome


def main_competency(variable: str) -> str:
    """T1.1 -> T1; T1 -> T1."""
    return variable.split(".")[0]


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
                weight=combined_weight([g.weight for g in moving]),
                confidence=max(g.confidence for g in moving),
                source_item_id=moving[0].source_item_id,
                modality=moving[0].modality,
            )
        )
    return rolled


def combined_weight(weights: list[float]) -> float:
    """How much evidence ONE response carries about one main competency.

    Every outcome in a rollup comes from a single administered item, so summing their
    weights adds one response to itself. `min(1, sum)` hid that behind a cap and made it
    worse rather than better: a code question measuring two sub-competencies at 0.70 and
    0.65 saturated at exactly 1.0 — the same posterior weight as a flawless full-credit
    multiple-choice answer — so past the cap the whole fractional-weight apparatus stopped
    distinguishing anything at all.

    A probabilistic union instead:

        w = 1 - prod(1 - w_i)

    Three properties, and all three are why:

    - **Bounded by one response.** Two partial measurements can never outweigh one whole
      one, however many nodes an item touches.
    - **Monotone.** Measuring a second sub-competency still adds evidence — 0.70 and 0.65
      give 0.895, more than either alone — so an item that tests more is still worth more.
    - **Exact at the edges.** A single outcome returns its own weight unchanged, so every
      single-node item, which is every multiple-choice item, updates exactly as before.

    It reads each sub-competency as partially independent evidence about the main, which
    is the same assumption the weighted-mean score above already makes.
    """
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - min(max(float(weight), 0.0), 1.0)
    return 1.0 - remaining
