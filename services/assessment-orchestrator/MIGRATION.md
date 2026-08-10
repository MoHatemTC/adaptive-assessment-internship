# assessment-orchestrator — migration record

The loop, the posterior, selection and stopping.

**Status: migrated.** Routes are real; there is no stub left. `backend/app/main.py`'s CAT
endpoints, its session store and its candidate boundary all live here now.

## Owns

- the CAT posterior per competency
- item selection and the queue
- convergence and the stopping rules
- the assessment report
- live sessions, and the candidate boundary

## Surface

| method | path | returns |
|---|---|---|
| `POST` | `/assessments` | begin over several competencies |
| `GET` | `/assessments/{id}` | state, with the presented item or the report |
| `GET` | `/assessments/{id}/next` | the next item to present |
| `POST` | `/assessments/{id}/responses` | record a response, advance the loop |
| `GET` | `/assessments/{id}/report` | the report (409 while still running) |
| `DELETE` | `/assessments/{id}` | discard |
| `GET` | `/banks` | the catalogue, proxied so a frontend has one base URL |

Gated behind `AUTHOR_DIAGNOSTICS_ENABLED`, and **refused rather than thinned** when off:

| method | path | returns |
|---|---|---|
| `GET` | `/assessments/{id}/diagnostics` | posteriors, criterion, per-item information, queue reasoning, graph state |
| `GET` | `/assessments/{id}/state` | the raw `AssessmentState` |

## The candidate boundary

Assessment responses carry no answer key, no hidden test and no grading detail. Two
different reasons: a client that could read an answer could harvest the bank, and feedback
after each answer changes what the assessment measures — a candidate told which hidden test
failed learns something between question four and question five, and the estimate that
comes out is of a person who was taught mid-measurement.

One thing is NOT withheld: that the sandbox fell over. Their answer moved nothing and they
may be asked again.

**Fixed on the way through.** The monolith filtered those flags on the prefix
`INFRASTRUCTURE_`, which nothing in the engine has ever emitted — the code path emits
`SANDBOX_UNAVAILABLE:` and the voice path `PACKAGE_INFRASTRUCTURE_ERROR`. So the receipt's
`flags` list was empty on every response, including the ones it existed for.

## One orchestrator per (bank, VERSION)

Not per bank. A bank replaced through `PUT /banks/{id}` gets a new version, and a session
already running keeps the pool it began with — items disappearing from a queue that already
ranked them is not a change any candidate consented to.

## Sessions are in memory

Exactly what the monolith did, with the same retention and capacity rules. An assessment in
progress is never evicted; only finished sessions are reclaimed.

The consequence is stated rather than hidden: **sessions are bound to one replica**. Run
one, or put sticky sessions in front of several, until there is a store. `AssessmentState`
is serialisable by construction — the 41-point posterior is a `list[float]` specifically so
it round-trips — so the seam exists. What does not exist is a decision about where
candidate response data lives and under whose retention policy, and inventing one inside a
refactor is the wrong way to make it.

## It needs model egress, and ADR-0001 said it would not

The Picking Agent calls a model on every queue fill. A deployment that wrote its network
policy from `docs/microservices.md`'s "the grader is the only component needing egress"
would find selection silently falling back to the deterministic choice on every question —
a legitimate degraded mode, and therefore one that produces no error and no alert.

## Checklist

- [x] Move the loop, the posterior, selection and stopping
- [x] Move the session store, its retention rules and the stale-answer guard
- [x] Point it at `bank-registry`, `grader` and `competency-graph` through `adaptive_clients`
- [x] Serve the author diagnostics the deleted tester UI used to read from engine internals
- [x] Contract tests: the service and `adaptive_contracts` agree on every DTO
- [x] Enforce the engine slice this service may import
- [x] Load: p90 24 ms per response against a 150 ms budget (`deploy/latency.py`)
