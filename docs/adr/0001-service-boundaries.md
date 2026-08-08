# ADR-0001 — Service boundaries for the adaptive assessment engine

**Status:** accepted, unimplemented. The seams are chosen and tested; no business logic has
moved.
**Date:** 2026-08-08
**Supersedes:** nothing. **Superseded by:** nothing.

---

## Context

The engine is a working monolith in `backend/`: a computerised adaptive test that measures a
candidate against a competency graph using items of four modalities — multiple choice, code,
open text and voice — all calibrated onto one latent ability scale.

It is being prepared for extraction into services. This ADR records **which seams were
chosen, which were deliberately left alone, and the one invariant that makes the
decomposition safe at all** — before any code moves, because a seam chosen during a move is
chosen under pressure.

The reader this is written for is a reviewer who has not seen the monolith.

---

## Decision

Four services and one shared package, along seams the engine already has. Every boundary
below is a `Protocol` or a single module today, not a decomposition invented for the
diagram.

| Service | Owns | Existing seam | Port |
|---|---|---|---:|
| `assessment-orchestrator` | the loop, the posterior, selection, stopping | `Orchestrator`, `services.adaptive` | 8080 |
| `bank-registry` | items, banks, competency graphs, propagation policy | `UnifiedBankRepository`, `orchestrator.registry` | 8081 |
| `grader` | one response → `GradedOutcome[]`, per modality | `GraderAgent`, `code_adaptive`, `voice` | 8082 |
| `competency-graph` | node state, propagation, coverage | `services.competency_graph` | 8083 |

`contracts/` is a **shared package, not a service** — the envelope types every service
speaks. It is a package precisely so that no service has to call another to understand a
message.

---

## The invariant that makes this decomposable

> **Only a directly observed response may move a posterior.**

The competency graph deduces things: if a candidate demonstrated a dependent skill, its
prerequisites are probably held. Those deductions shape **which question is asked next** and
**what the report says**. They never enter the measurement.

This is enforced by the type system, not by convention. `InferredSignalDTO` — what the graph
service returns — **has no `score` field and no `weight` field**, so it cannot be handed to
the posterior update even by accident:

```python
class InferredSignalDTO(BaseModel):
    node: str
    source_node: str          # provenance, so an inferred claim is contestable
    distance: int
    strength: float           # for ranking and reporting. NOT a weight.
    source_evidence_id: str
    modality: Modality
```

**Why it is structural rather than a flag.** Propagated evidence is a deduction *from* a
response that is already in the likelihood. Multiplying it in again counts one answer twice
— measured at 1.80× weight inflation on the specification's own worked example. The damage
is not mainly to the ability estimate; it is to the standard error, which is what the
assessment stops on. A flag would leave four uncalibrated constants wired into a path that
terminates at the stopping rule. Removing the path removes the failure mode.

**What this buys the architecture.** `competency-graph` can run as a separate service, with
its own store, without the orchestrator having to trust it with measurement. The worst a
compromised or buggy graph service can do is change which question is asked next. That is a
quality-of-service problem, not a correctness one — and it is the reason this decomposition
is safe where a naive one would not be.

Tested in `services/tests/test_contracts.py::TestInferredSignalCannotCarryEvidence`,
including that a payload claiming `score` on the wire cannot smuggle it through decoding.

---

## The second boundary: parameters are not payloads

`BankItemRef` carries what **selection** needs — `a`, `b`, `c`, which variables the item
measures, estimated time. It carries **no stem, no options, no answer**.

That split lets `bank-registry` serve two callers with different authorisation: one that
ranks items without being able to read a question, and one that renders them. Collapsing it
would silently make one caller's authorisation both callers'.

Tested by asserting the forbidden field names are absent from the model, rather than by
inspection — the assertion survives a refactor that inspection would not.

---

## What is deliberately NOT split

Recorded because the omissions are decisions, and an unrecorded decision reads to the next
person as an oversight.

**The posterior update and the stopping rule stay together**, in the orchestrator. They read
the same state on every response. Splitting them buys a network hop per question and a
distributed-consistency problem on the one piece of state that must be exactly right.

**Item parameters stay with the bank, cached in the orchestrator.** Selection needs `a`, `b`,
`c` for every candidate item on every step. Fetching per-decision puts a round trip in the
hot loop. The orchestrator holds a read-through cache keyed by bank version.

**Grading stays one service across four modalities**, rather than one service per modality.
They share the output contract (`GradedOutcomeDTO`) and differ only in how they produce it;
splitting by modality would give four services with one contract and no independent
lifecycle.

**The evaluation harness (`backend/evaluation/`) is not a service and never will be.** It
imports the engine directly and drives it in-process — that is what makes 36,000 simulated
sessions affordable. It is a test instrument, not a component.

---

## Status: what exists today

Every service is a running FastAPI app with:

- `GET /health` — liveness, release, and **the contract version the build speaks**
- `GET /config` — effective configuration, credentials excluded
- everything else — `501`, naming the service and its `MIGRATION.md`

A 501 that says what it is and where the plan lives beats a 404, and beats a stub that
returns plausible data. **The monolith in `backend/` remains the working system** until each
migration checklist is ticked.

The contract version on `/health` exists so a mismatched pair is visible in a dashboard
rather than as a decoding error three hops away. The test suite asserts all four services
report the *same* version, since a version that is never compared is decoration.

### What the service tests cover

67 tests, run separately from the engine suite (`cd services && python -m pytest`).

| Area | What is asserted |
|---|---|
| Contracts | Bounds on every field; `InferredSignalDTO` cannot carry evidence; `BankItemRef` cannot carry a payload; weight 0 is legal and means "no evidence" |
| Health | All four report the same contract version; each identifies itself by the name a dashboard would key on |
| Config | No declared setting is a credential; the redaction filter's actual reach is pinned |
| Stub honesty | 501 on every declared method; the `MIGRATION.md` it points at exists |
| **Route ordering** | `/health` and `/config` are not shadowed by the catch-all — asserted on the route table *and* on behaviour |
| Deployment | Declared ports are unique and match `docker-compose.yml`; every service has a Dockerfile |
| Independence | No service imports another, or the monolith |

The route-ordering tests are the ones that earn their keep. `/{path:path}` matches
everything including `/health`; FastAPI resolves in registration order, so the operator
endpoints survive only because they are declared first. That is a property of line ordering
in a file — the kind of thing a later edit breaks silently, whose symptom is a liveness
probe returning 501 during an incident. Verified by deliberately reordering the routes and
confirming the suite fails with a message naming the cause.

---

## Consequences

**Good.** The seams are reviewable before any code moves. The measurement boundary is
enforced by types rather than by review attention. Each service has its own `Settings`, so
one cannot read another's configuration — a service that can will eventually depend on it.

**Costs, accepted.**

- Four services all name their package `app`. Correct per-container; it means they cannot be
  imported into one interpreter, so the test suite loads them in isolation. Documented in
  `services/tests/conftest.py` rather than worked around silently.
- The `/config` redaction filter tests the **field name**, not the value. `api_key`,
  `jwt_secret`, `refresh_token` and `db_password` are caught; a credential embedded in a
  `database_url` would not be. No service declares such a field today, which is why this is
  a recorded gap and not a defect — pinned by a named test so closing it has an obvious home.
- The same redaction expression is duplicated in all four services. Acceptable while it is
  four lines; it belongs in `contracts/` the moment it grows a rule.
- The orchestrator's item-parameter cache introduces a staleness window bounded by bank
  version. Not yet specified.

**Open.** Nothing here is validated under load, and no container has been built or run. The
compose file is asserted for consistency with the code, not executed.

---

## Alternatives considered

**Split by modality** (an mcq service, a code service, a voice service). Rejected: they share
one output contract and differ only in production, so this yields four services with no
independent lifecycle and a fan-out on every response.

**Keep the graph inside the orchestrator.** Rejected on the evidence: propagation is the part
of this engine most likely to change and most likely to be wrong — a completed screening
study measured its wrong-inference rate at 7–17% against a 3% gate, and it now ships off. The
component you expect to disable independently is the one to isolate.

**One service per bank.** Rejected: banks are data, not behaviour. `bank-registry` is
multi-tenant by bank id, and the propagation policy is resolved per bank inside it.

---

## References

- `services/README.md` — the same boundaries, from the implementer's side
- `services/*/MIGRATION.md` — per-service checklist of the modules that will move
- `docs/preregistration/S1_Screening_Report.md` — why propagation ships off, which is why
  `competency-graph` is a separately disableable service
- `docs/preregistration/test_report.html` — test coverage, findings and blockers
