# `cat_engine/engine/schemas/`

The typed boundaries. These describe; they do not act.

Distinct from [`contracts/`](../../contracts/README.md), which is what a HOST receives.
These are the engine's own types, and the difference is load-bearing: `BankItem` carries the
payload — the answer key, the hidden tests, the rubric — and `BankItemRef` has no field to
put one in.

## Files

| File | Responsibility |
|---|---|
| `orchestration.py` | The multi-modality assessment: `BankItem`, `VariableState`, `AssessmentState`, `GradedResponse`, and the report types. `AssessmentState` is serialisable by construction — the 41-point posterior is a `list[float]` specifically so it round-trips through JSON, which is what makes a session store possible at all. |
| `adaptive.py` | The MCQ engine's boundary. |
| `code_adaptive.py` | The code engine's boundary: submissions, criterion scores, execution evidence, competency evidence. |
| `voice.py` | The voice / open half: `VoiceResponsePackage`, `VoiceEvaluation`, `GradedVoiceResponse`. |

## Why `AssessmentState` being serialisable matters

It is what lets an assessment span requests, be persisted between them, and resume in a
different process. That single property is what made splitting the monolith into services
tractable, and what made collapsing them back equally cheap — both directions were a change
of who calls whom, not of what is computed.
