# `cat_engine/engine/schemas/`

The typed boundaries. These describe; they do not act.

Distinct from [`contracts/`](../../contracts/README.md), which is what a HOST receives, and
the difference is load-bearing rather than stylistic: `BankItem` carries the payload — the
answer key, the hidden tests, the rubric — and `BankItemRef` has no field to put one in. A
function that returns the wrong one is a question leak; a function that returns the wrong
TYPE is caught before it runs.

## Files

| File | Responsibility |
|---|---|
| `orchestration.py` | The multi-modality assessment. `BankItem`, `VariableState`, `AssessmentState`, `GradedResponse`, `AssessmentReport`, and the queue types. The largest of the four and the one most code touches. |
| `adaptive.py` | The MCQ engine's own boundary: `Item`, and the session types it works in. |
| `code_adaptive.py` | The code engine: submissions, criterion scores, execution evidence, competency evidence. A different scale from the MCQ engine — mastery on `[0, 1]` rather than theta on `[-4, 4]`. |
| `voice.py` | Open and spoken answers: `VoiceResponsePackage`, `VoiceEvaluation`, `GradedVoiceResponse`, `CriterionEvidence`, `QuoteTier`. |

## Three properties worth knowing

**`AssessmentState` is serialisable by construction.** The 41-point posterior is carried as
a `list[float]` specifically so it round-trips through JSON. That single property is what
lets an assessment span requests, be persisted between them, and resume in a different
process — and it is what made splitting the monolith into services tractable and collapsing
them back equally cheap. Both directions changed who calls whom, not what is computed.

**`BankItem` validates the modality against its payload.** An item claiming to be `code`
with no code payload is rejected at parse time rather than at grading time, when a candidate
is already looking at it.

**`GradedResponse.outcomes` is the narrow waist.** An MCQ answer, a code submission and a
spoken response are graded by completely different machinery and end in the same statement —
*this response says this much about this variable*. That is the only thing the measurement
layer knows about, and it is why a modality can be added without the loop learning what a
test case is.

## Why these are pydantic and not dataclasses

They cross a boundary where the other side is a bank file, a model reply, or a database row
— none of which is trustworthy. Validation at construction means a malformed bank fails when
it is loaded, naming the item, rather than three layers later as an `AttributeError`.
