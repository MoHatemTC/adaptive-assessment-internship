"""When to stop asking questions, and how confident the result is.

Kept separate from `irt` because these are policy, not measurement: the numbers here are
choices about how much precision is worth how many questions, and they are expected to be
retuned per deployment. `irt` is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config.settings import settings


class StopReason(StrEnum):
    """Every way a competency or a session can end. There is no unnamed way.

    The empty string used to be one of them, and it was the most common: a session ending
    on a session-level rule left every still-open variable at `""`, which was copied
    straight into the report. Between a third and 38% of sessions in one arm reported a
    blank reason, and the analysis bucketed `""` alongside the named reasons and printed a
    percentage next to it.

    A blank is not a stop reason; it is the absence of one. Naming every path is what lets
    the distribution be asserted to sum to 1 over reasons that mean something, which is
    the PRE-2 check.
    """

    # Measurement criteria — these are the ones that may claim convergence.
    PRECISION = "precision"
    STABLE_BAND = "stable_band"
    BAND_PROBABILITY = "band_probability"

    # Budget outcomes. The competency ended; nobody learned the candidate's level.
    QUESTION_BUDGET = "question_budget"
    BANK_EXHAUSTED = "bank_exhausted"

    # Orchestrator-level, and only it can know them.
    ALL_VARIABLES_FINALISED = "all_variables_finalised"
    TIME_LIMIT = "time_limit"
    ITEM_BUDGET = "item_budget"
    NO_CANDIDATES_AVAILABLE = "no_candidates_available"
    GRAPH_GATES_WAIVED = "graph_gates_waived"

    # Not a stop at all. Named so that "still running" is distinguishable from "ended for
    # a reason nobody recorded" — which is exactly what the blank conflated.
    IN_PROGRESS = "in_progress"

    # A variable still open when a SESSION-level rule ended the run. The session stopped;
    # this competency did not finish. Prefixed so the distribution shows at a glance how
    # much of it is competencies that were cut off rather than measured.
    SESSION_TIME_LIMIT = "session_time_limit"
    SESSION_ITEM_BUDGET = "session_item_budget"
    SESSION_NO_CANDIDATES_AVAILABLE = "session_no_candidates_available"
    SESSION_ENDED = "session_ended"


#: Reasons that may appear on a FINISHED variable or session. `IN_PROGRESS` is excluded:
#: it is the one value that means no stop happened.
TERMINAL_STOP_REASONS: frozenset[str] = frozenset(
    r.value for r in StopReason if r is not StopReason.IN_PROGRESS
)

#: What a session-level stop becomes on a variable that was still open when it fired.
SESSION_STOP_FOR: dict[str, str] = {
    StopReason.TIME_LIMIT: StopReason.SESSION_TIME_LIMIT,
    StopReason.ITEM_BUDGET: StopReason.SESSION_ITEM_BUDGET,
    StopReason.NO_CANDIDATES_AVAILABLE: StopReason.SESSION_NO_CANDIDATES_AVAILABLE,
}


@dataclass(frozen=True)
class StopDecision:
    """Why a competency ended.

    `converged` is True only when a MEASUREMENT criterion was met. Running out of
    questions or items is a budget outcome and must never be reported as convergence —
    the distinction is the difference between "we know this candidate's level" and "we
    stopped asking".
    """

    should_stop: bool
    # precision | stable_band | band_probability | question_budget | bank_exhausted
    # The orchestrator adds two more it alone can know about: `time_budget` when no item
    # fits the clock, and `graph_gates_waived` when measurement converged but required
    # sub-competencies were never measured.
    reason: str = ""
    converged: bool = False

    @property
    def is_precise(self) -> bool:
        """True only for a stop earned on measured precision."""
        return self.reason == "precision"


def measurement_certainty_pct(standard_error: float) -> float:
    """SE → posterior *precision index* (0–100), not a calibrated probability.

    Maps SE = TARGET to exactly 90 so the precision stop and the UI share one scale,
    and saturates at 100 as SE falls further. This is a deterministic remapping of
    posterior SD — not P(correct band), not a frequentist confidence level, and not
    validated unless item parameters are empirically calibrated. Absolute in SE on
    purpose: scaling by prior width made two candidates with the same posterior look
    different purely because of intake.
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
    """Posterior precision index shown after each question (not statistical confidence).

    Raw SE→% is a width signal. Before `cat_precision_min_questions` answers,
    that signal is treated as *provisional*: a couple of high-`a` items can make SE look
    like 90% while the estimate has barely been corroborated. The reported index
    therefore ramps with evidence count and is capped below 90 until the
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
    band_probability: float | None,
    questions_answered: int,
    difficulty_corroborated: bool,
    standard_error: float | None = None,
) -> bool:
    """Whether a P(band) stop may fire. Off by default; see `evaluate`.

    CONJUNCTIVE OR NOT (PRE-5). As written the rule runs BEFORE the precision rule and
    never consults the standard error, so it can end a competency at an SE far above
    target: mass concentrated in one band is not the same claim as a precise estimate,
    and near a cut point the two diverge sharply. Every propagation configuration in the
    study is judged by its effect on a posterior, so a stopping rule that ends sessions
    at an arbitrary precision would confound the whole sweep.

    Under `cat_band_probability_stop_conjunctive` the rule additionally requires the
    precision target, making it a genuine refinement of the precision stop rather than an
    alternative to it. Default OFF, so no shipped report changes; ON in the evaluation
    harness, where the posterior has to mean one thing.
    """
    if not (
        settings.cat_band_probability_stop_enabled
        and band_probability is not None
        and band_probability >= settings.cat_band_probability_target
        and questions_answered
        >= getattr(settings, "cat_precision_min_questions", settings.cat_min_questions)
        and difficulty_corroborated
    ):
        return False
    if getattr(settings, "cat_band_probability_stop_conjunctive", False):
        return standard_error is not None and standard_error <= settings.cat_se_target
    return True


def evaluate(
    standard_error: float,
    band_history: list[int],
    questions_answered: int,
    items_remaining: int,
    *,
    difficulty_corroborated: bool = True,
    band_probability: float | None = None,
) -> StopDecision:
    """Apply the stopping rules in precedence order.

    0. BAND PROBABILITY — the reported level is probably right. OFF by default, and
       ADDITIVE: it can end a competency early, never keep one open. It asks a different
       question from precision — "is this level right" rather than "is this estimate
       tight" — and near a band cut point it demands materially more evidence for the
       same standard error (0.57 against 0.85 at the SE target). That is the rule
       working, but it changes test length in a way that wants measuring before it is
       trusted, which is why it ships off.
       Keeps the observation floor regardless: a prior is not a measurement, however
       concentrated it happens to look.
    1. PRECISION — SE at/under target, enough observations, and item difficulty that
       corroborates the estimated level.
       A single high-discrimination hit can crush SE; the observation floor stops that
       from ending the competency before the estimate has been corroborated.
    2. STABLE BAND — the reported band has settled and the estimate is reasonably precise.
       A test-length optimisation, not evidence of precision: gated on a secondary SE
       ceiling and its own min-questions floor.
    3. BUDGET — out of questions, or out of items. Not convergence.
    """
    if band_probability_stop_available(
        band_probability, questions_answered, difficulty_corroborated, standard_error
    ):
        return StopDecision(True, "band_probability", converged=True)

    if (
        standard_error <= settings.cat_se_target
        and questions_answered
        >= getattr(settings, "cat_precision_min_questions", settings.cat_min_questions)
        and difficulty_corroborated
    ):
        return StopDecision(True, "precision", converged=True)

    if (
        questions_answered >= settings.cat_min_questions
        and standard_error <= settings.cat_stability_se_ceiling
        and band_is_stable(band_history, settings.cat_stable_window)
        and difficulty_corroborated
    ):
        return StopDecision(True, "stable_band", converged=True)

    if items_remaining <= 0:
        return StopDecision(True, "bank_exhausted", converged=False)

    if questions_answered >= settings.cat_max_questions:
        return StopDecision(True, "question_budget", converged=False)

    return StopDecision(False, StopReason.IN_PROGRESS)
