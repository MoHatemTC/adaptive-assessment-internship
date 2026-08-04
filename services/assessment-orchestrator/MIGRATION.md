# assessment-orchestrator — migration checklist

The loop, the posterior, selection and stopping.

## Owns

- the CAT posterior per competency
- item selection and the queue
- convergence and the stopping rules
- the assessment report

## Modules that move here

- `backend/app/services/orchestrator/orchestrator.py — the loop`
- `backend/app/services/orchestrator/variables.py — examinee variables`
- `backend/app/services/adaptive/irt.py — the 3PL core`
- `backend/app/services/adaptive/convergence.py — stopping`

## Planned surface

| method | path | returns |
|---|---|---|
| `POST` | `/assessments` | begin an assessment over several competencies |
| `GET` | `/assessments/{id}/next` | the next item to present |
| `POST` | `/assessments/{id}/responses` | record a response, advance the loop |
| `GET` | `/assessments/{id}/report` | the assessment report |

## Note

Holds a read-through cache of item parameters keyed by bank version: selection needs a, b and c for every candidate item on every step, and a round trip per decision would put the network in the hot loop.

## Checklist

- [ ] Move the modules above, keeping their tests, into `app/`
- [ ] Replace `backend/` imports of them with a client that speaks `adaptive_contracts`
- [ ] Keep the monolith path working behind a flag until the client is proven in shadow
- [ ] Contract tests: the client and this service agree on every DTO in `adaptive_contracts`
- [ ] Load: the hot path stays inside its budget (see `docs/microservices.md`)
- [ ] Delete the monolith copy and the flag
