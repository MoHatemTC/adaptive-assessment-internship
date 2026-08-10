# The services

**Status: migrated.** Five services over one engine library. The monolith is gone.

```bash
cd deploy && docker compose up --build     # all five
./scripts/run_services.sh                  # the same, without Docker
cd services && python -m pytest            # the seams, the contracts, each service
```

## What is here

| directory | what it is |
|---|---|
| `bank-registry/` | banks, items, graphs, propagation policy — **and the write path** |
| `grader/` | one response → `GradedOutcome[]`. The only service that runs a sandbox |
| `competency-graph/` | propagation and coverage. **No egress, no state** |
| `assessment-orchestrator/` | the loop, the posterior, sessions, the candidate boundary |
| `live-voice/` | realtime rooms and the interview page |
| `contracts/` | `adaptive_contracts` — wire types. Depends on pydantic and nothing else |
| `common/` | `adaptive_service` — the operator surface every service shares |
| `clients/` | `adaptive_clients` — HTTP adapters over the engine's own seams |

Each service's `MIGRATION.md` records what it took and what it left behind.

## Three shared packages, and why they are three

`contracts` is deliberately anaemic — pydantic only — so a frontend's generated client can
install it without numpy, a sandbox client and 600 KB of question banks.

`common` holds `/health` and `/config`. ADR-0001 recorded five duplicated copies of the
`/config` redaction filter as acceptable "while it is four lines", and noted it belonged
somewhere shared "the moment it grows a rule". It grew one: the engine config fingerprint.

`clients` is the adapter layer. `adaptive_clients.engine` is the only module in it that
imports the engine, and the package root does not import that module.

## The engine is a library, not a copy per service

One implementation of the psychometrics. Which PART of it a service may import is not a
packaging question — `test_services.py` parses every import in every service against a
declared allowlist. Widening one is a deliberate act with a failing test attached.

That test has already earned its keep three times during this migration: it caught the
grader reaching into `voice_live` (correct, and now narrowed to the one transcription
module), the orchestrator importing a package constructor from beside the rubric grader
(wrong — the constructor moved onto its type), and `competency-graph` needing the
propagation transaction (correct, and named as a module rather than the whole orchestrator).

## The rule that makes this decomposable

**Only a directly observed response may move a posterior.** `InferredSignalDTO` has no
`score` and no `weight`, so `competency-graph` cannot hand the orchestrator something it
could mistake for evidence. The worst a compromised or buggy graph service can do is change
which question is asked next.

Asserted against both the wire type and the engine type — the failure mode is not somebody
adding a score to the DTO, it is somebody adding one to the engine type and then widening
the DTO to match.

## What the suite covers

`cd services && python -m pytest`

| area | what is asserted |
|---|---|
| Contracts | bounds on every field; the inferred signal cannot carry evidence; `BankItemRef` cannot carry a payload; weight 0 is legal and means "no evidence" |
| Health | all five report the same contract version AND the same engine fingerprint |
| Config | no declared setting is a credential; the redaction filter's reach is pinned |
| Route ordering | the operator endpoints are never shadowed — on the route table and on behaviour |
| Deployment | declared ports are unique and match the compose file; every service has a Dockerfile |
| Boundaries | no service imports another; each imports only its declared engine slice |
| bank-registry | two read paths; a refused bank says why; a POST cannot overwrite; deleting a shadow restores the seed |
| grader | the answer key never leaves; weight 0 for an outage against 0.25 for a failed compile; no hidden case reaches a trial run |
| competency-graph | **the service and the in-process path produce identical state, for every node in a bank** |
| assessment-orchestrator | a whole assessment across four services; the candidate boundary; capacity fails closed; two answers in flight leave exactly one recorded |
| Documentation | every route has a summary and a tag; the committed OpenAPI matches the code |

## Why these boundaries

[ADR-0001](../docs/adr/0001-service-boundaries.md) chose the seams.
[ADR-0002](../docs/adr/0002-engine-as-a-library.md) records what changed when the code
actually moved — including four places where ADR-0001 turned out to be wrong, and why each
mattered.
