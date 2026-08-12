# `.../services/orchestrator/`

The loop, and everything it needs to run one multi-modality assessment.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The package surface: the orchestrator, the registry and `session_dump`. |
| `orchestrator.py` | The controller. Takes an `AssessmentState` and returns a new one, holding nothing — which is why an assessment can span requests and resume elsewhere. |
| `queue.py` | Exactly one pending candidate per open variable. Finalised competencies are skipped entirely: no pick, no model call. |
| `picker.py` | The Picking Agent: choose the next item for ONE variable, across modalities, from a shortlist the engine already ranked. |
| `variables.py` | The live estimate — one latent ability per variable, and the fractional update that consumes a `GradedOutcome`. |
| `outcome.py` | What every modality reduces to. `GradedOutcome(variable, score, weight, confidence)` is the narrow waist of the whole design. |
| `grader.py` | The Grader Agent: route a response to the grader its modality requires. Voice arrives pre-evaluated, because evaluation is async and this path is not. |
| `competency.py` | Main vs sub-competency: the rollup boundary. `combined_weight` is a probabilistic union, so two partial measurements can never outweigh one whole one. |
| `bank.py` | The multi-modality question bank, behind a seam — the Protocol that let a bank be a file, a database or an HTTP service without the loop noticing. |
| `bank_store.py` | Banks are data. Two layers — checked-in seeds and a writable directory — plus the validation every bank clears, seeded or uploaded. |
| `registry.py` | Which question bank, and which competency graph goes with it. A profile is the unit, because a mismatched pair vetoes convergence for a whole session. |
| `calibration.py` | Placing every modality's items on one ability scale, so "the least-measured competency" is a well-defined question. |
| `selection_calibration.py` | Measured replacements for the two constants that decide the modality mix, derived rather than chosen. |
| `budget.py` | Does the configured session actually fit in the configured time? |
| `propagation_port.py` | The one call the orchestrator makes into the competency graph, behind a seam. `InProcessPropagation` is the implementation. |
| `graph_delta.py` | Everything one response did to the graph, held until all of it succeeded. A graph failure costs the candidate nothing. |
| `session_dump.py` | Append a finished assessment to a JSONL corpus — what an appeal is adjudicated from. |
| `prompts.py` | Everything the orchestration layer ever tells a model. |

## The property the whole arrangement rests on

**Stateless between calls.** The orchestrator holds nothing, so who calls it — a monolith, a
service, a module — has never changed what it computes.
