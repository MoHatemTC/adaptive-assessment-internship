# competency-graph — migration record

Node state, propagation, coverage. Never a posterior.

**Status: migrated.** Routes are real; there is no stub left.

## Owns

- upward inference and descendant blocking
- contradiction detection
- the evidence ledger transaction
- the coverage requirement
- the propagation manifest

## Stateless — a deliberate departure from the original plan

The plan was `POST /sessions/{id}/evidence`, with this service owning per-session node
state. It does not. The property the whole architecture rests on is that the orchestrator
holds nothing between calls, so an assessment can be persisted and resumed on a different
worker — and that only works while `AssessmentState` is the single source of truth. A graph
service keeping its own copy of the same session's node state creates a second one, and the
two disagree the first time a request is retried.

So a caller sends what it knows and gets back what changed. Nothing is stored, the service
scales horizontally for free, and a restart loses nothing.

## Surface

| method | path | returns |
|---|---|---|
| `POST` | `/propagate` | one response's outcomes → the delta, all or nothing |
| `GET` | `/manifest/{bank_id}` | the propagation configuration in force, and its hash |
| `POST` | `/coverage` | which required sub-competencies still lack direct evidence |

`/report` was planned and is not here: the orchestrator assembles the graph report from
session state it already holds, and a second construction of it would drift from the first.

## What this service cannot do

It cannot move a candidate's estimate. `InferredSignalDTO` has no `score` and no `weight`
field — structural rather than stylistic, because a deduction FROM a response is not a
second response, and multiplying it back in counts one answer twice. The damage lands on
the standard error, which is what the assessment stops on; it was measured at 1.80x weight
inflation on the specification's own worked example.

The worst a compromised or buggy graph service can do is change which question is asked
next. That is a quality-of-service problem rather than a correctness one, and it is the
reason this decomposition is safe where a naive one would not be.

## What did NOT move

Graph **traversal** — which sub-competencies a main requires, which nodes are blocked, what
would unblock one. Selection reads it on every candidate on every step, inside a 100 ms
budget, so it stays local against a cached graph structure. The graph is immutable for a
bank version, so a remote read of it is a cache fill rather than a boundary.

Only **propagation** crosses the wire: once per answered question, next to a grading call
that already costs between 500 ms and 30 s.

## Failing open is correct

If this service is unreachable the response is still graded, still folded into the
posterior, and the session continues with the graph layer off for that response. Propagation
ships INERT on this deployment already — a completed screening study measured its
wrong-inference rate at 7-17% against a 3% gate — so an outage that abandoned a session
would be a far worse failure than one that degrades to the ungated engine.

## Checklist

- [x] Serve propagation, coverage and the manifest
- [x] Fetch graphs from `bank-registry` rather than reading a local copy
- [x] Contract tests: the service and `adaptive_contracts` agree on every DTO
- [x] Prove the service and the in-process path produce identical state updates, for every
      node in a bank
- [x] Enforce the engine slice this service may import
- [x] Point the orchestrator at it through `adaptive_clients`
- [x] Load: p90 24 ms per response against a 150 ms budget (`deploy/latency.py`)
