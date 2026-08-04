"""The envelope types every service speaks.

ONE PACKAGE, VERSIONED, OWNED BY NOBODY IN PARTICULAR

These are lifted verbatim from `backend/app/schemas/orchestration.py` and
`backend/app/schemas/voice.py` so a migration is an import change rather than a rewrite. The
monolith keeps its copies until each service actually moves; the duplication is temporary
and deliberate, and `docs/microservices.md` says how it ends.

WHY `GradedOutcome` IS THE NARROW WAIST

An MCQ answer, a code submission and a spoken response are graded by completely different
machinery and end in the same statement: *this response says this much about this variable*.
That is the only thing the measurement layer knows about, and it is why a grader can be a
separate service without the orchestrator learning what a test case is.
"""

from .envelopes import (
    BankItemRef,
    CatParameters,
    GradedOutcomeDTO,
    GradedResponseDTO,
    InferredSignalDTO,
    MeasuredVariableRef,
    SCHEMA_VERSION,
)

__all__ = [
    "SCHEMA_VERSION",
    "CatParameters",
    "MeasuredVariableRef",
    "BankItemRef",
    "GradedOutcomeDTO",
    "GradedResponseDTO",
    "InferredSignalDTO",
]
