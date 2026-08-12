# `.../services/code_adaptive/`

The code engine. **The only thing in this repository that executes anything.**

Untrusted candidate code runs in E2B, never in this process. Which components may import
this package is asserted by `tests/test_module_boundaries.py` — it was a packaging boundary
when the grader was its own image, and one process cannot provide that.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The package surface: `CodeAdaptiveSession`, the repositories, and the types a grader needs. |
| `execution.py` | Run untrusted learner code in the sandbox and collect objective evidence. The E2B client is imported inside the function, so a host that never grades code never installs it. |
| `static_analysis.py` | Structural signals read from the submission's syntax tree. No execution. |
| `llm_evaluator.py` | Model competency diagnosis, with the validation that makes it safe to use — a reply that fails validation contributes nothing rather than contributing noise. |
| `scoring.py` | Combining evidence sources into criterion scores. Tests 60%, static analysis 15%, model 25% — the measured split. |
| `weights.py` | The logic/LLM split, as an adjustable and auditable object rather than constants scattered through the scorer. |
| `evidence.py` | Criterion scores into per-competency evidence. |
| `competency.py` | Learner competency state and the deterministic update. |
| `irt.py` | The measurement core on the mastery scale — a Beta posterior over [0, 1], not theta. |
| `selection.py` | Filter, rank, shortlist, constrained pick. |
| `session.py` | The public entry point: run one competency, submission by submission. |
| `trial.py` | Let a candidate run their solution before submitting it. **Grades nothing** — there is no code path from here into a learner model, not an unused one, none at all. |
| `bank.py` | The question bank, behind a seam. |
| `prompts.py` | System prompts and rubric rendering: everything the model is ever told. |
| `llm.py` | JSON-returning LLM calls via the LiteLLM proxy. |

## An infrastructure failure is not evidence about a candidate

A sandbox that falls over produces outcomes of **weight 0**, which make the likelihood
identically 1 and move no estimate. A zero score at full weight would record our outage as
their inability. A failure to *compile* is different — it is weak evidence, capped at a
quarter, because a syntax error shows a syntax problem rather than an absence of every
competency the question touches.
