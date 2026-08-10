"""The envelope types every service speaks.

ONE PACKAGE, VERSIONED, OWNED BY NOBODY IN PARTICULAR

Deliberately independent of the engine, and it stays that way even though every service now
installs the engine as a library. Two reasons:

  - The wire contract is not the internal one. `BankItemRef` deliberately OMITS the payload
    that `BankItem` carries, and that omission is the security boundary of the whole bank
    surface. Re-exporting the engine's schema would delete it.
  - A consumer that is not this engine — a frontend's generated client, another team's
    service — should be able to depend on these shapes without pulling in numpy, a sandbox
    client and 600 KB of question banks.

The duplication is therefore permanent rather than transitional, and it is policed:
`backend/tests/test_contract_parity.py` asserts that the types which genuinely do mirror
engine types still agree field for field.

WHY `GradedOutcome` IS THE NARROW WAIST

An MCQ answer, a code submission and a spoken response are graded by completely different
machinery and end in the same statement: *this response says this much about this variable*.
That is the only thing the measurement layer knows about, and it is why a grader can be a
separate service without the orchestrator learning what a test case is.
"""

from .assessment import (
    AnswerRequest,
    AssessmentReportDTO,
    AssessmentStateResponse,
    CreateAssessmentRequest,
    DiagnosticsResponse,
    ErrorResponse,
    GradeReceiptDTO,
    PresentedItemDTO,
    PresentingDTO,
    VariableDiagnosticsDTO,
    VariableReportDTO,
)
from .bank import (
    BankItemFull,
    BankItemSubmission,
    BankSubmission,
    BankSummary,
    BankValidationReport,
    CompetencyGraphDTO,
    EdgeDecisionDTO,
    GraphEdgeDTO,
    GraphNodeDTO,
    ParityRowDTO,
    PolicyDTO,
    ValidationFinding,
)
from .envelopes import (
    SCHEMA_VERSION,
    BankItemRef,
    CatParameters,
    GradedOutcomeDTO,
    GradedResponseDTO,
    InferredSignalDTO,
    MeasuredVariableRef,
    Modality,
)
from .grading import (
    GradeCodeRequest,
    GradeMcqRequest,
    GradeOpenRequest,
    GradeRequestBase,
    PublicTestsResponse,
    TranscribeRequest,
    TranscribeResponse,
    TrialCaseDTO,
    TrialRunRequest,
    TrialRunResponse,
    VoicePackageDTO,
    VoiceTurnDTO,
)
from .graph import (
    CoverageRequest,
    CoverageResponse,
    EvidenceOutcomeDTO,
    ManifestResponse,
    PropagateRequest,
    PropagateResponse,
    PropagationItemDTO,
)

__all__ = [
    "SCHEMA_VERSION",
    # the narrow waist
    "CatParameters",
    "MeasuredVariableRef",
    "Modality",
    "BankItemRef",
    "GradedOutcomeDTO",
    "GradedResponseDTO",
    "InferredSignalDTO",
    # bank-registry
    "BankItemFull",
    "BankItemSubmission",
    "BankSubmission",
    "BankSummary",
    "BankValidationReport",
    "CompetencyGraphDTO",
    "EdgeDecisionDTO",
    "GraphEdgeDTO",
    "GraphNodeDTO",
    "ParityRowDTO",
    "PolicyDTO",
    "ValidationFinding",
    # grader
    "GradeCodeRequest",
    "GradeMcqRequest",
    "GradeOpenRequest",
    "GradeRequestBase",
    "PublicTestsResponse",
    "TranscribeRequest",
    "TranscribeResponse",
    "TrialCaseDTO",
    "TrialRunRequest",
    "TrialRunResponse",
    "VoicePackageDTO",
    "VoiceTurnDTO",
    # competency-graph
    "CoverageRequest",
    "CoverageResponse",
    "EvidenceOutcomeDTO",
    "ManifestResponse",
    "PropagateRequest",
    "PropagateResponse",
    "PropagationItemDTO",
    # assessment-orchestrator
    "AnswerRequest",
    "AssessmentReportDTO",
    "AssessmentStateResponse",
    "CreateAssessmentRequest",
    "DiagnosticsResponse",
    "ErrorResponse",
    "GradeReceiptDTO",
    "PresentedItemDTO",
    "PresentingDTO",
    "VariableDiagnosticsDTO",
    "VariableReportDTO",
]
