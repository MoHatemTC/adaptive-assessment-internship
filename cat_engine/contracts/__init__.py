"""The types a host receives.

ONE PACKAGE, VERSIONED, OWNED BY NOBODY IN PARTICULAR

Deliberately independent of `engine`, and it stays that way now that both live in one
package. Two reasons:

  - The wire contract is not the internal one. `BankItemRef` deliberately OMITS the payload
    that `BankItem` carries, and that omission is the security boundary of the whole bank
    surface. Re-exporting the engine's schema would delete it.
  - A consumer that is not this engine — a frontend's generated client, another team's
    service, a host's own routes — should be able to depend on these shapes without pulling
    in numpy, a sandbox client and 600 KB of question banks.

The duplication is therefore permanent rather than transitional, and it is policed:
`tests/test_contract_parity.py` asserts that the types which genuinely do mirror
engine types still agree field for field.

WHY `GradedOutcome` IS THE NARROW WAIST

An MCQ answer, a code submission and a spoken response are graded by completely different
machinery and end in the same statement: *this response says this much about this variable*.
That is the only thing the measurement layer knows about, and it is why a modality can be
added without the loop learning what a test case is.
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
from .ingest import DerivedGraphDTO, UploadReceipt, UploadStatus
from .scope import (
    DanglingEdgeDTO,
    ScopeCoverageDTO,
    ScopeEdgeDTO,
    ScopeMainDTO,
    ScopeManifest,
    ScopeNodeDTO,
    ScopeRequest,
    ScopeSelection,
    ScopeSummaryDTO,
)

# Grouped by surface rather than sorted. The comments are the point: they say which part of
# the system each type belongs to, and alphabetising would scatter every group.
__all__ = [  # noqa: RUF022
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
    # bank-ingest
    "DerivedGraphDTO",
    "UploadReceipt",
    "UploadStatus",
    # competency-scope
    "DanglingEdgeDTO",
    "ScopeCoverageDTO",
    "ScopeEdgeDTO",
    "ScopeMainDTO",
    "ScopeManifest",
    "ScopeNodeDTO",
    "ScopeRequest",
    "ScopeSelection",
    "ScopeSummaryDTO",
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
