"""Was this response surprising, given what we already believed?

A posterior-predictive residual. The specification proposes a hand-rolled precedence table
for deciding which of two conflicting pieces of evidence to trust — recent over repeated,
direct over inferred, and so on. Aberrant-response detection is a solved problem with real
machinery behind it, and a standardised residual subsumes that table: it weights by
evidence strength automatically and has an approximate null to threshold against.

Worth stating plainly: this codebase never implemented that precedence table, so this is
NEW surface, not a replacement. It ships report-only.

    p̄ = Σ posterior(θ) · P(θ; a, b, c)          the predicted score, before the update
    z = √w · (s − p̄) / √(p̄(1 − p̄))

`p̄(1 − p̄)` is exactly the marginal Bernoulli variance: Var(y) = E[P(1−P)] + Var(P), whose
E[P²] terms cancel. The √w factor makes z reduce to the ordinary standardised residual at
w = 1 with a binary score, and shrink toward zero as the evidence weakens.

WHAT THIS IS NOT. The fractional likelihood does not define a sampling distribution for a
fractional score, so standardising a partial credit here is a heuristic and the null is
only approximately normal. That is precisely why the residual flags and never scores: it
prioritises a verification question, and no branch is permitted to read it and move an
estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.adaptive.irt import THETA_GRID, probability_correct


@dataclass(frozen=True)
class ResponseResidual:
    variable: str
    item_id: str
    score: float
    expected: float
    weight: float
    z: float

    @property
    def aberrant(self) -> bool:
        return abs(self.z) >= settings.cat_aberrant_residual_threshold

    def as_dict(self) -> dict:
        return {
            "variable": self.variable,
            "item_id": self.item_id,
            "score": round(self.score, 4),
            "expected": round(self.expected, 4),
            "weight": round(self.weight, 4),
            "z": round(self.z, 4),
        }


def posterior_predictive_mean(
    posterior: np.ndarray, a: float, b: float, c: float
) -> float:
    """E[P(theta)] over the CURRENT posterior — before this response is absorbed."""
    weights = np.asarray(posterior, dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        return 0.5
    probabilities = probability_correct(THETA_GRID, a, b, c)
    return float(np.sum((weights / total) * probabilities))


def residual(
    *,
    variable: str,
    item_id: str,
    posterior: np.ndarray,
    a: float,
    b: float,
    c: float,
    score: float,
    weight: float,
) -> ResponseResidual:
    """The standardised residual of one response against the pre-update posterior.

    Computed BEFORE `apply_outcome`. Posterior-predictive means predictive of an
    observation not yet absorbed; computing it afterwards makes it self-referential and
    shrinks every residual toward zero, which would hide exactly the responses it exists
    to surface.
    """
    expected = posterior_predictive_mean(posterior, a, b, c)
    variance = expected * (1.0 - expected)
    if variance <= 1e-12 or weight <= 0.0:
        z = 0.0
    else:
        z = float(np.sqrt(weight) * (score - expected) / np.sqrt(variance))
    return ResponseResidual(
        variable=variable,
        item_id=item_id,
        score=float(score),
        expected=expected,
        weight=float(weight),
        z=z,
    )
