"""The one convergence evaluator, and every named way an assessment can stop.

Convergence is a claim — "we know this candidate's level" — and it is only made when the
measurement earns it: enough observations, standard error at target, and the estimate
corroborated by the difficulty of what was actually asked. Running out of questions is a
budget outcome and is never reported as convergence; the two are different stop reasons
because they are different facts.

Nothing here finalises anything. Convergence is recomputed from the posterior each turn,
so later evidence from a multi-competency question keeps counting and there is no stored
verdict to drift out of step with the belief it summarises.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.irt import estimate
from adaptive_engine.state import AdaptiveState

# A high estimate is only credible if the candidate actually faced questions near it.
# The estimate is corroborated when the hardest question asked of this competency sits
# within this slack below theta-hat.
CORROBORATION_SLACK = 0.25


class StopReason(StrEnum):
    """Why a run ended. Convergence and forced termination are never one flag."""

    #: Every competency met the measurement criteria — the run converged.
    ALL_COMPETENCIES_CONVERGED = "all_competencies_converged"
    #: The total or per-competency question budget ran out first.
    QUESTION_BUDGET = "question_budget"
    #: No eligible questions remain for the competencies still open.
    QUESTIONS_EXHAUSTED = "questions_exhausted"


def competency_converged(
    compiled: CompiledAssessment, state: AdaptiveState, competency_id: str
) -> bool:
    """The measurement criteria for one competency, in one place.

    1. Observation floor — a couple of high-discrimination hits can crush the standard
       error before the estimate has been corroborated; the floor stops that.
    2. Precision — posterior standard error at or under the policy target.
    3. Difficulty corroboration — the hardest question this competency actually faced
       must sit near or above the estimate, so a level cannot be claimed on easy
       questions alone.
    """
    policy = compiled.definition.policy
    competency_state = state.competency_states[competency_id]
    if competency_state.observations < policy.min_questions_per_competency:
        return False
    theta_hat, standard_error = estimate(np.asarray(competency_state.posterior))
    if standard_error > policy.se_target:
        return False
    difficulties = [
        question.difficulty
        for question_id in state.administered_question_ids
        if (question := compiled.questions_by_id.get(question_id)) is not None
        and any(m.competency_id == competency_id for m in question.measures)
    ]
    if not difficulties:
        return False
    return max(difficulties) >= theta_hat - CORROBORATION_SLACK


def converged_competencies(
    compiled: CompiledAssessment, state: AdaptiveState
) -> frozenset[str]:
    """Every competency currently meeting the measurement criteria."""
    return frozenset(
        competency_id
        for competency_id in compiled.competency_ids
        if competency_converged(compiled, state, competency_id)
    )
