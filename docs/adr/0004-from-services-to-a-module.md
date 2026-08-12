# ADR-0004: from seven services back to one module

**Status:** accepted
**Supersedes the topology of:** [ADR-0001](0001-service-boundaries.md)
**Preserves:** [ADR-0002](0002-engine-as-a-library.md), [ADR-0003](0003-uploaded-banks-and-scoped-assessments.md)

## Context

The engine was split into seven services because a decomposition was wanted: independent
scaling, a grader that was the only component with a sandbox, a graph service with no egress
at all. ADR-0001 recorded those seams and ADR-0002 recorded what changed when the code
actually moved — the engine became one installed library rather than four copies.

The requirement has changed. This is to be **a module a sibling project imports**, not a
stack a sibling project deploys. Nothing about the measurement ever required a network: the
split was a deployment choice over an engine that is stateless between calls.

## Decision

Collapse the seven services into `cat_engine/`, a single Python package with no web
framework. The host owns transport.

## Why it was cheap, and why that is not a coincidence

Every hop was already behind a Protocol the engine defined, and an in-process implementation
of each already existed:

| seam | HTTP implementation | in-process implementation |
|---|---|---|
| bank repository | `HttpUnifiedBank` | `registry.get_bank()` |
| grader | `HttpGrader` | `GraderAgent` |
| propagation | `CompetencyGraphClient` | `InProcessPropagation` |
| graph structure | `HttpGraphSource` | `registry.get_graph_service()` |
| scope | `CompetencyScopeClient` | `build_scope()` — pure |

`services/assessment-orchestrator/service/engine.py` built an `Orchestrator` out of four HTTP
clients. `cat_engine/wiring.py` builds one out of four local objects. Neither touched the
loop, the posterior, selection or stopping — which is the same fact ADR-0002 recorded in the
other direction, and the reason both moves were wiring changes rather than rewrites.

## What was given up

**Egress by construction.** `competency-graph` could not reach a model because its image had
no credentials and its network policy allowed nothing. In one process that is no longer a
deployment fact.

**Import isolation by packaging.** The sandbox client was installed into one image, so no
other component *could* execute candidate code. Also gone.

Both are now `tests/test_module_boundaries.py`, which asserts that the parts deciding what to
ask and what to show stay one import from `code_adaptive`, and that no module file imports a
web framework. A review property with a failing test attached is weaker than a packaging
boundary and stronger than a convention. This is the real cost of the decision and it is
recorded rather than argued away.

**Independent scaling and independent failure.** A host scales the whole module or none of
it. For a library that is the expected arrangement.

## What was kept

- **The narrow waist.** `GradedOutcome` is still the only thing the measurement layer knows
  about, which is why a modality can be added without touching the loop.
- **The candidate boundary**, now carried by return types rather than by which service
  answered. `BankItemRef` has no payload field; the ranking path cannot read a question.
- **`InferredSignalDTO` carries no score and no weight.** The graph changes what is asked and
  when the test may stop. It never changes what is estimated.
- **The contracts package**, still independent of the engine. Re-exporting engine types would
  delete the omission that is the boundary.
- **Every error code.** A frontend written against the HTTP API branches on the same strings.

## What replaced the parity test

`test_parity_inprocess_vs_services.py` ran one seeded assessment through the engine and
through the services and asserted the reports were identical. It was the evidence the split
changed no measurement.

`test_parity_facade_vs_engine.py` does the same job for the merge: one seeded assessment
through `AssessmentModule` and through a raw `Orchestrator`, asserted identical. It runs over
two banks, because `DA` administers only mcq and code under every seed tried — so a run over
`DA` alone would never evaluate a rubric, and the rubric ordering is the one thing this
change had to get right. Verified by mutation: skipping `evaluate_voice` fails the `AIE`
cases and passes every `DA` one.

## The one thing the collapse had to get right

`GraderAgent._grade_open` requires an already-evaluated `GradedVoiceResponse`, because
evaluation is a model call and `Orchestrator.record_response` is synchronous. The grader
service hid that: it awaited the evaluation itself before grading, so the orchestrator handed
over a raw package and got outcomes back.

In one process the await has to happen at the call site instead. Everything else in this
change was mechanical; this was not, and it is why the parity test was extended to a bank
that actually administers spoken items.

## Consequences

- One measurement policy per process — but only that. `CatConfig` is applied onto a
  module-level singleton, and a second module that disagrees about MEASUREMENT raises
  rather than silently rescoring the first; two modules differ freely on topology, which is
  the case a host actually hits.
- The latency budget the split had to respect is moot; there are no hops.
- 15,551 lines removed: seven `main.py`, seven Dockerfiles, a compose file, an HTTP client
  layer, a FastAPI runtime package, and the tests that asserted on ports and specs.
- `docs/microservices.md` becomes [`docs/module.md`](../module.md), the embedding contract.
