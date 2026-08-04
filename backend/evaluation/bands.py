"""The COMMON banding rule, applied to both arms' theta.

WHY THIS FILE EXISTS AT ALL

Section 6 of the test plan freezes "level boundaries" so that only the adaptation
architecture varies between arms. The two approach branches do not honour that: Approach B
maps theta to a level with `round(3 + theta)` — five bands, 1.0 logits wide — while
Approach C uses explicit cut points 1.6 logits wide. Measured on the same posterior those
two rules disagree about the level of roughly a third of candidates, which is an order of
magnitude larger than the 2pp non-inferiority margin M-03 is gated on.

Comparing each arm's NATIVE level would therefore not measure the DAG. It would measure
the band width, and it would report the result under the DAG's name.

So exact-level accuracy is computed twice, and both are reported:

    common  — this module's rule, identical for every arm. The primary endpoint (A5).
    native  — whatever the arm's own `ability_band` says. Descriptive only; it is what
              that branch would actually print on a report today.

The common cuts are Approach C's, because they are the ones with a stated derivation
(P(reported band is true band) at the SE target) rather than an inherited rounding rule.
Band COUNT is a free parameter here on purpose: the validation document's A7 says the
count is worth more decision accuracy than the DAG can plausibly buy, and `prestudy.py`
needs to vary it without touching an engine.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.adaptive.irt import THETA_GRID

# The frozen comparison scale. Interior bands 1.6 logits wide over [-4, 4].
COMMON_CUTS: tuple[float, ...] = (-2.4, -0.8, 0.8, 2.4)
COMMON_LABELS = {1: "Novice", 2: "Developing", 3: "Competent", 4: "Proficient", 5: "Expert"}


def cuts_for(n_bands: int) -> tuple[float, ...]:
    """Equal-width interior cut points for `n_bands` levels over the usable theta range.

    Anchored on [-4, 4] and centred, so band 3 of 5 straddles theta = 0 exactly as the
    frozen scale does. Used only by the Phase 0a pre-study; the comparison itself always
    runs on `COMMON_CUTS`.
    """
    if n_bands < 2:
        raise ValueError("a scale needs at least two bands")
    if n_bands == 5:
        return COMMON_CUTS
    width = 8.0 / n_bands
    return tuple(round(-4.0 + width * (i + 1), 6) for i in range(n_bands - 1))


def band_of(theta: float, cuts: tuple[float, ...] = COMMON_CUTS) -> int:
    """Level 1..len(cuts)+1. Right-open, matching the engine's `np.digitize` convention."""
    return int(np.digitize(float(theta), cuts)) + 1


def band_probabilities(
    posterior: np.ndarray, cuts: tuple[float, ...] = COMMON_CUTS
) -> dict[int, float]:
    """P(theta in each band) under a posterior on THETA_GRID, on the COMMON scale.

    Derived from `band_of`'s own rule applied per grid point, so a reported level and its
    probability cannot disagree about where a boundary is.
    """
    weights = np.asarray(posterior, dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        raise ValueError("posterior has non-positive mass")
    levels = np.digitize(THETA_GRID, cuts) + 1
    normalised = weights / total
    return {
        int(level): float(normalised[levels == level].sum())
        for level in sorted(set(levels.tolist()))
    }


@dataclass(frozen=True)
class BandScale:
    """A named banding rule, so a result can say which scale produced it."""

    name: str
    cuts: tuple[float, ...]

    @property
    def n_bands(self) -> int:
        return len(self.cuts) + 1

    def band(self, theta: float) -> int:
        return band_of(theta, self.cuts)


COMMON = BandScale("common-5x1.6", COMMON_CUTS)
