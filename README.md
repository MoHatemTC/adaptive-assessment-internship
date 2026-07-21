# Adaptive Competency Assessment — code-question engine

Computerised adaptive testing for **coding** competency assessment. Candidate code is
executed against real tests in a sandbox, structure is read from its syntax tree, and an
LLM interprets what the evidence means — but never decides whether the code worked.

Each submission narrows an explicit belief about a competency, and the next question is the
one that will narrow it most. A test ends when the estimate is precise enough rather than
after a fixed number of questions.

```
backend/
  app/
    config/settings.py               configuration, incl. every policy knob
    schemas/code_adaptive.py         typed boundary: Question, SessionState, reports
    services/code_adaptive/
      irt.py                         Beta posterior, Fisher & KL information, bands
      competency.py                  learner model and the only permitted update
      execution.py                   E2B sandbox — untrusted code runs only here
      static_analysis.py             structural signals read from the syntax tree
      scoring.py                     the source-weight seam, guards, integrity cap
      weights.py                     WeightProfile — the tunable logic/LLM split
      evidence.py                    criterion -> competency projection
      llm.py                         LiteLLM client with transport retry
      llm_evaluator.py               model diagnosis + reply validation
      prompts.py                     system prompts and rubric rendering
      selection.py                   filter, rank, shortlist, constrained pick, stopping
      bank.py                        QuestionRepository seam + JSON implementation
      session.py                     CodeAdaptiveSession — the public entry point
    data/code_bank.json              25 questions, 218 tests
    data/rubrics/                    loose | mid | tight grading instructions
  tests/                             measurement invariants, scoring guards, session flow
```

## Who decides what

This split was chosen by measurement, not preference. The alternatives were built and
scored against 150 seeded submissions carrying known, verified defects.

| Decision | Owner | Why |
|---|---|---|
| Did it compile, which tests passed | **code** | Execution in a sandbox. A fact, not a judgement. |
| Structural signals (complexity, hard-coding, guards) | **code** | The syntax tree. Deterministic and free. |
| Functional correctness score | **code** | The passed-test ratio, and nothing else. |
| Code quality, algorithm appropriateness | **LLM** | No objective ground truth exists. This is what a model is actually for. |
| Competency estimate | **code** | Bayes on a Beta posterior. The model cannot write one. |
| Stopping | **code** | A psychometric threshold, not a judgement call. |
| Which questions are viable, and their rank | **code** | KL early, expected Fisher later. |
| **Which viable question to ask** | **LLM** | From a code-computed shortlist, with the engine's own choice recorded beside it. |

**The model cannot damage the measurement.** It may not score functional correctness above
what execution showed — attempts are clamped and flagged. It may only pick a question the
engine already ranked near-optimal. Any failure (an invented id, a malformed reply, an
unreachable gateway) falls back to the engine's choice, because there is always a correct
next question and an infrastructure problem must not end a candidate's assessment.

## Why approach B

Three splits were built and measured. On 150 seeded submissions, 67 of which admit a
separation verdict:

| | separation | mean margin | misconception recall | cost / submission |
|---|---:|---:|---:|---:|
| **A** objective only | 47/67 (70.1%) | +0.110 | **0.22** | 0 tokens, 1.0 s |
| **B** test-anchored *(shipped)* | **49/67 (73.1%)** | **+0.114** | **0.92** | 7,269 tokens, 49 s |
| C model-led | not re-run | — | — | ~7,000 tokens, 51 s |

Paired bootstrap, B over A: **+0.018 [+0.002, +0.034]** — real, but small. The decisive
difference is not the score, it is the explanation: **recall 0.22 → 0.92**. You are paying
for the diagnosis, not the number. C was excluded on requirement rather than performance —
it places functional correctness at only 50% tests, so its score is not anchored to whether
the code ran.

Anchoring violations across every arm and rubric: **0**.

## The scoring split, and how to change it

Each criterion is scored from up to three sources. Approach B:

| criterion | weight in overall | tests | static | LLM |
|---|---:|---:|---:|---:|
| functional_correctness | 40% | **1.00** | — | — |
| edge_case_handling | 25% | 0.80 | 0.05 | 0.15 |
| algorithm_choice | 20% | — | 0.46 | 0.54 |
| code_quality | 15% | — | 0.30 | 0.70 |

**Effective authority: tests 60.0% · static 15.0% · LLM 25.0%.**

Effective, not declared. Two corrections separate the two, and both once misreported this
exact quantity. A source that cannot produce a value for a criterion is dropped and the
rest renormalised — execution cannot judge algorithm choice, so the 0.35 that table once
assigned it silently became the model's. And criteria are not equally weighted: they carry
`maximum_score` from the bank.

Set `CODE_LLM_SHARES` to move the split per deployment. Whatever is in force is
fingerprinted onto every score, because a score of 0.62 is uninterpretable without knowing
whether the model held 15% or 70% of the criterion.

## Guards

Each one exists because it failed once, in a way that reached a score.

- **Anchoring** — the model may not score functional correctness above the passed-test
  ratio. Clamped and flagged, never silently dropped.
- **NOT_ASSESSED** — a criterion no source could score is `None`, never 0.0 and never full
  credit. Absence of evidence is neither a pass nor a fail.
- **Integrity cap** — hard-coded output caps at 0.25, an empty body at 0.0. A weighted
  average cannot express *"this finding invalidates the rest"*. Execution evidence is never
  rewritten: the tests really did pass; what is capped is the inference to competency.
  Predicates are exempt — a branching `is_something()` returns literals by definition.
- **Evidence strength** — an infrastructure failure carries strength 0 and is skipped
  entirely, so our fault never becomes a candidate's inability. A syntax error carries 0.25:
  a typo is not the absence of every competency the question touches.
- **Stop-rule calibration** — `stop_rule_calibration()` reports `TRIVIAL`, `BINDING` or
  `UNREACHABLE`. Both failure modes have occurred: a target above the prior means
  `min_questions` decides every session, and one below the reachable floor means the rule
  never fires.

## Usage

```python
from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository

session = CodeAdaptiveSession(JsonQuestionRepository())
state = session.begin("T1.4", self_rating=3)

while True:
    selected = session.next_question(state)
    if selected is None:                       # bank exhausted
        break

    # present the question, collect the candidate's code
    state, result, stop = session.record_submission(state, selected.question_id, code)
    if stop.should_stop:
        break

report = session.summarise(state, stop)
```

`SessionState` is serialisable and the engine holds nothing between calls, so an assessment
can span HTTP requests and resume on a different worker.

## Configuration

See `backend/.env.example`. `E2B_API_KEY` is required — without it no submission can run.

| setting | default | meaning |
|---|---|---|
| `CODE_APPROACH` | `B` | scoring split preset |
| `CODE_RUBRIC` | `mid` | grading instructions given to the model |
| `TARGET_STANDARD_ERROR` | `0.15` | precision to stop at |
| `MIN_QUESTIONS` / `MAX_QUESTIONS` | `5` / `12` | session length bounds |
| `MINIMUM_RELATIVE_UTILITY` | `0.75` | below this the model's pick is overridden |
| `MINIMUM_LLM_CONFIDENCE` | `0.70` | below this its weight is halved |
| `CODE_LLM_SHARES` | unset | per-deployment override of the split |

## Tests

```bash
cd backend && PYTHONPATH=. pytest
```

62 tests, no network and no sandbox: measurement invariants, the scoring guards above, and
session flow with both boundaries stubbed — including a sandbox failure, which must move no
estimate at all.
