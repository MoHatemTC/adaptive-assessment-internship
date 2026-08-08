"""The measurement core: item response theory on the mastery scale.

Pure functions over floats and lists. No I/O, no LLM, no configuration — deliberately,
because this module decides what a candidate's performance means and must never depend on
whether a network call succeeded.

THE MODEL

Belief about a competency is a Beta posterior over mastery in [0, 1], updated by evidence
weight. The doc's destination is a graded response model, and that is the right one — but
GPCM needs calibrated item parameters, which need real learner responses, which do not
exist yet. Beta is chosen because it is honest in the meantime: a real posterior with a
real variance, whose standard error falls only as evidence accumulates, swappable without
changing anything upstream.

A question is described by two parameters on the same 0..1 scale:

    a   discrimination — how sharply it separates candidates either side of `b`
    b   difficulty     — the mastery at which the candidate has even odds

    P(m) = 1 / (1 + exp(-1.7 * a * (m - b)))

There is no guessing parameter, and its absence is a fact about the domain rather than a
simplification: nobody guesses their way to a passing test suite the way they can guess a
multiple-choice option. With c = 0 the exact 3PL information reduces to a^2 P(1-P), so
this is the exact quantity, not the 2PL approximation of a different one.

SELECTION CRITERION

Two phases, following the MCQ engine:

    KL          first three items, while the estimate is still vague. Fisher information
                is LOCAL — it asks which question is most informative exactly here, when
                "here" is barely known. KL integrates over a neighbourhood, so it prefers
                questions separating a whole region of ability.
    E[Fisher]   thereafter, averaged over the posterior rather than taken at its mean.
"""

from __future__ import annotations

import math
from typing import Literal

# Mastery grid for posterior-weighted quantities. 41 points over [0, 1], mirroring the MCQ
# engine's 41 points over [-4, 4]: fine enough that discretisation error is far below any
# realistic standard error, coarse enough to be microseconds.
MASTERY_GRID: tuple[float, ...] = tuple(i / 40.0 for i in range(41))

# KL while the estimate is vague, expected Fisher once it is worth localising around.
# Three items is where the Beta standard error stops moving sharply: 0.289 -> 0.220 ->
# 0.199 over the first three, after which a local criterion begins to mean something.
KL_PHASE_ITEMS = 3

# KL neighbourhood at the first item, as a fraction of the ability range. The MCQ engine
# uses 3.0 on a theta range of 8; mastery spans 1, so the same proportion is 0.375. It
# shrinks as sqrt(n+1): early on ask which question separates a wide region, later a
# narrow one.
KL_DELTA_AT_START = 0.375

# Logistic-to-normal scaling constant, as in the MCQ engine.
SCALING = 1.7

# Mastery bands, as reported to a candidate. Derived from the posterior on demand and
# never stored, so a band can never drift from the estimate it describes.
BANDS: tuple[tuple[float, int, str], ...] = (
    (0.20, 1, "Novice"),
    (0.40, 2, "Developing"),
    (0.60, 3, "Competent"),
    (0.80, 4, "Proficient"),
    (1.01, 5, "Expert"),
)


def probability_correct(mastery: float, a: float, b: float) -> float:
    """Probability of a successful response at `mastery`."""
    a = max(float(a), 0.1)
    return 1.0 / (1.0 + math.exp(-SCALING * a * (mastery - b)))


def fisher_information(
    mastery: float, a: float, b: float, loading: float = 1.0
) -> float:
    """Fisher information at a point on the mastery scale.

        I = a^2 * P * (1 - P) * loading

    This is the exact 3PL quantity at c = 0, not an approximation of it:

        a^2 * ((P - c)/(1 - c))^2 * (1 - P)/P   ->   a^2 * P * (1 - P)

    Two properties make it the right selection criterion. It PEAKS WHERE THE OUTCOME IS
    UNCERTAIN — maximal at P = 0.5, where the question is matched to the estimate and the
    response genuinely could go either way. And it SCALES WITH DISCRIMINATION SQUARED, so
    a sharply discriminating question at the right difficulty is worth several blunt ones.

    `loading` is the question's weight on the competency being measured, so a question
    that merely brushes the target contributes proportionally less.
    """
    p = probability_correct(mastery, a, b)
    a = max(float(a), 0.1)
    return (a**2) * p * (1.0 - p) * loading


def beta_posterior_grid(alpha: float, beta: float) -> tuple[float, ...]:
    """The Beta posterior evaluated on MASTERY_GRID, normalised to sum to 1.

    Makes the belief an explicit object rather than a mean and a standard deviation, which
    is what posterior-expected information needs: a function cannot be averaged over a
    distribution you only have two moments of.

    Computed in log space. Alpha and beta reach double figures within a few questions and
    m**(alpha-1) underflows to zero across most of the grid well before that, which would
    silently yield a degenerate posterior concentrated on a single point.
    """
    logs = []
    for m in MASTERY_GRID:
        m = min(max(m, 1e-9), 1.0 - 1e-9)
        logs.append((alpha - 1.0) * math.log(m) + (beta - 1.0) * math.log(1.0 - m))
    peak = max(logs)
    weights = [math.exp(v - peak) for v in logs]
    total = sum(weights) or 1.0
    return tuple(w / total for w in weights)


def expected_fisher_information(
    posterior: tuple[float, ...], a: float, b: float, loading: float = 1.0
) -> float:
    """Fisher information averaged over the posterior rather than taken at its mean.

    Information at a point estimate is only the right criterion if the point estimate is
    right. Early in a session it is not, so maximising I(mastery_hat) chases a number that
    is still moving and can select a question the next response invalidates. Weighting by
    where the candidate plausibly IS uses the same belief the estimate is read from.
    """
    return sum(
        w * fisher_information(m, a, b, loading)
        for m, w in zip(MASTERY_GRID, posterior)
    )


def kl_information(
    mastery_hat: float, a: float, b: float, delta: float, loading: float = 1.0
) -> float:
    """KL divergence between the response distributions at `mastery_hat` and nearby.

    Used while the estimate is vague, where Fisher information is a poor criterion because
    it is local. KL integrates over a neighbourhood of width `delta`, so it prefers
    questions that separate a whole region of ability — which is what an early question is
    for.
    """
    p_hat = min(max(probability_correct(mastery_hat, a, b), 1e-9), 1.0 - 1e-9)
    total = 0.0
    for m in MASTERY_GRID:
        if abs(m - mastery_hat) > delta:
            continue
        p = min(max(probability_correct(m, a, b), 1e-9), 1.0 - 1e-9)
        total += p_hat * math.log(p_hat / p) + (1.0 - p_hat) * math.log(
            (1.0 - p_hat) / (1.0 - p)
        )
    return total * loading


def selection_criterion(evidence_count: int) -> Literal["KL", "E[Fisher]"]:
    """Which information criterion applies at this point in the session."""
    return "KL" if evidence_count < KL_PHASE_ITEMS else "E[Fisher]"


def kl_delta(evidence_count: int) -> float:
    """KL neighbourhood width, shrinking as the estimate sharpens."""
    return KL_DELTA_AT_START / math.sqrt(evidence_count + 1)


def posterior_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


def posterior_standard_error(alpha: float, beta: float) -> float:
    """Posterior SD of the Beta. Falls only as real evidence arrives."""
    return math.sqrt((alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1.0)))


def mastery_band(mastery: float, observed: bool) -> tuple[int | None, str]:
    """Mastery as a 1-5 level and its label, or (None, "Not assessed") while unmeasured.

    None rather than 3 when unobserved: a mid band reads as a measured "Competent", which
    is exactly the false impression a Beta(1,1) mean of exactly 0.5 would give.
    """
    if not observed:
        return None, "Not assessed"
    for ceiling, level, label in BANDS:
        if mastery < ceiling:
            return level, label
    return 5, "Expert"


def stop_rule_calibration(
    target: float, min_questions: int, max_questions: int
) -> dict:
    """Can the precision target actually END a session, between the floor and the cap?

    A stopping rule is only adaptive if it can bind, and it fails silently in two
    directions. This engine's predecessors hit both, one per question type:

      TRIVIAL      the target sits above where the prior already is, so it is met almost
                   immediately and min_questions decides every session length. Traced code
                   sessions crossed SE 0.20 at question 2 and still ran to 5.
      UNREACHABLE  the target is below what max_questions can deliver, so the rule never
                   fires and the cap decides. The MCQ bank needed 13 questions to reach
                   SE 0.65 against a 12-question limit.

    Both look like a working adaptive test from outside, which is why this is computed
    rather than assumed. Modelled on the median candidate (p = 0.5, the slowest case) with
    one unit of evidence per question, so it is a LOWER bound on convergence speed: a
    strong candidate has less posterior variance and will converge sooner, possibly before
    min_questions. A BINDING verdict means the rule can fire, not that it always will.
    """

    def se_after(n: int, p: float = 0.5) -> float:
        return posterior_standard_error(1.0 + n * p, 1.0 + n * (1.0 - p))

    reached = next((n for n in range(max_questions + 1) if se_after(n) <= target), None)
    floor = se_after(max_questions)

    if reached is None:
        verdict = "UNREACHABLE"
        detail = (
            f"SE {target} is below the {floor:.4f} floor reachable in {max_questions} "
            "questions — the rule can never fire and max_questions decides every session"
        )
    elif reached <= min_questions:
        verdict = "TRIVIAL"
        detail = (
            f"SE {target} is met by question {reached}, at or before the min_questions "
            f"floor of {min_questions} — min_questions decides every session, not precision"
        )
    else:
        verdict = "BINDING"
        detail = (
            f"SE {target} is met around question {reached}, between the floor of "
            f"{min_questions} and the cap of {max_questions} — the rule can end a session"
        )
    return {
        "verdict": verdict,
        "detail": detail,
        "expected_stop_question": reached,
        "reachable_floor": round(floor, 4),
        "target": target,
    }
