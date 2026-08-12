# `.../services/orchestrator/`

The loop, and everything it needs to run one multi-modality assessment.

## What happens per response

```
ensure_presenting   the queue holds one candidate item per OPEN competency
       ↓
next_item           the least-certain open competency's item is presented
       ↓
record_response     grade → rollup to the main → one fractional posterior update
       ↓
propagate           the graph records what this response evidenced   (never an estimate)
       ↓
after_response      refill the queue for whatever the update opened or closed
       ↓
should_stop         per competency, independently
```

`Orchestrator` takes an `AssessmentState` and returns a new one. It holds nothing — no
session dict, no cache keyed on a candidate — which is why an assessment can be persisted
between calls and resumed elsewhere, and why the same object served a monolith, seven
services and a module without changing.

## The two decisions this component owns

**Which competency to probe next**, and **when one is finished**. That is all. Which item
measures that competency belongs to the Picking Agent; how a response is graded belongs to
the grader; what a response implies about neighbouring nodes belongs to the graph. Keeping
those apart is what lets a modality be added without touching the loop.

## Files

| File | Responsibility |
|---|---|
| `orchestrator.py` | The controller. The loop above, and the only place the four seams are composed. |
| `queue.py` | Exactly one pending candidate per open variable. Finalised competencies are skipped entirely — no pick, no model call. |
| `picker.py` | The Picking Agent: choose the next item for ONE variable across modalities, from a shortlist the engine already ranked. Falls back to the engine's top pick when the model is unavailable. |
| `variables.py` | The live estimate — one latent ability per variable — and the fractional update that consumes a `GradedOutcome`. |
| `outcome.py` | `GradedOutcome(variable, score, weight, confidence)`: what every modality reduces to, and the one update that consumes it. |
| `grader.py` | Routes a response to the grader its modality requires. Voice arrives **pre-evaluated**, because evaluation is a model call and this path is synchronous. |
| `competency.py` | Main vs sub-competency: the rollup boundary. `combined_weight` is a probabilistic union `1 - Π(1 - wᵢ)`, so two partial measurements of one response can never outweigh one whole one. |
| `bank.py` | The multi-modality bank behind a `UnifiedBankRepository` seam — the Protocol that let a bank be a file, a database or an HTTP service without the loop noticing. |
| `bank_store.py` | Banks are data. Two layers — checked-in seeds and a writable directory — plus the validation every bank clears, seeded or uploaded. |
| `registry.py` | Which bank, and which graph goes with it. A profile is the unit: a mismatched pair marks every required node unmeasured and vetoes convergence for a whole session. |
| `calibration.py` | Placing every modality's items on one ability scale, so "the least-measured competency" is a well-defined question. |
| `selection_calibration.py` | Measured replacements for the two constants that decide the modality mix — expected seconds and expected weight — derived from a session corpus rather than chosen. |
| `budget.py` | Does the configured session actually fit in the configured time? Answered before a candidate starts, not discovered at minute 88. |
| `propagation_port.py` | The one call the loop makes into the graph, behind a seam. `InProcessPropagation` is the implementation. |
| `graph_delta.py` | Everything one response did to the graph, held until all of it succeeded. A graph failure is caught and the delta discarded, so it costs the candidate nothing. |
| `session_dump.py` | Append a finished assessment to a JSONL corpus — what an appeal is adjudicated from, and what `selection_calibration` is derived from. |
| `prompts.py` | Everything this layer ever tells a model. |
| `__init__.py` | The package surface: the orchestrator, the registry and `session_dump`. |

## Why the update is fractional

```
L(theta) = [ P(theta)^s · (1 - P(theta))^(1-s) ] ^ w
```

At `s ∈ {0,1}, w = 1` this **is** the Bernoulli likelihood term for term, so the MCQ path is
unchanged by construction rather than by inspection — asserted to float equality against
`irt.posterior_update`. At `w = 0` it is identically 1, so an infrastructure failure moves
nothing: the rule that an outage is not evidence about a candidate falls out of the
arithmetic instead of being special-cased at every call site.
