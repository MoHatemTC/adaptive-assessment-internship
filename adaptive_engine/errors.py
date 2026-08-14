"""The engine's error vocabulary.

Every error carries a stable machine-readable ``code`` so a host can branch on failure
class without parsing prose, and a message written for the developer who caused it. The
hierarchy is deliberately small: one class per failure a caller can actually cause and
handle differently. New codes are added when a caller demonstrably needs to distinguish a
new case, not in advance of one.
"""

from __future__ import annotations


class AdaptiveEngineError(Exception):
    """Base class for every error this package raises on purpose.

    ``code`` is part of the public contract: it is stable across releases and safe to
    branch on. The message is not — it exists to be read, not matched.
    """

    code = "adaptive_engine_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidDefinition(AdaptiveEngineError):
    """The assessment definition cannot be compiled as supplied."""

    code = "invalid_definition"


class InvalidInitialCompetency(AdaptiveEngineError):
    """An initial belief refers to an unknown competency or carries an invalid value."""

    code = "invalid_initial_competency"


class StateMismatch(AdaptiveEngineError):
    """The adaptive state belongs to a different assessment ID or version."""

    code = "state_mismatch"


class InvalidState(AdaptiveEngineError):
    """The adaptive state is internally inconsistent with the compiled assessment.

    Raised when the state references questions or competencies the assessment does not
    contain, or carries a schema version this engine does not understand. This is the
    error a host sees when it persists state against one assessment and replays it
    against a structurally different one that kept the same ID and version.
    """

    code = "invalid_state"


class StaleResponse(AdaptiveEngineError):
    """The response answers a question that is not the currently presented one.

    Covers duplicates, out-of-order delivery and delayed retries alike: the one rule is
    that ``response.question_id`` must equal ``state.current_question_id``.
    """

    code = "stale_response"


class InvalidAnswer(AdaptiveEngineError):
    """The answer payload does not fit the presented question's type."""

    code = "invalid_answer"


class AssessmentFinished(AdaptiveEngineError):
    """The assessment already completed or terminated; no further responses are valid."""

    code = "assessment_finished"
