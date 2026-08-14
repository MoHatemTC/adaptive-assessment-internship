"""3PL item response theory: the measurement core of the adaptive engine.

Pure functions over numpy arrays — no I/O, no configuration, no state. Ported from the
proven ``cat_engine`` implementation; the mathematics is unchanged, only the boundary
around it is new.

THE MODEL

A question is described by three calibrated parameters:

    a   discrimination — how sharply the item separates ability either side of ``b``
    b   difficulty     — the ability at which a non-guessing candidate has even odds
    c   guessing floor — the probability a candidate well below ``b`` still answers right

and the probability of a correct response at ability ``theta`` is

    P(theta) = c + (1 - c) / (1 + exp(-a * (theta - b)))

Ability is estimated on a fixed grid (``THETA_GRID``) rather than by root-finding. The
grid makes the posterior an explicit object: it can be serialized into the adaptive
state, carried across processes, and read for any summary statistic without re-deriving
anything. At 41 points over [-4, 4] the discretisation error is far below the standard
error of any realistic test length.
"""

from __future__ import annotations

import numpy as np

# Ability grid. Spacing 0.2 over [-4, 4]: fine enough that discretisation error is
# negligible against a standard error that never falls below ~0.5 in a short test, coarse
# enough that a full posterior update costs microseconds.
THETA_GRID = np.arange(-4.0, 4.0001, 0.2)
GRID_SIZE = int(THETA_GRID.size)

# How an initial belief widens or narrows the prior. Confidence 0 gives a prior nearly as
# flat as no belief at all; confidence 1 gives one tight enough that two or three
# contradicting responses are still sufficient to override it. An initial belief sets
# where the search starts — it is never counted as observed evidence.
PRIOR_SD_WIDE = 2.0
PRIOR_SD_NARROW = 0.6


def probability_correct(theta, a: float, b: float, c: float):
    """3PL probability of a correct response. Accepts scalar or array ``theta``."""
    return c + (1.0 - c) / (1.0 + np.exp(-a * (theta - b)))


def fisher_information(theta: float, a: float, b: float, c: float) -> float:
    """Exact 3PL Fisher information at a point.

        I(theta) = a^2 * ((P - c) / (1 - c))^2 * (1 - P) / P

    Not the 2PL approximation ``a^2 * P * (1 - P)``, which ignores the guessing floor and
    overstates how much a hard item tells you about a weak candidate.
    """
    if c >= 1.0:
        return 0.0
    p = float(np.clip(probability_correct(theta, a, b, c), 1e-9, 1 - 1e-9))
    ratio = (p - c) / (1.0 - c)
    return float((a**2) * (ratio**2) * ((1.0 - p) / p))


def expected_fisher_information(
    posterior: np.ndarray, a: float, b: float, c: float
) -> float:
    """Fisher information averaged over the posterior rather than taken at its mean.

    Information at a point estimate is only the right criterion if the point estimate is
    right. Weighting by where the candidate plausibly *is* uses the same belief the
    ability estimate itself is read from.
    """
    infos = np.array([fisher_information(float(t), a, b, c) for t in THETA_GRID])
    return float(np.sum(posterior * infos))


def kullback_leibler_information(
    theta_hat: float, a: float, b: float, c: float, delta: float
) -> float:
    """KL divergence between the response distributions at ``theta_hat`` and nearby.

    Used for the first few questions, where Fisher information is a poor criterion
    because it is local: it asks which item is most informative *exactly here*, when
    "here" is barely known. KL integrates over a neighbourhood of width ``delta``, so it
    prefers items that separate a whole region of ability.
    """
    mask = np.abs(THETA_GRID - theta_hat) <= delta
    if not mask.any():
        return 0.0
    p_hat = float(np.clip(probability_correct(theta_hat, a, b, c), 1e-9, 1 - 1e-9))
    p = np.clip(probability_correct(THETA_GRID[mask], a, b, c), 1e-9, 1 - 1e-9)
    kl = p_hat * np.log(p_hat / p) + (1 - p_hat) * np.log((1 - p_hat) / (1 - p))
    return float(np.sum(kl))


def uniform_prior() -> np.ndarray:
    """Flat prior — the neutral belief used when no initial competency is supplied."""
    return np.ones_like(THETA_GRID) / THETA_GRID.size


def prior_from_initial(level: int, confidence: float) -> np.ndarray:
    """THE conversion from an initial (level, confidence) belief into a prior.

    Level 1..5 maps linearly onto theta -2..+2 — the same anchoring the reporting bands
    use, so a stated level 3 centres the search exactly where a reported level 3 lives.
    Confidence scales the prior's width between ``PRIOR_SD_WIDE`` (confidence 0: barely
    more than flat) and ``PRIOR_SD_NARROW`` (confidence 1: firmly centred but still
    overridable by a handful of responses).

    This is the only place that conversion exists. Anything that needs it calls this.
    """
    mean = (float(level) - 3.0) * 1.0
    sd = PRIOR_SD_WIDE - (PRIOR_SD_WIDE - PRIOR_SD_NARROW) * float(confidence)
    density = np.exp(-0.5 * ((THETA_GRID - mean) / sd) ** 2)
    return density / density.sum()


def apply_graded_evidence(
    posterior: np.ndarray, a: float, b: float, c: float, score: float, exponent: float
) -> np.ndarray:
    """Bayes update on one piece of graded evidence; returns the normalized posterior.

    The likelihood generalizes the Bernoulli response model to partial credit and
    down-weighted evidence:

        L(theta) = [ P(theta)^score * (1 - P(theta))^(1 - score) ] ^ exponent

    At score in {0, 1} and exponent 1 this is exactly the dichotomous 3PL update. An
    exponent of 0 leaves the posterior untouched — evidence the grader could not stand
    behind moves nothing.
    """
    p = np.clip(probability_correct(THETA_GRID, a, b, c), 1e-9, 1 - 1e-9)
    likelihood = (p**score * (1.0 - p) ** (1.0 - score)) ** exponent
    updated = posterior * np.clip(likelihood, 1e-12, None)
    total = updated.sum()
    if total <= 0:  # pragma: no cover — unreachable while the clip floor holds
        updated = np.ones_like(THETA_GRID)
        total = updated.sum()
    return updated / total


def estimate(posterior: np.ndarray) -> tuple[float, float]:
    """EAP ability estimate and its standard error, read from the posterior.

    These are always derived, never stored: the posterior is the single authoritative
    representation of belief, and every summary is recomputed from it.
    """
    weights = np.asarray(posterior, dtype=float)
    theta_hat = float(np.sum(THETA_GRID * weights))
    standard_error = float(np.sqrt(np.sum((THETA_GRID - theta_hat) ** 2 * weights)))
    return theta_hat, standard_error


BAND_LABELS = {
    1: "Novice",
    2: "Developing",
    3: "Competent",
    4: "Proficient",
    5: "Expert",
}

# Interior bands 1.6 logits wide — the width at which the reported band is probably right
# at the standard-error target (P ≈ 0.85 at a band centre). The single source of truth
# for where a level begins.
BAND_CUTS: tuple[float, ...] = (-2.4, -0.8, 0.8, 2.4)


def ability_band(theta_hat: float) -> tuple[int, str]:
    """Coarse 1-5 level and its label. ``np.digitize`` is right-open, so a theta exactly
    on a cut point falls in the higher band, consistently here and in
    ``band_probabilities``."""
    level = int(np.digitize(float(theta_hat), BAND_CUTS)) + 1
    return level, BAND_LABELS[level]


def band_probabilities(posterior: np.ndarray) -> dict[int, float]:
    """P(theta in each band), summed over the grid.

    Derived from ``ability_band``'s own rule applied per grid point, so the reported
    level and its probability cannot disagree about where a boundary is.
    """
    weights = np.asarray(posterior, dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        raise ValueError("posterior has non-positive mass")
    levels = np.digitize(THETA_GRID, BAND_CUTS) + 1
    normalised = weights / total
    return {
        int(level): float(normalised[levels == level].sum())
        for level in sorted(set(levels.tolist()))
    }
