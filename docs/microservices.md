# The services

**Status: migrated.** Five services, three shared packages, and an engine that is a library
rather than a monolith. `backend/app/main.py` and the Streamlit UI are gone.

Read [ADR-0001](adr/0001-service-boundaries.md) for why these are the seams, then
[ADR-0002](adr/0002-engine-as-a-library.md) for what changed when the code actually moved.
[api.md](api.md) is the contract a frontend builds against.

## What runs

| service | port | owns | egress |
|---|---|---|---|
| `assessment-orchestrator` | 8080 | the loop, the posterior, selection, stopping, sessions | model |
| `bank-registry` | 8081 | banks, items, graphs, propagation policy, the write path | none |
| `grader` | 8082 | one response → `GradedOutcome[]`, per modality | model, sandbox |
| `competency-graph` | 8083 | propagation, coverage, the manifest | **none** |
| `live-voice` | 8765 | realtime rooms, the interview page | model |

```bash
cd deploy && cp .env.example .env && docker compose up --build
curl localhost:8080/health
```

Or, without Docker, `./scripts/run_services.sh`.

## The engine is a library

One implementation of the psychometrics, installed into every image as `adaptive-engine`,
imported under the name `app`. Services are adapters over the slice they own, and which
slice that is is enforced by `services/tests/test_services.py`, which parses every import
against a declared list.

That matters more than it sounds. The 3PL core, the fractional likelihood and the stopping
rule are where two implementations drifting apart is a measurement problem rather than a
maintenance one — two candidates scored by different arithmetic, with nothing comparing
them.

`backend/evaluation/` imports the same library in-process. It is a test instrument, not a
component, and driving 36,000 simulated sessions through HTTP is not affordable.

## Why this decomposes at all

**Only a directly observed response may move a posterior.** `InferredSignalDTO` has no
`score` and no `weight` field, so `competency-graph` cannot hand the orchestrator something
it could mistake for evidence. Enforced by the contract, and asserted against both the wire
type and the engine type — the failure mode is not somebody adding a score to the DTO, it
is somebody adding one to the engine type and widening the DTO to match.

The worst a compromised or buggy graph service can do is change which question is asked
next.

Two more properties carry over:

- **The orchestrator is stateless between calls.** It takes a state and returns one. The
  service holds the states in memory; see the caveat below.
- **The grader is the only component that executes anything.** Untrusted candidate code
  runs in E2B, not in any container here.

## What crosses the wire, and what does not

| | where | why |
|---|---|---|
| item parameters | cached in the orchestrator, per bank version | selection ranks the whole pool on every step; a fetch per decision puts the network in a 100 ms loop |
| item payloads | fetched per presented item | large, and selection is not allowed to read them |
| grading | grader, which fetches its own item | the answer key never transits the orchestrator |
| graph traversal | local, per bank version | read on every candidate on every step |
| graph propagation | competency-graph | once per answered question, beside a 500 ms-30 s grading call |

## Configuration must agree, and the fingerprint says whether it does

Engine settings decide what a candidate is SCORED by. Split across services nothing makes
them agree: a grader running `CODE_APPROACH=C` beside an orchestrator that believes it is
`B` produces a session whose scores were computed one way and whose stopping rule assumed
another — internally consistent, entirely wrong, no request failing.

`deploy/docker-compose.yml` declares them once, and every service reports
`engine_config_fingerprint` on `/health`. Two services with different fingerprints are not
running the same assessment.

## Contract versioning

`adaptive_contracts.SCHEMA_VERSION` is reported by every service on `/health`. Bump it when
a field is **removed** or its meaning changes; additive fields do not. The suite asserts all
five report the same one — a version nobody compares is decoration.

## The budgets a split has to respect

The 90-minute cap is the binding constraint and P90 already sits at 83 minutes with the
coverage requirement on. Per candidate response:

| step | budget | note |
|---|---|---|
| grade | 500 ms (MCQ) / 30 s (code) | sandbox-bound; the cap already accounts for it |
| graph propagation | 50 ms + one hop | |
| posterior update | 5 ms | 41-point vector |
| selection | 100 ms | ranks the whole eligible pool, entirely local |
| **added by the split** | **< 150 ms** | the budget |

**Measured: p90 24 ms, p50 20 ms**, over 30 MCQ responses across 6 sessions on one machine
(`deploy/latency.py`). That is a WHOLE response — the grader hop, which itself fetches the
item from the registry, the propagation hop, MCQ grading and the selection refill. In the
monolith all of it was function calls, so the split's share is bounded above by 24 ms
against a budget of 150.

Re-measure on the target cluster before believing it there; a same-host Docker network is
the optimistic case.

## Known limits

- **Sessions are bound to one replica.** The orchestrator holds them in memory, exactly as
  the monolith did. Run one, or use sticky sessions. `AssessmentState` is serialisable by
  construction, so the seam for a store exists; what does not exist is a decision about
  where candidate response data lives and under whose retention policy.
- **No authentication anywhere**, including a bank write path that can replace the bank a
  live assessment is running against. Carried forward from the monolith rather than
  introduced here — see `docs/operations.md`. `ADMIN_API_ENABLED=false` disables writes.
- Load is measured on one machine only. A real cluster adds real network.
