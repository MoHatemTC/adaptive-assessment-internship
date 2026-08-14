# `adaptive_engine` — the stateless adaptive assessment runtime

A small, pure Python API for running an adaptive assessment. The engine is a state
transition and nothing more:

```text
assessment definition + previous adaptive state + optional response
                           |
             new adaptive state + decision
```

It holds **no sessions, no users, no questions, no caches** — the host backend owns
persistence, identity, assignments and transport. The caller persists the returned
`AdaptiveState` (it is JSON-compatible) and sends it back on the next call; two processes
holding the same state make the same decision.

Dependencies: `pydantic` and `numpy`. No web framework, no database, no LLM client.

## The complete lifecycle

```python
from adaptive_engine import (
    AssessmentDefinition, AssessmentGraph, Competency, GraphEdge,
    InitialCompetency, Measurement, Question, QuestionResponse,
    advance_assessment, compile_assessment, start_assessment,
)

definition = AssessmentDefinition(
    assessment_id="python-backend",
    version="v1",
    competencies=[
        Competency(competency_id="python", title="Python"),
        Competency(competency_id="databases", title="Databases"),
    ],
    # source is a prerequisite of target. The graph steers selection and shapes the
    # report; it never manufactures evidence.
    graph=AssessmentGraph(edges=[GraphEdge(source="python", target="databases")]),
    questions=[
        Question(
            question_id="py-1",
            prompt="What does a list comprehension return?",
            options=["A generator", "A list", "A tuple"],
            answer_index=1,                                # grading data: never presented
            measures=[Measurement(competency_id="python")],
            difficulty=-0.5,                               # b, on the ability scale
            discrimination=1.5,                            # a
        ),
        # ... more questions, spread across difficulties and competencies
    ],
)

compiled = compile_assessment(definition)   # validates everything, builds indexes

decision = start_assessment(
    compiled,
    initial_competencies={
        # Seeds the prior only — never counted as observed evidence.
        "python": InitialCompetency(level=3, confidence=0.8),
    },
)
save_state(decision.state)                  # the host persists the state

while decision.status == "question":
    response = QuestionResponse(
        question_id=decision.question.question_id,   # always named, never inferred
        answer=get_answer(decision.question),        # option index for mcq
    )
    decision = advance_assessment(compiled, state=load_state(), response=response)
    save_state(decision.state)

show_report(decision.report)
```

Every call returns an `AdaptiveDecision` with the same shape, and its invariants are
enforced by the model itself:

| `status`       | meaning                                   | `question` | `report` | `converged` |
|----------------|-------------------------------------------|------------|----------|-------------|
| `"question"`   | present `decision.question` next          | set        | `None`   | `False`     |
| `"completed"`  | every competency converged                | `None`     | set      | `True`      |
| `"terminated"` | stopped without full convergence          | `None`     | set      | `False`     |

`decision.stop_reason` names why a run ended: `all_competencies_converged`,
`question_budget`, or `questions_exhausted`. Convergence and forced termination are never
one ambiguous flag.

## Ownership boundaries

The engine owns: definition and graph validation, compilation, belief initialization,
competency and question selection, deterministic (mcq) grading, evidence application,
convergence, and the final report.

The host owns: users, authentication, application sessions, assessment assignments,
question/assessment persistence and versioning, adaptive-state persistence, retention,
databases, HTTP, and external grading. For modalities the host grades itself (code, free
text, voice), declare the question `kind="externally_graded"` and pass a validated
`GradedAnswer(score=..., confidence=...)` as the response answer — it flows through the
same evidence path as every mcq.

## Determinism and concurrency

The same compiled assessment + state + response always produces the same decision.
Randomized exposure control draws purely from the seed stored in the state, so replay is
exact anywhere. The engine holds no locks; it makes invalid transitions detectable
instead — a response for anything but the currently presented question raises
`StaleResponse`, a finished run raises `AssessmentFinished`, and a state replayed against
the wrong assessment or version raises `StateMismatch`. Every error carries a stable
machine-readable `code`.

## Files

```text
adaptive_engine/
  __init__.py     the public surface — everything a host imports is named here
  models.py       AssessmentDefinition and its parts: the one definition model
  compilation.py  compile_assessment — the one validation path, plus derived indexes
  runtime.py      start_assessment / advance_assessment / AdaptiveDecision
  state.py        AdaptiveState — the JSON-compatible memory the host persists
  evidence.py     grading and the one evidence-application path
  selection.py    the one question-selection path (KL early, expected Fisher after)
  convergence.py  the one convergence evaluator, and every named stop reason
  graph.py        runtime graph behavior: strong-evidence classification, blocking
  report.py       the final report, derived entirely from the state
  irt.py          the 3PL measurement core, ported intact from the proven engine
  errors.py       the typed error hierarchy with stable codes
  tests/          the behavioral contract, one file per concern
```

The measurement mathematics (grid posterior, EAP, KL/Fisher item information,
band reporting) is ported unchanged from the validated `cat_engine` implementation; what
changed is the boundary around it.
