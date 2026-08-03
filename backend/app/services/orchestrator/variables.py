"""Examinee Variables: the live estimate, one latent ability per variable.

The architecture's central store. Seeded from intake, updated after every graded outcome,
and read by the orchestrator to decide which variable to probe next.

ONE SCALE, ALL MODALITIES. Every variable holds a posterior over the same theta grid,
whatever measured it. That is what makes "the least-measured variable" a well-defined
question: comparing a variable measured by MCQ against one measured by code is only
legitimate if the two numbers mean the same thing, and they do because both are EAP means
of a posterior over the same latent scale.

FINALISATION IS ONE-WAY. Once a variable's stopping rule fires it is finalised: no further
picking, no further probing, no un-finalising. A rule that could reverse would let a
variable oscillate in and out of the queue and make session length unpredictable — and a
report saying "measured" must not later become "still measuring".
"""

from __future__ import annotations

import logging

import numpy as np

from app.config.settings import settings
from app.schemas.orchestration import VariableState
from app.services.adaptive import convergence
from app.services.adaptive.irt import (
    THETA_GRID,
    ability_band,
    band_lower_bound,
    band_probabilities,
    credible_interval,
    prior_from_self_rating,
    uniform_prior,
)
from app.services.orchestrator.outcome import GradedOutcome, graded_posterior_update

logger = logging.getLogger(__name__)

# Prior width by how much the self-rating is worth believing, matching the MCQ engine.
# A rating sets where the search starts, never where it ends.
# Flattened from 1.1 (review A9/T7). Simulated on this module's own update: a candidate
# whose true theta is 0 but who self-rates 5 lands at +0.42 after the full six-observation
# floor at SD 1.1, and at +0.23 at SD 1.5. Bands are 1.0 logits wide and the nearest
# boundary is 0.5 away, so at 1.1 the SELF-RATING ALONE moves the estimate 42% of a band
# width — in the direction the candidate rated themselves, at full precision, not as an
# early-session artefact. Self-assessment differs systematically by background, so an
# instrument used for placement imports that difference into a certified level.
#
# Costs one extra item per variable for confident self-raters: about +3.75 minutes across
# three mains, against 13.5 minutes of slack in the time budget.
PRIOR_SD_CONFIDENT = 1.5
PRIOR_SD_TENTATIVE = 1.7
PRIOR_SD_NO_RATING = 2.0
STRONG_STREAK_LENGTH = 3
STRONG_SCORE_FLOOR = 0.85


def seed_variable(
    variable: str, self_rating: int | None = None, rating_confident: bool = False
) -> VariableState:
    """Open a variable, seeding its prior from intake."""
    if self_rating is None:
        posterior = uniform_prior()
        standard_error = PRIOR_SD_NO_RATING
    else:
        sd = PRIOR_SD_CONFIDENT if rating_confident else PRIOR_SD_TENTATIVE
        posterior = prior_from_self_rating(self_rating, sd)
        standard_error = sd

    theta_hat = float(np.sum(np.asarray(posterior) * THETA_GRID))
    return VariableState(
        variable=variable,
        posterior=list(map(float, posterior)),
        theta_hat=theta_hat,
        standard_error=standard_error,
    )


def certainty(state: VariableState) -> float:
    """Posterior precision index for this estimate, 0-100.

    Not a calibrated confidence probability. Width still comes from SE alone (two
    variables with the same posterior SD compare equal on that axis). Observation count
    only gates the *reported* index while the precision floor has not been met, so a
    short burst of high-`a` items cannot look like a finished measurement in the UI.
    Prefer ``posterior_interval`` when an honest uncertainty range is needed.
    """
    return convergence.certainty_pct(
        state.standard_error, observations=state.observations
    )


def band_probability(state: VariableState) -> dict[int, float]:
    """P(theta in each ability band) under the current posterior."""
    return band_probabilities(np.asarray(state.posterior, dtype=float))


def posterior_interval(
    state: VariableState, *, mass: float = 0.95
) -> tuple[float, float]:
    """Equal-tailed credible interval from the live posterior."""
    return credible_interval(np.asarray(state.posterior, dtype=float), mass=mass)


def upper_challenge_difficulty(state: VariableState) -> float | None:
    """Difficulty needed to test whether a strong candidate belongs in the next band."""
    if len(state.score_history) < STRONG_STREAK_LENGTH:
        return None
    if any(score < STRONG_SCORE_FLOOR for score in state.score_history[-STRONG_STREAK_LENGTH:]):
        return None

    level, _ = ability_band(state.theta_hat)
    if level >= 5:
        return None

    # Read from the band definition rather than re-deriving it. This used to be
    # `level - 2.5`, an inverse of the old rounding rule written out by hand — the kind of
    # thing that keeps working silently after the bands it inverts have moved.
    next_band_boundary = band_lower_bound(level + 1)
    if next_band_boundary is None:
        return None
    return next_band_boundary - settings.cat_difficulty_corroboration_slack


def required_corroboration_difficulty(
    state: VariableState,
    *,
    maximum_available_difficulty: float | None = None,
) -> float:
    """Minimum administered ``b`` needed before finalisation.

    At the top of the theta scale, ``theta - slack`` can exceed every item in a finite
    bank. Requiring an impossible item turns a precision rule into guaranteed budget
    exhaustion, so the requirement is capped at the hardest available item.
    """
    challenge = upper_challenge_difficulty(state)
    required = (
        challenge
        if challenge is not None
        else state.theta_hat - settings.cat_difficulty_corroboration_slack
    )
    if maximum_available_difficulty is not None:
        required = min(required, float(maximum_available_difficulty))
    return required


def difficulty_is_corroborated(
    state: VariableState,
    administered_difficulties: list[float],
    *,
    maximum_available_difficulty: float | None = None,
) -> bool:
    """True when at least one administered item tested the claimed ability region."""
    if not administered_difficulties:
        return False
    return max(administered_difficulties) >= required_corroboration_difficulty(
        state,
        maximum_available_difficulty=maximum_available_difficulty,
    )


def apply_outcome(
    state: VariableState,
    outcome: GradedOutcome,
    a: float,
    b: float,
    c: float,
    item_id: str,
) -> VariableState:
    """Fold one graded outcome into a variable's estimate, returning a NEW state.

    Zero-weight outcomes are skipped entirely rather than applied as a no-op update: the
    arithmetic would leave the posterior untouched either way, but incrementing
    `observations` would make an unmeasured variable look measured — the same distinction
    the code engine draws when an infrastructure failure carries no evidence.
    """
    if not outcome.moves_the_estimate:
        logger.debug("outcome for %s carries no evidence — estimate untouched", outcome.variable)
        return state

    posterior, theta_hat, standard_error = graded_posterior_update(
        np.asarray(state.posterior, dtype=float), a, b, c, outcome.score, outcome.weight
    )
    level, _ = ability_band(theta_hat)
    return state.model_copy(
        update={
            "posterior": list(map(float, posterior)),
            "theta_hat": theta_hat,
            "standard_error": standard_error,
            "observations": state.observations + 1,
            "served_item_ids": [*state.served_item_ids, item_id],
            "band_history": [*state.band_history, level],
            "score_history": [*state.score_history, float(outcome.score)],
        }
    )


def evaluate_finalisation(
    state: VariableState,
    items_remaining: int,
    *,
    administered_difficulties: list[float] | None = None,
    maximum_available_difficulty: float | None = None,
) -> VariableState:
    """Apply the stopping rules and finalise if any fires.

    Reuses the MCQ engine's `convergence.evaluate` unchanged, so precision, band stability
    and budget mean exactly what they already mean elsewhere. `converged` stays False for a
    budget stop: running out of items is not the same as knowing a candidate's level, and
    a report that conflates them is claiming a measurement nobody made.
    """
    if state.finalised:
        return state

    corroborated = (
        True
        if administered_difficulties is None
        else difficulty_is_corroborated(
            state,
            administered_difficulties,
            maximum_available_difficulty=maximum_available_difficulty,
        )
    )
    stop = convergence.evaluate(
        state.standard_error,
        state.band_history,
        state.observations,
        items_remaining,
        difficulty_corroborated=corroborated,
    )
    if not stop.should_stop:
        return state

    return state.model_copy(
        update={"finalised": True, "stop_reason": stop.reason, "converged": stop.converged}
    )


def mark_time_exhausted(state: VariableState) -> VariableState:
    """No remaining item fits the clock.

    Distinct from `bank_exhausted` on purpose: "we ran out of questions" and "we ran out
    of time" are different facts about a session, and reporting one as the other describes
    a stop that did not happen.
    """
    return state.model_copy(
        update={"finalised": True, "stop_reason": "time_budget", "converged": False}
    )


def mark_exhausted(state: VariableState) -> VariableState:
    """Finalise a variable whose pool is empty, without claiming convergence."""
    if state.finalised:
        return state
    return state.model_copy(
        update={"finalised": True, "stop_reason": "bank_exhausted", "converged": False}
    )
