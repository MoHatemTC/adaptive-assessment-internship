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
