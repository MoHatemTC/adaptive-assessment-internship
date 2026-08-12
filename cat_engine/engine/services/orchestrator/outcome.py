"""What every modality reduces to, and the one update that consumes it.

THE UNIFYING ABSTRACTION

An MCQ answer, a code submission and (later) an open-ended response are graded by
completely different machinery, but all three end in the same statement: *this response
says something of this strength about this variable*. `GradedOutcome` is that statement,
and it is the only thing the orchestrator's measurement layer knows about.

    MCQ    score in {0.0, 1.0} from an exact index comparison, weight 1.0
    code   one outcome per CompetencyEvidence — score as graded, weight the evidence
           strength times how much the question loads on that variable
    open   an LLM rubric score per criterion, projected onto competencies, with weight the
           evidence strength times the grader's confidence
    voice  identical to `open` — same evaluator, same rubric criteria, same projection. The
           modality only records how the answer was collected and how a report describes it.

THE FRACTIONAL LIKELIHOOD

The MCQ engine updates its posterior with a Bernoulli likelihood: P(theta) if the answer
was right, 1 - P(theta) if it was wrong. A code score is not binary — 0.62 is a real and
common value — so the likelihood generalises to

    L(theta) = [ P(theta)^s * (1 - P(theta))^(1-s) ] ^ w

Two properties make this the right generalisation rather than merely a plausible one:

    IT REDUCES EXACTLY. At s in {0, 1} and w = 1 this is the Bernoulli likelihood, term
    for term. The MCQ path is therefore unchanged by construction, not by inspection, and
    `test_orchestration.py` asserts float equality against `irt.posterior_update` to keep
    it that way.

    ZERO WEIGHT MEANS ZERO UPDATE. w = 0 makes L identically 1, so the posterior is
    untouched. The rule that an infrastructure failure must never move a candidate's
    estimate now falls out of the arithmetic instead of being special-cased — and it
    cannot be forgotten at a call site, because there is nothing to forget.

Fractional exponents are not a partial-credit MODEL. A graded response model with
calibrated step parameters is the destination; this is the honest interim, treating a
score of 0.62 as 0.62 of an observation rather than inventing thresholds nobody measured.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cat_engine.engine.services.adaptive.irt import THETA_GRID, probability_correct


@dataclass(frozen=True)
class GradedOutcome:
    """One graded statement about one variable, from any modality.

    `weight` is how much of an observation this is worth, in [0, 1]. It is deliberately
    separate from `score`: a submission that failed to compile scores 0 but demonstrates
    very little, and punishing a competency at full strength for a typo would measure the
    wrong thing.
    """

    variable: str
    score: float
    weight: float = 1.0
    confidence: float = 1.0
    source_item_id: str = ""
    modality: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score {self.score} outside [0, 1] for {self.variable}")
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"weight {self.weight} outside [0, 1] for {self.variable}")

    @property
    def moves_the_estimate(self) -> bool:
        """False when this outcome carries no evidence and must change nothing."""
        return self.weight > 0.0


def graded_likelihood(
    a: float, b: float, c: float, score: float, weight: float
) -> np.ndarray:
    """L(theta) over THETA_GRID for one graded outcome.

    Clipped away from 0 and 1 before exponentiation: P = 0 with a non-zero score would
    make the whole posterior collapse to zeros, and the renormalisation that follows
    cannot recover from that.
    """
    p = np.clip(probability_correct(THETA_GRID, a, b, c), 1e-9, 1.0 - 1e-9)
    if weight <= 0.0:
        return np.ones_like(p)
    return (p**score * (1.0 - p) ** (1.0 - score)) ** weight


def graded_posterior_update(
    posterior: np.ndarray,
    a: float,
    b: float,
    c: float,
    score: float,
    weight: float = 1.0,
) -> tuple[np.ndarray, float, float]:
    """Bayes update on one graded outcome.

    Returns ``(posterior, theta_hat, standard_error)`` with `theta_hat` the posterior mean
    (EAP) and the standard error its posterior standard deviation — the same summary the
    MCQ engine reports, so a caller cannot tell which modality produced an estimate. That
    is the point: a competency measured by code and one measured by MCQ are the same kind
    of number, and mixing them is only legitimate if this is true.
    """
    likelihood = graded_likelihood(a, b, c, score, weight)
    updated = np.asarray(posterior, dtype=float) * likelihood

    total = updated.sum()
    if total <= 0:  # pragma: no cover — unreachable while the clip floor holds
        updated = np.ones_like(THETA_GRID)
        total = updated.sum()
    updated = updated / total

    theta_hat = float(np.sum(THETA_GRID * updated))
    standard_error = float(np.sqrt(np.sum((THETA_GRID - theta_hat) ** 2 * updated)))
    return updated, theta_hat, standard_error
