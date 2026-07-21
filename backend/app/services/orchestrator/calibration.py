"""Placing every modality's items on one ability scale.

THIS FILE IS PROVISIONAL AND THE ONLY PLACE THAT SHOULD BE.

Cross-modality selection only means anything if an MCQ item's information and a code
question's information are computed on the same scale. MCQ items already are: their a, b
and c were calibrated against real response data on theta over [-4, 4]. Code questions
were not. They carry an authored `difficulty` in [0, 1] on the mastery scale the Beta
engine used, and an authored `discrimination` — numbers chosen by a bank author's judgement,
never fitted to anyone's answers.

So mapping them onto theta is a MODELLING DECISION, not a derivation, and everything that
decision affects is confined to this module. When real response data exists, calibrate the
code items properly and delete the mapping — nothing else has to change.

WHAT THE MAPPING IS

    b_theta = logit(difficulty)

Principled rather than arbitrary: difficulty on the mastery scale is the point where a
candidate has even odds, and logit is exactly the theta at which P = 0.5 under a logistic
link. The bank's authored range [0.15, 0.70] maps to [-1.73, +0.85], which sits inside the
band the MCQ items occupy, so code questions are neither unreachably hard nor trivially
easy relative to them.

    a_theta = discrimination,  c = 0

`a` passes through: both scales express "how sharply this separates candidates either side
of b", and neither number is calibrated, so transforming one authored value into another
would add arithmetic without adding truth. c = 0 is not an approximation — a candidate
cannot guess their way to a passing test suite the way they can guess one of four options.

WHY THIS IS THE LARGEST CORRECTNESS RISK HERE

Information scales with a^2. If the authored code discriminations are systematically lower
than the calibrated MCQ ones, code questions lose every ranking and the assessment quietly
becomes MCQ-only while looking mixed. `UnifiedBank.information_parity()` exists to detect
exactly that, and it should be read before trusting a mixed session.
"""

from __future__ import annotations

import math

# The mastery scale is open at both ends: difficulty 0 or 1 would map to infinite theta.
# Clamped rather than rejected, because a bank author writing 0.0 means "as easy as this
# scale goes", not "undefined".
_MASTERY_FLOOR = 0.02
_MASTERY_CEILING = 0.98

# Bounds enforced by the Item schema. A mapped value outside them is a bank error, not a
# candidate's problem, so it is clamped and surfaced by the migration rather than raised
# at the moment someone is waiting for a question.
THETA_MIN, THETA_MAX = -4.0, 4.0


def mastery_difficulty_to_theta(difficulty: float) -> float:
    """Mastery-scale difficulty in [0, 1] -> the theta at which P = 0.5."""
    m = min(max(float(difficulty), _MASTERY_FLOOR), _MASTERY_CEILING)
    theta = math.log(m / (1.0 - m))
    return round(min(max(theta, THETA_MIN), THETA_MAX), 4)


def theta_to_mastery_difficulty(theta: float) -> float:
    """Inverse of the above, so a migration can be checked for round-trip fidelity."""
    return round(1.0 / (1.0 + math.exp(-float(theta))), 4)


def code_cat_parameters(difficulty: float, discrimination: float) -> dict[str, float]:
    """CAT parameters on theta for one code question.

    Returns the same (a, b, c) triple an MCQ item carries, so downstream code cannot tell
    the two apart — which is the property that makes ranking them against each other valid.
    """
    return {
        "a": round(max(float(discrimination), 0.05), 4),
        "b": mastery_difficulty_to_theta(difficulty),
        "c": 0.0,
    }


def open_cat_parameters(difficulty: float, discrimination: float) -> dict[str, float]:
    """CAT parameters for an open-ended item.

    Not implemented, and deliberately not guessed. Open-ended items are graded against a
    rubric with no test suite behind them, so whether they carry a guessing floor and how
    their difficulty was authored are questions the open-ended design has to answer first.
    Defining a mapping now would bake in an answer nobody has given.
    """
    raise NotImplementedError(
        "open-ended calibration is undefined until the open-ended grading design exists"
    )
