"""Assessment certainty: monotonic in SE reduction (not intake-inflated)."""

import numpy as np

from engine import GRID, SD_UNINFORMED, SE_TARGET

_prior = np.exp(-0.5 * (GRID / SD_UNINFORMED) ** 2)
_prior /= _prior.sum()
SE_PRIOR_MAX = float(np.sqrt(np.sum((GRID - 0.0) ** 2 * _prior)))


def posterior_certainty_pct(se: float, se_start: float | None = None) -> float:
    """
    Certainty that θ̂ is well-determined, driven only by posterior SE.

    Maps SE_start (or SE_PRIOR_MAX) → ~0%, SE_TARGET → 90%, below → up to 100%.
    Monotonic decreasing in SE — answering informative items must not lower certainty.
    """
    se = max(float(se), 1e-6)
    start = float(se_start) if se_start and se_start > SE_TARGET else SE_PRIOR_MAX
    start = max(start, SE_TARGET + 0.05)

    if se <= SE_TARGET:
        # Bonus for beating the target
        return float(np.clip(90.0 + 10.0 * (1.0 - se / SE_TARGET), 90.0, 100.0))

    # Linear map: SE=start → 5%, SE=SE_TARGET → 90%
    progress = (start - se) / (start - SE_TARGET)
    return float(np.clip(5.0 + 85.0 * progress, 5.0, 89.0))


def intake_certainty_pct(self_confidence: str) -> float:
    return 40.0 if self_confidence == "low" else 70.0


def combined_certainty_pct(
    se: float,
    self_confidence: str,
    q_count: int,
    prior_sd: float = SD_UNINFORMED,
    se_start: float | None = None,
) -> float:
    """
    Small intake blend for q=0 only; thereafter pure posterior certainty.

    Previous weighting made certainty *fall* after correct answers when SE
    stayed above target (logs: 84% → 59% despite all-correct streaks).
    """
    posterior = posterior_certainty_pct(se, se_start=se_start)
    if q_count <= 0:
        intake = intake_certainty_pct(self_confidence)
        return float(np.clip(0.4 * intake + 0.6 * posterior, 0, 100))
    return posterior


def certainty_label(pct: float) -> str:
    if pct >= 85:
        return "High"
    if pct >= 65:
        return "Moderate"
    if pct >= 40:
        return "Low"
    return "Very low"
