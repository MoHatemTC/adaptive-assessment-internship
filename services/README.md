# Microservice boilerplate

**Status: boilerplate.** No business logic has moved. Every service here is a running
FastAPI app with health, config and its slice of the contract — and a `MIGRATION.md`
naming the modules that will fill it. The point is that the seams are chosen and written
down before the code moves, because a seam chosen during a move is chosen under pressure.

The monolith in `backend/` remains the working system and stays that way until each
service's migration checklist is ticked.

## Service boundaries, and why these four

The boundaries follow the seams the engine already has, not a decomposition invented for
the diagram. Each one is a `Protocol` or a single module today:

| Service | Owns | Existing seam |
|---|---|---|
| `bank-registry` | items, banks, competency graphs, propagation policy | `UnifiedBankRepository`, `orchestrator.registry` |
| `grader` | one response → `GradedOutcome[]`, per modality | `GraderAgent`, `code_adaptive`, `voice` |
| `competency-graph` | node state, propagation, coverage | `services.competency_graph` |
| `assessment-orchestrator` | the loop, the posterior, selection, stopping | `Orchestrator`, `adaptive` |

`contracts/` is a shared package, not a service: the envelope types every service speaks.

## The rule that makes this decomposable at all

**Only a directly observed response may move a posterior.** The graph produces
`InferredNodeSignal`, which carries no score and no weight, so it cannot become a
`GradedOutcome` even by accident. That is why `competency-graph` can be a separate service
with its own store without the orchestrator having to trust it with measurement: the worst
a compromised or buggy graph service can do is change which question is asked next.

## What is deliberately NOT split

- **The posterior update and stopping rule stay together** in the orchestrator. They read
  the same state on every response and splitting them buys a network hop per question.
- **Item parameters stay with the bank.** Selection needs `a`, `b`, `c` for every candidate
  item on every step; fetching them per-decision would put a round trip in the hot loop.
  The orchestrator holds a read-through cache keyed by bank version.

## Running it

    cd deploy && docker compose up --build

Each service answers `GET /health` and `GET /config`. Nothing calls anything yet.

Note: the compose file is asserted for consistency with the code — every declared port is
checked against it by the test suite — but it has not been executed. No container here has
been built or run.

## Testing it

    cd services && python -m pytest      # 67 tests: the seam and the contracts

**Run separately from `backend/`, deliberately.** `backend/pytest.ini` sets `LITELLM_*` and
`E2B_*` placeholders that the monolith's `Settings` requires at import, and the services
share no configuration with it. One rootdir would mean either the services inherit the
monolith's environment — hiding exactly the coupling this suite exists to prove is absent —
or the monolith fails to import.

What it covers, given that no route does anything yet: the contract envelopes (hardest,
since they are the only code whose meaning survives the migration), that all four services
report the same contract version, that `/config` leaks no credential, that the catch-all
does not shadow `/health`, that declared ports match the compose file, and that no service
imports another or the monolith.

The route-ordering tests are the ones that earn their keep. `/{path:path}` matches
everything including `/health`, and the operator endpoints survive only because they are
registered first — a property of line ordering whose symptom, when broken, is a liveness
probe returning 501 during an incident.

## Why these boundaries

[ADR-0001](../docs/adr/0001-service-boundaries.md) — the seams, what was deliberately not
split, the alternatives rejected, and the posterior-isolation rule that makes the
decomposition safe. Start there if you are reviewing this.
