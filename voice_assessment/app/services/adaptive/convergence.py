"""When to stop asking questions, and how confident the result is.

Kept separate from `irt` because these are policy, not measurement: the numbers here are
choices about how much precision is worth how many questions, and they are expected to be
retuned per deployment. `irt` is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import settings


@dataclass(frozen=True)
class StopDecision:
    """Why a competency ended.

    `converged` is True only when a MEASUREMENT criterion was met. Running out of
    questions or items is a budget outcome and must never be reported as convergence —
    the distinction is the difference between "we know this candidate's level" and "we
    stopped asking".
    """

    should_stop: bool
    reason: str = ""  # precision | stable_band | question_budget | bank_exhausted
    converged: bool = False

    @property
    def is_precise(self) -> bool:
        """True only for a stop earned on measured precision."""
        return self.reason == "precision"


def certainty_pct(standard_error: float) -> float:
    """Report confidence as a function of the standard error alone.

    Deliberately absolute, not relative to where the candidate started. An earlier design
    scaled this by the prior width, which meant two candidates with identical posterior
    precision were shown different confidence, and — because the stability rule below
    gates on this number — were given different-length tests, purely because of how they
    answered an intake question. Confidence should describe the estimate, not the guess it
    started from.

    Maps SE = TARGET to exactly 90% so the precision rule below reads in the same units
    the candidate's report is written in, and saturates at 100% as SE falls further.
    """
    se = max(float(standard_error), 1e-6)
    target = settings.cat_se_target
    if se <= target:
        return float(min(90.0 + 10.0 * (1.0 - se / target), 100.0))
    # Above target, decay smoothly toward 0 as SE grows. SE = 2*target -> 45%.
    return float(max(90.0 * (target / se), 0.0))


def band_is_stable(band_history: list[int], window: int) -> bool:
    """True when the reported band has not moved for `window` consecutive items."""
    if len(band_history) < window:
        return False
    return len(set(band_history[-window:])) == 1


def evaluate(
    standard_error: float,
    band_history: list[int],
    questions_answered: int,
    items_remaining: int,
) -> StopDecision:
    """Apply the stopping rules in precedence order.

    1. PRECISION — the estimate is good enough. The only rule that yields `converged`
       with `is_precise`, and the only one whose threshold is a psychometric statement.
    2. STABLE BAND — the reported band has settled and the estimate is reasonably precise.
       A test-length optimisation, not evidence of precision: it is gated on a secondary
       SE floor so it cannot fire while the estimate is still vague, and it still reports
       `converged` without `is_precise` so a report can say the band settled without
       claiming a precision it did not reach.
    3. BUDGET — out of questions, or out of items. Not convergence.
    """
    if standard_error <= settings.cat_se_target:
        return StopDecision(True, "precision", converged=True)

    if (
        questions_answered >= settings.cat_min_questions
        and standard_error <= settings.cat_stability_se_ceiling
        and band_is_stable(band_history, settings.cat_stable_window)
    ):
        return StopDecision(True, "stable_band", converged=True)

    if items_remaining <= 0:
        return StopDecision(True, "bank_exhausted", converged=False)

    if questions_answered >= settings.cat_max_questions:
        return StopDecision(True, "question_budget", converged=False)

    return StopDecision(False)
