"""The final assessment report — read entirely from the state, computed nowhere else.

Every number is derived from the posterior at report time by the same functions the
runtime uses, so the report cannot disagree with the run that produced it.

``band_probability`` is reported beside the level on purpose: at the standard-error
target, the probability that the reported band is the true band runs around 0.85 at a
band centre and worse near a boundary. A host that renders the level alone asserts a
confidence the measurement does not support; this field is the honest number to show
with it.
"""

from __future__ import annotations

import numpy as np
from pydantic import Field

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.convergence import StopReason
from adaptive_engine.graph import blocked_competencies, failing_competencies
from adaptive_engine.irt import ability_band, band_probabilities, estimate
from adaptive_engine.models import FrozenModel
from adaptive_engine.state import AdaptiveState


class CompetencyReport(FrozenModel):
    """One competency's result."""

    competency_id: str
    theta_hat: float
    standard_error: float
    level: int = Field(ge=1, le=5)
    band: str
    #: P(the reported level is the true one), read from the same posterior.
    band_probability: float = Field(ge=0.0, le=1.0)
    observations: int = Field(ge=0)
    converged: bool


class AssessmentReport(FrozenModel):
    """The result of the whole run.

    ``converged`` distinguishes a completed measurement from a terminated one;
    ``stop_reason`` says why the run ended either way. The graph fields carry what the
    prerequisite structure concluded: which nodes showed a clear failure pattern, and
    which competencies were deprioritized because of one.
    """

    assessment_id: str
    assessment_version: str
    questions_answered: int = Field(ge=0)
    converged: bool
    stop_reason: StopReason
    competencies: list[CompetencyReport]
    failing_competencies: list[str]
    blocked_competencies: list[str]


def build_report(
    compiled: CompiledAssessment,
    state: AdaptiveState,
    stop_reason: StopReason,
    converged_ids: frozenset[str],
) -> AssessmentReport:
    """Assemble the report for a finished run."""
    competencies: list[CompetencyReport] = []
    for competency_id in compiled.competency_ids:
        competency_state = state.competency_states[competency_id]
        posterior = np.asarray(competency_state.posterior, dtype=float)
        theta_hat, standard_error = estimate(posterior)
        level, band = ability_band(theta_hat)
        competencies.append(
            CompetencyReport(
                competency_id=competency_id,
                theta_hat=theta_hat,
                standard_error=standard_error,
                level=level,
                band=band,
                band_probability=band_probabilities(posterior).get(level, 0.0),
                observations=competency_state.observations,
                converged=competency_id in converged_ids,
            )
        )
    return AssessmentReport(
        assessment_id=compiled.assessment_id,
        assessment_version=compiled.version,
        questions_answered=state.questions_answered,
        converged=stop_reason is StopReason.ALL_COMPETENCIES_CONVERGED,
        stop_reason=stop_reason,
        competencies=competencies,
        failing_competencies=sorted(failing_competencies(state.graph_evidence)),
        blocked_competencies=sorted(blocked_competencies(compiled, state.graph_evidence)),
    )
