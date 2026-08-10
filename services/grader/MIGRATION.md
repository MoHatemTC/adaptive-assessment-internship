# grader — migration record

One response becomes `GradedOutcome`s. Per modality.

**Status: migrated.** Routes are real; there is no stub left.

## Owns

- MCQ index comparison
- code execution, static analysis and scoring
- voice/open rubric evaluation and projection
- transcription of recorded audio
- the candidate's own trial run
- the unscorable classification

## Where the code lives

The engine is installed as a library; this service's declared slice is
`app.services.code_adaptive`, `app.services.voice`, `app.services.orchestrator.grader`,
`app.services.orchestrator.outcome` and `app.services.voice_live.transcribe` — the module,
not the package, so importing a realtime room from here fails a test.

## Surface

| method | path | returns |
|---|---|---|
| `POST` | `/grade/mcq` | chosen index → `GradedResponseDTO` |
| `POST` | `/grade/code` | source → `GradedResponseDTO` (sandboxed) |
| `POST` | `/grade/open` | transcript package → `GradedResponseDTO` (evaluates, then grades) |
| `POST` | `/transcribe` | audio → text |
| `GET` | `/trial/{bank_id}/{item_id}/public-tests` | the example cases a candidate may run |
| `POST` | `/trial/code` | source → pass/fail per public case. **Grades nothing.** |

## Why it fetches its own items

The grading payload holds the answer index, the hidden test cases and the reference
solution. The caller is the orchestrator, whose responses reach a candidate's browser.
Naming the item and fetching it from `bank-registry` means none of those is ever in a
message somebody has to remember not to forward. Items are cached per bank version, so a
retry or a replay of the same item costs nothing.

## Why `/grade/open` evaluates as well as grades

The original plan expected a pre-evaluated rubric package, because in the monolith
evaluation ran in `app.main` before the sync grading path. There is no `app.main` any more,
and rubric evaluation is a model call — so putting it anywhere else would give a second
service egress, which is most of what splitting this one out bought.

## The only service that needs egress

A sandbox for candidate code and a model for rubric grading, code interpretation and
transcription. Untrusted code runs in E2B, not in this container.

**Correction to ADR-0001:** it is not the only service that needs egress. The
assessment-orchestrator's Picking Agent calls a model on every queue fill. See ADR-0002.

## Weight zero is not a score of zero

A sandbox that fell over produces outcomes of weight 0, which make the likelihood
identically 1 and move nothing. A submission that failed to compile is different: it is
weak evidence, not absent evidence, and is capped at 0.25 rather than discarded. Both are
tested, because the difference is the difference between recording our outage as a
candidate's inability and not.

## Checklist

- [x] Serve the three grading paths plus transcription and the trial run
- [x] Fetch items from `bank-registry` rather than accepting them from the caller
- [x] Contract tests: the service and `adaptive_contracts` agree on every DTO
- [x] Enforce the engine slice this service may import
- [ ] Point the orchestrator at it through `adaptive_clients` (phase 7)
- [ ] Load: the hot path stays inside its budget (see `docs/microservices.md`)
