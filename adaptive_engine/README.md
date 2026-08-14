# `adaptive_engine` — the stateless boundary around the proven assessment engine

A small, pure Python API for running an adaptive assessment. The engine is a state
transition and nothing more:

```text
compiled assessment + previous adaptive state + optional response
                           |
             new adaptive state + decision
```

**This package makes no measurement decisions of its own.** Every one — per-competency
grading rollup, person-fit checks, competency-graph propagation, coverage and modality
gates, the shipped stopping rules, the full report — is made by `cat_engine`'s existing,
battle-tested `Orchestrator`, which was already written in the stateless style (state in,
new state out, nothing held). What this layer adds is the boundary a host backend needs:

- **Caller-supplied content.** The definition carries bank items and the competency
  graph in the engine's own wire shapes — exactly what the authoring branch produces —
  validated by the engine's own validators. No bank store, no registry, no files.
- **One JSON-safe state blob** the host persists and sends back, carrying the engine's
  complete runtime state plus the exposure-control RNG, pinned to the assessment id,
  version, **and a content hash** so edited content behind an unchanged version string is
  detected instead of silently continued.
- **Strict transition checking**: stale, duplicate and out-of-order responses are
  rejected; a finished run accepts nothing; a state replayed against the wrong assessment
  raises. Every error carries a stable machine-readable `code`.
- **Host-side grading input.** MCQ is graded deterministically inside. For code / open /
  voice items the host runs its own grading and hands in a validated result — one grade,
  or **one grade per measured competency** (`{"python.errors": GradedAnswer(...)}`), the
  same per-competency evidence the internal graders produce.
- **No I/O.** The optional LLM question-pick is disabled (measured 353/353 agreement
  with the deterministic choice); nothing in a run touches the network, a database, or a
  session store.

## The complete lifecycle

```python
from adaptive_engine import (
    AssessmentDefinition, GradedAnswer, InitialCompetency, QuestionResponse,
    advance_assessment, compile_assessment, start_assessment,
)

definition = AssessmentDefinition(
    assessment_id="python-backend",
    version="v1",
    items=[  # BankItem wire shape — as the authoring branch emits it
        {
            "item_id": "py-1",
            "modality": "mcq",
            "measures": [{"variable": "python", "weight": 1.0}],
            "cat": {"a": 1.6, "b": -0.5, "c": 0.2},
            "mcq": {
                "stem": "What does a list comprehension return?",
                "options": ["A generator", "A list", "A tuple"],
                "answer_index": 1,          # grading data: never presented
            },
        },
        # ... more items: mcq, code, open, voice
    ],
    graph={...},        # competency-graph JSON, or None
    targets=None,       # None = every main the items measure
)

compiled = compile_assessment(definition)   # validate + wire the engine; cache by (id, version)

decision = start_assessment(
    compiled,
    initial_competencies={"python": InitialCompetency(level=3, confidence=0.8)},
)
save_state(decision.state)                  # the host persists the state

while decision.status == "question":
    item = decision.question.item           # candidate-safe: no answer keys, by type
    if item.modality == "mcq":
        answer = collect_option_index(item)
    else:
        answer = my_grader.grade(item, collect_submission(item))  # -> GradedAnswer(s)
    decision = advance_assessment(
        compiled,
        state=load_state(),
        response=QuestionResponse(question_id=item.item_id, answer=answer),
    )
    save_state(decision.state)

show_report(decision.report)                # the engine's full report DTO
```

Every call returns an `AdaptiveDecision`, and its invariants are enforced by the model:

| `status`       | meaning                          | `question` | `report` | `converged` |
|----------------|----------------------------------|------------|----------|-------------|
| `"question"`   | present `decision.question`      | set        | `None`   | `False`     |
| `"completed"`  | every competency converged       | `None`     | set      | `True`      |
| `"terminated"` | stopped without full convergence | `None`     | set      | `False`     |

`stop_reason` speaks the engine's vocabulary (`all_variables_finalised`, `item_budget`,
`time_limit`, `no_candidates_available`), and the report carries a per-competency
`stop_reason` and `decision_status` — a competency that was never asked about reads
`not_assessed`, never a level.

## Ownership boundaries

The host owns: users, authentication, application sessions, assignments, question and
assessment persistence, versioning, adaptive-state persistence, retention, databases,
HTTP, and external grading infrastructure. This layer owns validation, compilation, and
the stateless transition; `cat_engine.engine` owns the measurement.

## Known limitation (deliberate, phase 1)

The measurement policy (`MeasurementPolicy`) is routed through the engine's config
layer, which enforces **one measurement policy per process**: a second, different policy
raises `ConfigConflict` loudly rather than silently re-tuning estimates. Run
differently-tuned assessments in separate processes until the policy is threaded
per-call through the engine's stopping rules.

Timing fields inside the state (`presenting_since`, `elapsed_minutes`) read the real
clock — a time budget should — so byte-identical replay holds for everything except the
time-limit rule.

## Files

```text
adaptive_engine/
  __init__.py     the public surface
  models.py       AssessmentDefinition + MeasurementPolicy (content in engine wire shapes)
  compilation.py  compile_assessment: engine validators + InMemoryBank + Orchestrator wiring
  runtime.py      start/advance, response validation, host-graded evidence, decisions
  state.py        AdaptiveState: engine state + RNG + content-hash pinning, JSON-safe
  errors.py       the typed error hierarchy with stable codes
  tests/          the behavioral contract, one file per concern
```

One `cat_engine` seam was added for this layer (backward-compatible):
`Orchestrator.record_response(..., graded=)` accepts a pre-graded response so external
grading can live in the host while everything downstream runs unchanged.
