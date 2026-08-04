# Microservice migration

**Status: boilerplate.** Nothing has moved. `services/` contains four running FastAPI apps
that answer `/health` and `/config`, a shared contracts package, and a per-service
`MIGRATION.md`. The monolith in `backend/` is the working system.

The point of writing the seams down before moving code is that a seam chosen during a move
is chosen under pressure.

## Boundaries

Each follows a seam the engine already has — a `Protocol` or a single package — rather than
a decomposition invented for the diagram.

| Service | Owns | Port |
|---|---|---|
| `bank-registry` | items, banks, graphs, propagation policy | 8081 |
| `grader` | one response → `GradedOutcome[]`, per modality | 8082 |
| `competency-graph` | node state, propagation, coverage | 8083 |
| `assessment-orchestrator` | the loop, the posterior, selection, stopping | 8080 |

`contracts/` is a package, not a service.

## Why this decomposes at all

**Only a directly observed response may move a posterior.** `InferredSignalDTO` has no
`score` and no `weight` field, so `competency-graph` cannot hand the orchestrator something
it could mistake for evidence. The boundary is enforced by the contract rather than by a
reviewer noticing.

The worst a compromised or buggy graph service can do is change which question is asked
next. That is what makes it safe to run it with its own store, its own release cadence, and
a lower trust level than the service that computes the estimate.

Two more properties carry over from the monolith:

- **The orchestrator is stateless between calls.** It takes a state and returns one. So it
  scales horizontally and an assessment can resume on a different worker.
- **The grader is the only component needing egress** — sandbox and model. Splitting it out
  is most of the security argument: everything else can run with no network policy at all.

## What is deliberately not split

- **Posterior update and stopping rule stay together.** They read the same state on every
  response; splitting them buys a network hop per question and no isolation.
- **Item parameters stay with the bank, cached in the orchestrator.** Selection needs `a`,
  `b`, `c` for every candidate item on every step. A fetch per decision puts the network in
  the hot loop. The orchestrator holds a read-through cache keyed by bank version.
- **Voice live sessions stay in the orchestrator's process for now.** They are stateful
  websocket bridges; moving them is a second project.

## Order of migration

Least-coupled first, so each step is revertible.

1. **`bank-registry`.** Read-only, no session state, already behind `UnifiedBankRepository`.
   Serves items and the resolved propagation policy. The orchestrator's cache means an
   outage degrades to stale parameters rather than to no assessment.
2. **`grader`.** Already a router over three independent graders and the only component
   with external dependencies. The contract is `GradedResponseDTO`, which the monolith
   already produces.
3. **`competency-graph`.** Owns session-scoped state, so it needs a store and idempotency
   on the evidence ledger — the ledger already exists and is already keyed on a
   deterministic evidence id, so replay is safe by construction.
4. **`assessment-orchestrator`.** What is left.

At each step the monolith path stays behind a flag until the client has run in shadow.

## Contract versioning

`adaptive_contracts.SCHEMA_VERSION` is reported by every service on `/health`. Bump it when
a field is **removed** or its meaning changes; additive fields do not bump it. A mismatched
pair is then visible in a dashboard rather than in a decoding error three hops away.

## The budgets a split has to respect

The 90-minute cap is the binding constraint, and P90 already sits at 83 minutes with the
coverage requirement on. Per candidate response:

| step | budget | note |
|---|---|---|
| grade | 500 ms (MCQ) / 30 s (code) | code is sandbox-bound; the cap already accounts for it |
| graph update | 50 ms | in-memory today |
| posterior update | 5 ms | 41-point vector |
| selection | 100 ms | ranks the whole eligible pool |
| **added by the split** | **< 150 ms** | four hops, same cluster |

If the split costs more than ~150 ms per response it has eaten a question from a
twelve-question budget. Measure it before step 4, not after.

## Running the boilerplate

```bash
cd deploy
cp .env.example .env
docker compose up --build
curl localhost:8080/health
```

Every route other than `/health` and `/config` returns **501** with a pointer to its
`MIGRATION.md`.
