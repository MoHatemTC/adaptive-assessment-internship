"""3PL item response theory: the measurement core of the adaptive engine.

Pure functions over numpy arrays. No I/O, no LLM, no configuration — everything here is
deterministic and unit-testable, which is deliberate: this module decides what a
candidate's score means, so it must never depend on whether a network call succeeded.

THE MODEL

An item is described by three calibrated parameters:

    a   discrimination — how sharply the item separates ability either side of `b`
    b   difficulty     — the ability at which a non-guessing candidate has even odds
    c   guessing floor — the probability a candidate well below `b` still answers right

and the probability of a correct response at ability ``theta`` is

    P(theta) = c + (1 - c) / (1 + exp(-a * (theta - b)))

Ability is estimated on a fixed grid (``THETA_GRID``) rather than by root-finding. The
grid makes the posterior an explicit object: it can be inspected, plotted, carried across
requests, and read for any summary statistic without re-deriving anything. At 41 points
over [-4, 4] the discretisation error is far below the standard error of any realistic
test length, so nothing is lost.
"""

from __future__ import annotations

import numpy as np

# Ability grid. Spacing 0.2 over [-4, 4]: fine enough that the discretisation error is
# negligible against a standard error that never falls below ~0.5 in a short test, coarse
# enough that a full posterior update is a handful of microseconds.
THETA_GRID = np.arange(-4.0, 4.0001, 0.2)

# Difficulty and discrimination labels, for banks that carry words rather than numbers.
# The ladder spans [-2, +2] so Fisher information stays usable for candidates well away
# from the mean; a narrower ladder leaves strong and weak candidates measured by items
# that cannot distinguish them.
DIFFICULTY_TO_B = {
    "very_easy": -2.0,
    "easy": -1.0,
    "medium": 0.0,
    "hard": 1.0,
    "very_hard": 2.0,
}
DISCRIMINATION_TO_A = {"low": 0.6, "medium": 1.0, "high": 1.5}


def probability_correct(theta, a: float, b: float, c: float):
    """3PL probability of a correct response. Accepts scalar or array ``theta``."""
    return c + (1.0 - c) / (1.0 + np.exp(-a * (theta - b)))


def fisher_information(theta: float, a: float, b: float, c: float) -> float:
    """Exact 3PL Fisher information at a point.

        I(theta) = a^2 * ((P - c) / (1 - c))^2 * (1 - P) / P

    Not the 2PL approximation ``a^2 * P * (1 - P)``, which ignores the guessing floor and
    therefore overstates how much a hard item tells you about a weak candidate — exactly
    the case where the difference matters.
    """
    if c >= 1.0:
        return 0.0
    p = float(np.clip(probability_correct(theta, a, b, c), 1e-9, 1 - 1e-9))
    ratio = (p - c) / (1.0 - c)
    return float((a**2) * (ratio**2) * ((1.0 - p) / p))


def expected_fisher_information(posterior: np.ndarray, a: float, b: float, c: float) -> float:
    """Fisher information averaged over the posterior rather than taken at its mean.

    Information at a point estimate is only the right criterion if the point estimate is
    right. Early in a test it is not — the standard error is around 1.0 — so maximising
    ``I(theta_hat)`` chases a number that is still moving and can select an item the next
    response invalidates. Weighting by where the candidate plausibly *is* uses the same
    belief the ability estimate itself is read from.
    """
    infos = np.array([fisher_information(float(t), a, b, c) for t in THETA_GRID])
    return float(np.sum(posterior * infos))


def kullback_leibler_information(
    theta_hat: float, a: float, b: float, c: float, delta: float
) -> float:
    """KL divergence between the response distributions at ``theta_hat`` and nearby.

    Used for the first few items, where Fisher information is a poor criterion because it
    is local: it asks which item is most informative *exactly here*, when "here" is barely
    known. KL integrates over a neighbourhood of width ``delta``, so it prefers items that
    separate a whole region of ability — which is what an early item is for.
    """
    mask = np.abs(THETA_GRID - theta_hat) <= delta
    if not mask.any():
        return 0.0
    p_hat = float(np.clip(probability_correct(theta_hat, a, b, c), 1e-9, 1 - 1e-9))
    p = np.clip(probability_correct(THETA_GRID[mask], a, b, c), 1e-9, 1 - 1e-9)
    kl = p_hat * np.log(p_hat / p) + (1 - p_hat) * np.log((1 - p_hat) / (1 - p))
    return float(np.sum(kl))


def prior_from_self_rating(level_1_to_5: int, sd: float) -> np.ndarray:
    """Normal prior centred on a self-reported level, mapped 1..5 -> theta -2..+2.

    A self-rating is weak evidence and is treated as such: it sets where the search
    starts, and two or three responses are enough to override it. It is never blended
    into the final score.
    """
    mean = (float(level_1_to_5) - 3.0) * 1.0
    density = np.exp(-0.5 * ((THETA_GRID - mean) / sd) ** 2)
    return density / density.sum()


def uniform_prior() -> np.ndarray:
    """Flat prior, for candidates who gave no self-rating."""
    return np.ones_like(THETA_GRID) / THETA_GRID.size


def posterior_update(
    posterior: np.ndarray, a: float, b: float, c: float, is_correct: bool
) -> tuple[np.ndarray, float, float]:
    """Bayes update on one graded response.

    Returns ``(posterior, theta_hat, standard_error)`` where ``theta_hat`` is the
    posterior mean (EAP) and the standard error is the posterior standard deviation.

    EAP rather than maximum likelihood on purpose: ML is undefined for an all-correct or
    all-wrong response pattern, which is common in a 12-item test, and the prior keeps the
    estimate finite without distorting it once a few items have been answered.
    """
    p = probability_correct(THETA_GRID, a, b, c)
    likelihood = p if is_correct else (1.0 - p)
    updated = posterior * np.clip(likelihood, 1e-9, 1.0)

    total = updated.sum()
    if total <= 0:  # pragma: no cover — unreachable while the clip floor holds
        updated = np.ones_like(THETA_GRID)
        total = updated.sum()
    updated = updated / total

    theta_hat = float(np.sum(THETA_GRID * updated))
    standard_error = float(np.sqrt(np.sum((THETA_GRID - theta_hat) ** 2 * updated)))
    return updated, theta_hat, standard_error


def ability_percentile(theta_hat: float) -> float:
    """Ability as a percentile of the N(0,1) reference population.

    Reports *ability*, not proportion correct — they are different quantities and only the
    first is what a CAT estimates. Continuous and monotone in theta_hat, so a candidate who
    improves sees the number move; a banded score (level * 20) does not have that property
    and reads as a percentage while behaving like a bucket.
    """
    from math import erf, sqrt

    return float(np.clip(100.0 * 0.5 * (1.0 + erf(float(theta_hat) / sqrt(2.0))), 0.0, 100.0))


BAND_LABELS = {1: "Novice", 2: "Developing", 3: "Competent", 4: "Proficient", 5: "Expert"}

# Interior bands 1.6 logits wide. THE SINGLE SOURCE OF TRUTH for where a level begins.
#
# WHY THIS REPLACED `int(clip(round(3 + theta), 1, 5))`. That rule left bands 1 and 5
# UNBOUNDED while the interior three were 1.0 wide, so "Novice" covered everything below
# -1.5 and "Expert" everything above +1.5. Accuracy in an open-ended band is nearly free,
# and measured on the evaluation cohort the engine reported 0.818 exact-level accuracy
# where the same theta estimates earned 0.644 on an even scale. A level that means a
# 1.0-logit range in the middle and an unbounded one at the ends is not one scale.
#
# 1.6 rather than 1.0 because a reported level is only worth reporting if it is probably
# right: at the SE target, P(reported band is the true band) at a band centre runs 0.639
# at width 1.0 and 0.850 at width 1.6. The instrument cannot resolve a tenth of a logit at
# six to twelve observations.
BAND_WIDTH = 1.6
BAND_CUTS: tuple[float, ...] = (-2.4, -0.8, 0.8, 2.4)


def ability_band(theta_hat: float) -> tuple[int, str]:
    """Coarse 1-5 level and its label, for reporting alongside the percentile.

    `np.digitize` is right-open, so a theta exactly on a cut point falls in the HIGHER
    band, consistently, here and in `band_probabilities`. The previous `round(3 + theta)`
    used banker's rounding, so -0.5 tied down to level 2 while +0.5 tied up to level 4.
    """
    level = int(np.digitize(float(theta_hat), BAND_CUTS)) + 1
    return level, BAND_LABELS[level]


def band_lower_bound(level: int) -> float | None:
    """Where `level` begins on theta. None for the lowest band, which is unbounded."""
    if level <= 1:
        return None
    if level - 2 < len(BAND_CUTS):
        return BAND_CUTS[level - 2]
    return None


def band_probabilities(posterior: np.ndarray) -> dict[int, float]:
    """P(theta in each band), summed over the grid.

    Derived from `ability_band`'s OWN rule applied per grid point, so the reported level
    and its probability cannot disagree about where a boundary is. This is the number a
    reader assumes `certainty_percent` already was, and it is not.
    """
    weights = np.asarray(posterior, dtype=float)
    if weights.shape != THETA_GRID.shape:
        raise ValueError(
            f"posterior length {weights.size} does not match THETA_GRID {THETA_GRID.size}"
        )
    total = float(weights.sum())
    if total <= 0.0:
        raise ValueError("posterior has non-positive mass")

    levels = np.digitize(THETA_GRID, BAND_CUTS) + 1
    normalised = weights / total
    return {
        int(level): float(normalised[levels == level].sum())
        for level in sorted(set(levels.tolist()))
    }
