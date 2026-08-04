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


def measurement_certainty_pct(standard_error: float) -> float:
    """SE → % confidence from the posterior width alone (no sample-size gate).

    Maps SE = TARGET to exactly 90% so the precision rule reads in the same units the
    report uses, and saturates at 100% as SE falls further. Absolute in SE on purpose:
    scaling by prior width made two candidates with the same posterior look different
    purely because of intake.
    """
    se = max(float(standard_error), 1e-6)
    target = settings.cat_se_target
    if se <= target:
        return float(min(90.0 + 10.0 * (1.0 - se / target), 100.0))
    return float(max(90.0 * (target / se), 0.0))


def certainty_pct(
    standard_error: float,
    *,
    observations: int | None = None,
) -> float:
    """Confidence shown after each question.

    Raw SE→% is the measurement signal. Before `cat_precision_min_questions` answers,
    that signal is treated as *provisional*: a couple of high-`a` items can make SE look
    like 90% certainty while the estimate has barely been corroborated. Reported
    confidence therefore ramps with evidence count and is capped below 90% until the
    precision floor is met — so the UI cannot read "done" before the stop rule would
    allow a precision stop.
    """
    raw = measurement_certainty_pct(standard_error)
    if observations is None:
        return raw

    floor = max(int(getattr(settings, "cat_precision_min_questions", settings.cat_min_questions)), 1)
    n = max(int(observations), 0)
    if n >= floor:
        return raw

    # Ramp 0 → raw across the precision floor; never display ≥90% while provisional.
    progress = n / floor
    return float(min(raw * progress, 89.0))


def band_is_stable(band_history: list[int], window: int) -> bool:
    """True when the reported band has not moved for `window` consecutive items."""
    if len(band_history) < window:
        return False
    return len(set(band_history[-window:])) == 1


def band_probability_stop_available(
    band_probability: float | None, questions_answered: int
) -> bool:
    """Whether a P(band) stop may fire. Off by default; see `evaluate`."""
    return (
        settings.cat_band_probability_stop_enabled
        and band_probability is not None
        and band_probability >= settings.cat_band_probability_target
        and questions_answered
        >= getattr(settings, "cat_precision_min_questions", settings.cat_min_questions)
    )


def evaluate(
    standard_error: float,
    band_history: list[int],
    questions_answered: int,
    items_remaining: int,
    *,
    band_probability: float | None = None,
) -> StopDecision:
    """Apply the stopping rules in precedence order.

    0. BAND PROBABILITY — the reported level is probably right. OFF by default, and
       ADDITIVE: it can end a competency early, never keep one open. It asks a different
       question from precision — "is this level right" rather than "is this estimate
       tight" — and measured on the evaluation cohort it is both more accurate and
       shorter than the SE rule at every band count tested. Keeps the observation floor
       regardless: a prior is not a measurement, however concentrated it looks.
    1. PRECISION — SE at/under target AND enough observations to trust that width.
       A single high-discrimination hit can crush SE; the observation floor stops that
       from ending the competency before the estimate has been corroborated.
    2. STABLE BAND — the reported band has settled and the estimate is reasonably precise.
       A test-length optimisation, not evidence of precision: gated on a secondary SE
       ceiling and its own min-questions floor.
    3. BUDGET — out of questions, or out of items. Not convergence.
    """
    if band_probability_stop_available(band_probability, questions_answered):
        return StopDecision(True, "band_probability", converged=True)

    if (
        standard_error <= settings.cat_se_target
        and questions_answered
        >= getattr(settings, "cat_precision_min_questions", settings.cat_min_questions)
    ):
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
