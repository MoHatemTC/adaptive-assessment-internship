# ADR-0002 — The engine is a library; the services are adapters over it

**Status:** accepted, implemented.
**Date:** 2026-08-10
**Supersedes:** nothing. **Amends:** [ADR-0001](0001-service-boundaries.md) in four places,
each marked below.

---

## Context

ADR-0001 chose the seams and left the code in `backend/`. This ADR records what happened
when the code actually moved, which is the part that could not be decided in advance.

Two things in ADR-0001 turned out to be in direct conflict:

> **`services/*/MIGRATION.md`:** "Move the modules above, keeping their tests, into `app/`
> … Delete the monolith copy and the flag."

> **ADR-0001:116-118:** "The evaluation harness is not a service and never will be. It
> imports the engine directly and drives it in-process — that is what makes 36,000
> simulated sessions affordable."

Both cannot hold. Every service names its package `app`, so four `app` packages cannot be
imported into one interpreter — and the harness that must import the engine in-process
would have had nothing left to import. The same is true of the 700-test engine suite.

---

## Decision

**One engine, installed as a library. Several services, each an adapter over the slice it
owns.**

```
backend/                    adaptive-engine   the psychometrics, the harness, the tests
services/contracts/         adaptive-contracts  wire types — pydantic only
services/common/            adaptive-service    operator endpoints, error shape
services/clients/           adaptive-clients    HTTP adapters over the engine's own seams
services/{five}/service/    the adapters
```

The import name stays `app`, which is why **not one import statement** in the engine, the
harness, the scripts or the test suite changed. The services renamed **their** package to
`service` instead: there is one engine and several adapters, and the names should say
which is which. That also retires the accepted cost ADR-0001 recorded at :168-171.

### Why not vendor a copy per service

Because the psychometrics would then exist in five places. The 3PL core, the fractional
likelihood and the stopping rule are the parts of this system where two implementations
drifting apart is not a maintenance problem but a measurement one — two candidates scored
by different arithmetic, with nothing that compares them.

### Why the shared library does not make this a distributed monolith

The test that used to say "no service imports the monolith" said nothing once every service
installs the engine — and it was always the weaker claim. It is replaced by a per-service
**allowlist of engine module prefixes**, parsed out of every source file:

| service | may import |
|---|---|
| bank-registry | bank store, registry, bank, competency_graph, schemas, config |
| grader | code_adaptive, voice, grader, outcome, `voice_live.transcribe` |
| competency-graph | competency_graph, graph_delta, propagation_port |
| assessment-orchestrator | adaptive, orchestrator, competency_graph, observability |
| live-voice | `voice_live`, observability, config |

Widening one is a deliberate act with a failing test attached. `bank-registry` reaching
into the grader would put sandbox execution behind a read-only bank API, and nothing else
would notice.

---

## Amendments to ADR-0001

### 1. The orchestrator needs model egress

> ADR-0001 / `docs/microservices.md:40`: "the grader is the only component needing egress."

**It is not.** The Picking Agent (`orchestrator/picker.py`) calls a model on every queue
fill, to choose one item from a shortlist the engine has already ranked.

This matters because of how it fails. A network policy written from that sentence does not
break the orchestrator — selection falls back to the deterministic choice, which is a
legitimate degraded mode and therefore raises no error, logs nothing alarming and alerts
nobody. Every session would silently stop using the model it was designed around.

`competency-graph` remains the service with **no egress at all**, which is where the
security argument now lives.

### 2. `competency-graph` is stateless

> ADR-0001 / `MIGRATION.md`: `POST /sessions/{session_id}/evidence`, with the service
> owning per-session node state.

**It owns none.** The property the architecture rests on is that the orchestrator holds
nothing between calls, so an assessment can be persisted and resumed on a different worker.
That works only while `AssessmentState` is the single source of truth. A graph service
holding its own copy of the same session's node state creates a second one, and the two
disagree the first time a request is retried.

So the caller sends its graph slice and receives a delta. Nothing is stored, it scales
horizontally for free, and a restart loses nothing. `/report` was planned and is not
implemented: the orchestrator assembles the graph report from state it already holds, and a
second construction of it would drift from the first.

### 3. Only propagation crosses the wire; traversal does not

The graph is used two ways and only one can afford a hop.

- **Traversal** — what a competency requires, which nodes are blocked. Read during
  selection, on every candidate, on every step, inside a 100 ms budget. Local, against a
  structure cached per bank version.
- **Propagation** — one response, one transaction. Once per answered question, beside a
  grading call that already costs 500 ms to 30 s. That one is a service call.

The graph structure is immutable for a bank version, so fetching it is a cache fill rather
than a boundary crossing.

### 4. Live voice moved after all

ADR-0001 deferred it — "stateful websocket bridges … a second project". Dissolving
`backend/app/main.py` brought it forward, and it was a lift rather than a redesign, because
the room lifecycle never touched the assessment. A room holds audio, turns and a
transcript. It holds no posterior and cannot end an assessment.

---

## What else changed, and why

### Banks are content, not code

`registry.REGISTRY` was five profiles written into a Python file, so adding a bank meant a
redeploy. It is now a store with two layers: the checked-in banks as read-only seeds, and a
writable overlay another service posts into. Stored shadows seed on the same id; deleting
the shadow restores the seed, which makes an accidental overwrite recoverable without a
redeploy.

Every bank carries a **content hash**. The orchestrator caches item parameters against it
and each assessment pins the version it began under — without which replacing a bank
changes the item pool underneath a live candidate, with items vanishing from a queue that
already ranked them.

Validation on write is not new policy: it is the invariants `test_bank_registry.py` already
asserted of the five checked-in banks, enforced at the door. A bank that arrives over HTTP
has to clear the same bar, or that suite tests the seeds rather than the system.

### The candidate boundary is a server-side refusal

The instrumented view — posteriors, per-item information, the criterion phase, raw session
state — used to be a Streamlit flag. It is now two endpoints gated by
`AUTHOR_DIAGNOSTICS_ENABLED`, refused rather than thinned when off. A boundary enforced by
a client choosing not to render a field is not a boundary.

### One defect fixed on the way through

The candidate's grade receipt filtered flags on the prefix `INFRASTRUCTURE_`, which nothing
in the engine has ever emitted — the code path emits `SANDBOX_UNAVAILABLE:` and the voice
path `PACKAGE_INFRASTRUCTURE_ERROR`. The list was empty on every response, including the
ones it existed for: a candidate whose sandbox died was told nothing, their answer moved no
estimate, and they were re-asked with no explanation.

### Release evidence affected

`evaluation/invariants/test_c_shipped_invariants.py` imported two request models from
`app.main`. They live in `adaptive_contracts` now, and **INV-10 is stronger**: the
answer-level seed is gone rather than defaulted to `None`. The monolith kept it "for wire
compatibility" and logged a warning that it was ignored — a field a client can send, that
does nothing, and whose name says it controls exposure.

---

## Consequences

**Good.** One implementation of the psychometrics. Service boundaries enforced by a test
rather than by review attention. The evaluation harness untouched — it still drives the
engine in-process, which is what keeps 36,000 sessions affordable. Banks are deployable
content. A frontend has a documented, versioned API.

**Costs, accepted.**

- **Every image contains the whole engine**, not just the slice it may import. The
  allowlist governs what a service can *reach*, not what is on its disk. Trimming would
  mean splitting the engine into five distributions, and coupling five release cadences to
  buy image size is a bad trade.
- **Sessions are bound to one replica.** The orchestrator holds them in memory, exactly as
  the monolith did. Run one, or use sticky sessions, until there is a store. The seam
  exists — `AssessmentState` is serialisable by construction — but where candidate response
  data lives and under whose retention policy is not a decision to make inside a refactor.
- **No authentication anywhere**, including the bank write path, which can replace the bank
  a live assessment is running against. This is the monolith's posture carried forward, not
  something this migration introduced, and it is recorded here and in `docs/operations.md`
  rather than implied. `ADMIN_API_ENABLED=false` turns the write path off without turning
  off the service.
- **Engine settings must be identical across services** or a session is scored one way and
  stopped another. Every service reports a fingerprint of the measurement settings on
  `/health`; the compose file declares them once.
- **`adaptive_contracts` duplicates `app.schemas` permanently.** Deliberate: the wire
  contract is not the internal one — `BankItemRef` omits the payload `BankItem` carries,
  and that omission IS the security boundary of the bank surface. `test_contract_parity.py`
  is the price, and it is the only place both definitions are in scope at once.

**Open.** Load is unmeasured against the < 150 ms budget in `docs/microservices.md`.

---

## References

- [ADR-0001](0001-service-boundaries.md) — the seams, and the posterior-isolation rule
- `services/*/MIGRATION.md` — what each service actually took
- `docs/api.md` and `docs/api/openapi-*.json` — the contract a frontend builds against
