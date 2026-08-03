"""Does the configured session actually fit in the configured time?

The review's C3: the specification's own numbers do not close. Sixty questions in ninety
minutes implies ninety seconds an item; a code item is estimated at nine hundred. Three
mains at the six-question floor is eighteen questions, which under a code-heavy diet costs
147 minutes against a 90-minute limit — so the observation floor itself was infeasible,
and selection optimising information PER ITEM trended straight towards that diet.

Measured on the AI Engineer bank: code items run 180-900s (median 480), voice 200-300
(median 240). What makes ninety minutes work is ranking on information per minute, plus a
blueprint that guarantees a mix, plus this check — which turns a silent overrun into a
loud one at startup rather than a session that runs long and gets cut off mid-answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config.settings import settings
from app.schemas.orchestration import DEFAULT_SECONDS_BY_MODALITY

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BudgetCheck:
    mains: int
    worst_case_seconds: float
    limit_seconds: float
    expensive_per_main: dict[str, int]
    feasible: bool

    @property
    def worst_case_minutes(self) -> float:
        return self.worst_case_seconds / 60.0

    def describe(self) -> str:
        mix = ", ".join(f"{n} {m}" for m, n in sorted(self.expensive_per_main.items()))
        return (
            f"{self.mains} mains x {settings.cat_max_questions} questions "
            f"({mix or 'no minimums'} per main) = {self.worst_case_minutes:.0f} min "
            f"against a {self.limit_seconds / 60:.0f} min limit"
        )


def max_expensive_items_per_main(mains: int) -> int:
    """How many slow items per main the clock can afford.

    Derived, not authored. Everything not spent on a slow item is assumed to go on the
    cheapest modality:

        floor((limit - reserve - mains x cap x t_cheapest) / (mains x sum(t_expensive)))
    """
    minimums = settings.modality_minimums()
    if not minimums or mains <= 0:
        return 0

    cheapest = min(DEFAULT_SECONDS_BY_MODALITY.values())
    expensive = sum(DEFAULT_SECONDS_BY_MODALITY.get(m, 0.0) for m in minimums)
    if expensive <= 0:
        return 0

    budget = (
        settings.orchestrator_time_limit_minutes * 60.0
        - settings.orchestrator_time_reserve_seconds
        - mains * settings.cat_max_questions * cheapest
    )
    return max(0, int(budget // (mains * expensive)))


def check(mains: int) -> BudgetCheck:
    """Worst case: every main runs to its cap, spending its minimums on slow items."""
    minimums = settings.modality_minimums()
    limit_seconds = settings.orchestrator_time_limit_minutes * 60.0
    cheapest = min(DEFAULT_SECONDS_BY_MODALITY.values())

    per_main = 0.0
    slow_items = 0
    for modality, required in minimums.items():
        per_main += required * DEFAULT_SECONDS_BY_MODALITY.get(modality, 0.0)
        slow_items += required
    remaining_questions = max(0, settings.cat_max_questions - slow_items)
    per_main += remaining_questions * cheapest

    worst_case = mains * per_main
    return BudgetCheck(
        mains=mains,
        worst_case_seconds=worst_case,
        limit_seconds=limit_seconds,
        expensive_per_main=dict(minimums),
        feasible=worst_case
        <= limit_seconds - settings.orchestrator_time_reserve_seconds,
    )


def warn_if_infeasible(mains: int) -> BudgetCheck:
    """Check and log. Never raises — a candidate mid-assessment is not the place to fail.

    A loud warning at session start is the difference between a configuration someone
    chose and a configuration nobody noticed. The recovery is a real decision: fewer
    mains, a longer session, or one fewer slow modality.
    """
    result = check(mains)
    if not result.feasible:
        affordable = max_expensive_items_per_main(mains)
        logger.warning(
            "session time budget does not close: %s. The clock affords %d slow item(s) "
            "per main. Reduce the mains, raise ORCHESTRATOR_TIME_LIMIT_MINUTES, or drop a "
            "modality minimum.",
            result.describe(),
            affordable,
        )
    return result
