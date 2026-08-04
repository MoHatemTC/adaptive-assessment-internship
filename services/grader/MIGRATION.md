# grader — migration checklist

One response becomes GradedOutcomes. Per modality.

## Owns

- MCQ index comparison
- code execution, static analysis and scoring
- voice/open rubric projection
- the unscorable classification

## Modules that move here

- `backend/app/services/orchestrator/grader.py — GraderAgent, the router`
- `backend/app/services/code_adaptive/ — execution, scoring, evidence`
- `backend/app/services/voice/ — evaluator, evidence projection, rubrics`

## Planned surface

| method | path | returns |
|---|---|---|
| `POST` | `/grade/mcq` | chosen index -> GradedResponseDTO |
| `POST` | `/grade/code` | source -> GradedResponseDTO (sandboxed) |
| `POST` | `/grade/open` | pre-evaluated rubric package -> GradedResponseDTO |

## Note

The only service that needs a sandbox and an LLM, and therefore the only one that needs egress. Splitting it out is what lets the orchestrator run with no network at all.

## Checklist

- [ ] Move the modules above, keeping their tests, into `app/`
- [ ] Replace `backend/` imports of them with a client that speaks `adaptive_contracts`
- [ ] Keep the monolith path working behind a flag until the client is proven in shadow
- [ ] Contract tests: the client and this service agree on every DTO in `adaptive_contracts`
- [ ] Load: the hot path stays inside its budget (see `docs/microservices.md`)
- [ ] Delete the monolith copy and the flag
