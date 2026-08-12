# `cat_engine/contracts/`

The DTOs a host receives. **Deliberately independent of `engine/`**, and it stays that way.

Two reasons, and the first is a security boundary rather than a preference:

- **The wire contract is not the internal one.** `BankItemRef` omits the payload that
  `BankItem` carries, and that omission is what stops the ranking path reading a question.
  Re-exporting the engine's schema would delete it.
- A consumer that is not this engine — a frontend's generated client, another team's
  service — should be able to depend on these shapes without pulling in numpy, a sandbox
  client and 600 KB of question banks.

The duplication is therefore permanent rather than transitional, and it is policed:
`tests/test_contract_parity.py` asserts that the types which genuinely do mirror engine
types still agree field for field.

## Why `GradedOutcome` is the narrow waist

An MCQ answer, a code submission and a spoken response are graded by completely different
machinery and end in the same statement: *this response says this much about this variable*.
That is the only thing the measurement layer knows about, and it is why a modality can be
added without the loop learning what a test case is.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | Re-exports every public type and `SCHEMA_VERSION`. Callers import from the package root. |
| `envelopes.py` | The narrow waist: `GradedOutcomeDTO`, `BankItemRef`, `CatParameters`, `Modality`, `InferredSignalDTO`. Deliberately anaemic — no behaviour, no imports from anything above. |
| `assessment.py` | The lifecycle surface: `CreateAssessmentRequest`, `AssessmentStateResponse`, `PresentingDTO`, `GradeReceiptDTO`, `AssessmentReportDTO`, `DiagnosticsResponse`. What a frontend is built against. |
| `bank.py` | What a bank is on the wire: `BankSummary`, `BankItemFull`, `CompetencyGraphDTO`, `PolicyDTO`, `BankValidationReport`. |
| `grading.py` | One response in, `GradedOutcome[]` out: the grade requests, `VoicePackageDTO`, transcription, and the trial-run types. |
| `graph.py` | Node state in, a delta out: `PropagateRequest/Response`, `CoverageRequest/Response`, `ManifestResponse`. Carries no session state. |
| `ingest.py` | One uploaded file becomes a registered bank: `UploadReceipt`, `UploadStatus`, `DerivedGraphDTO`. |
| `scope.py` | A selection in, an induced sub-graph and an allowlist out: `ScopeRequest`, `ScopeManifest`, `ScopeCoverageDTO`, `ScopeSummaryDTO`. |

## The invariant these types enforce

`InferredSignalDTO` has **no `score` and no `weight` field**. A deduction *from* a response
is not a second response, and multiplying it back in would count one answer twice — with the
damage landing on the standard error, which is what the assessment stops on. Asserted in
`tests/test_contracts.py::TestInferredSignalCannotCarryEvidence`.
