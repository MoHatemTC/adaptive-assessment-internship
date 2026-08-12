# Architecture

## The loop

```
intake -> seed a posterior per competency
       -> fill the queue (one candidate item per open competency)
       -> present the least-certain competency's item
       -> grade the response
       -> fold graded outcomes into that competency's posterior
       -> check convergence
       -> repeat, or report
```

Stateless between calls. The orchestrator takes an `AssessmentState`, returns a new one, and
holds nothing — so an assessment can span requests, be persisted between them, and resume in
a different process. That property is what made the split in
[ADR-0001](adr/0001-service-boundaries.md) tractable, and what made undoing it in
[ADR-0004](adr/0004-from-services-to-a-module.md) equally cheap: both directions were a
change of who calls whom, not of what is computed.

It is also why propagation holds no session state of its own. A second copy of the same
session's node state would disagree with the first the moment a call was retried — which was
true when propagation was a service and is true now that it is a function.

## The three things that are never delegated

A model picks an item from a shortlist the engine ranked, and interprets code. It does not
grade multiple choice, does not write an estimate, does not choose which competency to
probe, and **cannot stop an assessment**. Every number in the report is computed by
deterministic code from inputs recorded beside it.

## Measurement

One latent ability (`theta`) per main competency, estimated by EAP on a fixed 41-point grid
over [-4, 4] under a 3PL model. Every modality lands on the same scale, which is what makes
"the least-measured competency" a well-defined question.

Partial credit uses a fractional likelihood:

```
L(theta) = [ P(theta)^s * (1 - P(theta))^(1-s) ] ^ w
```

At `s ∈ {0,1}` and `w = 1` this is the Bernoulli likelihood term for term, so the MCQ path
is unchanged by construction rather than by inspection. At `w = 0` it is identically 1, so
an infrastructure failure moves nothing — the rule falls out of the arithmetic instead of
being special-cased at each call site.

## Reporting

Five bands, 1.6 logits wide, from explicit cut points. Band width is not cosmetic: at the
SE target, P(the reported band is the true band) runs about 0.64 at width 1.0 and 0.85 at
1.6. **Report a band range, not a point band** — within-one-level accuracy is ~99% while
exact accuracy is ~65%. See [evidence.md](evidence.md).

## The competency graph

An optional layer that adds:

- **sub-competency node state** — direct, inferred, blocked, contradicted, with provenance
- **a coverage requirement** — a competency may not claim convergence until its required
  sub-competencies have direct evidence
- **shared nodes** — one authoritative state, read by every main competency it serves
- **prerequisite propagation** — upward inference and descendant blocking, **off by default**

### The rule the whole design rests on

**Only a directly observed response may move a posterior.**

`InferredNodeSignal` carries no `score` and no `weight`, so it cannot be turned into a
`GradedOutcome` even by accident. A deduction *from* a response is not a second response;
multiplying it back in counts one answer twice, and the damage lands on the standard error,
which is what the assessment stops on.

So the graph changes **what is asked** and **when the test may stop**. It never changes
**what is estimated**. That is a real limitation as well as a safety property — see
[evidence.md](evidence.md) on the 9.9pp cost of one theta per competency.

## Layout

```
cat_engine/
  facade.py                          AssessmentModule: the surface a host calls
  wiring.py                          an Orchestrator built from in-process parts
  contracts/                         the DTOs a host receives; independent of the engine
  catalogue.py  grading.py           reading banks; grading one response
  projection.py  diagnostics.py      engine objects to what a host receives; the author view
  scope/  ingest/  stores/  live/    scoping, the write path, persistence, interviews
  engine/                            THE ENGINE
    config/settings.py               all engine policy, one place
    schemas/                         typed boundaries
    services/adaptive/               3PL core, convergence, MCQ engine
    services/code_adaptive/          code execution, scoring, evidence
    services/voice/                  rubric grading, evidence projection
    services/voice_live/             realtime rooms and their transport
    services/competency_graph/       graph, propagation, policy, coverage
    services/orchestrator/           the loop, selection, the bank store, reporting
    data/                            the checked-in banks and their graphs
  validation.py                      is the shipped DATA sound? bank floor, edge validity
  scripts/                           one calibration script the engine names by path
docs/module.md                       the embedding contract
docs/api.md                          the surface, method by method
```

The engine is one implementation rather than a copy per caller, because the 3PL core, the
fractional likelihood and the stopping rule are where two implementations drifting apart is
a measurement problem rather than a maintenance one. That argument survived the engine being
a library behind seven services and it survives the services being gone — see
[ADR-0002](adr/0002-engine-as-a-library.md) and
[ADR-0004](adr/0004-from-services-to-a-module.md).

Which parts of it may reach the sandbox is enforced by `tests/test_module_boundaries.py`.
That was a packaging fact while the grader was its own image; in one process it is a test,
which is weaker and is recorded as the cost of the collapse rather than argued away.
