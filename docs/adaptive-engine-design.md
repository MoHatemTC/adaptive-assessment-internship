# Design: `adaptive_engine` as a stateless boundary over the existing engine

Status: implemented (phase 1 + the `graded=` seam). 2026-08-14.
Authoring layer (`adaptive_engine.authoring`) added 2026-08-15 — see §Authoring below.

## Problem

A host backend owns users, sessions, persistence and transport, and wants to call the
assessment runtime as a pure library:

```text
compiled assessment + previous adaptive state + optional response
                           |
             new adaptive state + decision
```

The measurement logic already exists in `cat_engine` and is proven by ~1,250 tests — but
its public surface (`AssessmentModule`) is stateful: session stores, registries, config
singletons, wiring caches.

## Decision: wrap, don't copy

An earlier iteration re-implemented the runtime as a self-contained package. Review
found the copy dropped real measurement behavior (per-competency grading, zero-evidence
handling, coverage gates, the shipped stopping rules, not-assessed reporting, and more).
The copy was discarded in favor of reuse.

The reuse is possible because the stateful reputation of `cat_engine` is earned by the
machinery AROUND its engine, not the engine itself:

- `Orchestrator` (the loop) is stateless between calls: every method takes an
  `AssessmentState` and returns a new one.
- Its bank dependency is a four-method protocol (`all_items/get/shortlist/variables`) —
  satisfiable by an in-memory repository over caller-supplied items.
- The graph stack (`parse_and_validate_graph` → `resolve_policy` → `apply_policy` →
  `CompetencyGraphService`) is pure.
- `AssessmentState` (RNG state included) already round-trips JSON — the legacy SQL store
  persisted exactly that.

## Architecture

```text
host backend
    │  AssessmentDefinition (items + graph + policy)        persisted AdaptiveState
    ▼                                                            ▲     │
adaptive_engine (thin layer)                                     │     │
    compile_assessment ─► InMemoryBank + CompetencyGraphService ─┤     │
                          + GraderAgent(None) + InProcessPropagation   │
                          ─► Orchestrator (cat_engine, unchanged loop) │
    start/advance ─► begin / fill_queue / ensure_presenting /          │
                     record_response / after_response / should_stop /  │
                     summarise — with use_llm=False everywhere         │
    AdaptiveState = { assessment_id, version, content_hash,
                      engine_state: AssessmentState, rng_state }
```

The layer adds only boundary concerns: content-hash pinning (edited content behind an
unchanged version string is rejected), stale/duplicate/out-of-order response rejection,
typed errors with stable codes, host-graded evidence for non-mcq modalities (one
`GradedAnswer`, or one per measured competency), and a deep copy of the engine state at
entry so the caller's object is never mutated.

## Seams changed inside `cat_engine`

One, backward-compatible: `Orchestrator.record_response(..., graded: GradedResponse |
None = None)` — a caller that graded externally hands in the finished result; grading is
skipped, everything downstream (rollup, person-fit, propagation, gates) runs unchanged.

One private opt-out at construction: an assessment defined without a graph sets the
orchestrator's graph check so the deprecated registry fallback ("borrow the active
bank's graph") can never fire.

## Authoring (`adaptive_engine.authoring`)

The generation branch's first slice, built on the same reuse principle. Four pure
functions, nothing written — the host persists definitions as it persists states:

- `derive_graph(assessment_id, items, declaration=, relation_threshold=, edge_floor=)` —
  the general-purpose questions→graph operation, delegating to
  `cat_engine.ingest.derive.derive_graph` (item co-measurement; deterministic; derived
  prerequisite edges ship inert/unvalidated). The thresholds are explicit per-call
  arguments and are stamped into the graph under `derivation` — fixing the legacy
  hazard where they were ambient settings read at ingest time.
- `validate_content(assessment_id, items, graph, question_budget=, critical_only=)` —
  `BankStore.validate`'s findings (item validity, graph/bank pairing,
  `coverage_unreachable`, `required_node_unmeasured`, …) as a frozen report.
- `build_assessment(...)` — items (+ optional authored graph or declaration) → validated
  `AssessmentDefinition`; derives the graph when none is given, derives
  `coverage_critical_only` from graph content (the legacy rule), refuses invalid content
  naming every error finding, and compiles once as the final gate.
- `revise_assessment(definition, version=, add_items=, update_items=, retire_item_ids=,
  graph="rederive"|"keep"|dict)` — returns a NEW definition; requires a new version;
  retiring keeps the item in the definition (history stays whole; the engine never
  administers retired items).

Not wrapped, deliberately: the legacy upload store/receipt cache (host persistence),
`analyse_bank` (bank information floor) and the edge-validity Wilson checks in
`cat_engine/validation.py` — candidates for a later authoring slice if needed.

## Known limitations and the follow-up phases

1. **One measurement policy per process** (phase 2): stopping rules read the process
   settings singleton, and `CatConfig.apply()` deliberately refuses a second different
   policy (`ConfigConflict`). Fix: thread an optional `policy=` parameter through
   `convergence.evaluate`, `variables`, `picker` and the orchestrator, defaulting to
   settings — legacy behavior byte-identical, per-assessment policy for the layer.
2. **Wall clock in the loop**: `begin`/`ensure_presenting`/`record_response` read
   `time.time()`; the time-limit rule should consult a clock, but replay/testing would
   benefit from an optional `now=` parameter (phase 2, small).
3. **LLM modules are imported, never called**: `picker` imports the LLM client at module
   level; `use_llm=False` keeps the path dead. A lazy-import cleanup would restore the
   hard "never imported" guarantee (phase 3, cosmetic).
4. **Legacy surface retirement** (phase 3): once the host migrates to this layer,
   `facade`/`stores`/`wiring`/`registry` and the session paths are deleted; the layer
   already avoids importing any of them (pinned by `test_module_isolation`).
