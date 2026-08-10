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

Stateless between calls. The orchestrator takes an `AssessmentState`, returns a new one,
and holds nothing — so an assessment can span HTTP requests, be persisted between them, and
resume on a different worker. That property is what made the split in
[microservices.md](microservices.md) tractable, and it is why `competency-graph` holds no
session state either: a second copy of the same session's node state would disagree with
the first the moment a request is retried.

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
backend/app/                         THE ENGINE, installed as a library (`adaptive-engine`)
  config/settings.py                 all engine policy, one place
  schemas/                           typed boundaries
  services/adaptive/                 3PL core, convergence, MCQ engine
  services/code_adaptive/            code execution, scoring, evidence
  services/voice/                    rubric grading, evidence projection
  services/voice_live/               realtime rooms and their transport
  services/competency_graph/         graph, propagation, policy, coverage
  services/orchestrator/             the loop, selection, the bank store, reporting
  data/                              the checked-in banks and their graphs
backend/evaluation/                  the simulation harness. In-process, never a service
services/                            five adapters over the library, plus three shared packages
docs/api.md                          the contract a frontend builds against
```

The engine is one library rather than a copy per service, because the 3PL core, the
fractional likelihood and the stopping rule are where two implementations drifting apart is
a measurement problem rather than a maintenance one. Which part of it each service may
import is enforced by a test — see [microservices.md](microservices.md).
