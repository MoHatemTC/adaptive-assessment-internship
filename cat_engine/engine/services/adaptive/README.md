# `.../services/adaptive/`

The MCQ engine. 3PL item response theory, EAP on a fixed grid.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The package surface: the session, the repositories and the IRT primitives a caller needs. |
| `irt.py` | The measurement core: the 3PL curve, the likelihood, and the posterior update on a 41-point grid over [-4, 4]. |
| `selection.py` | Choosing the next item — KL early, expected Fisher information once there is evidence. |
| `convergence.py` | When to stop asking, and how confident the result is. Standard error, stability window, band probability. |
| `session.py` | The public entry point: run one competency, question by question. |
| `bank.py` | Where items come from, and whether the bank can support a test at all. |
| `personfit.py` | Was this response surprising, given what we already believed? |
| `rephrase.py` | Guard for model-rewritten question stems — a rewrite that changes the answer is not a rewrite. |
| `llm.py` | Async JSON-returning LLM calls via the LiteLLM proxy. |
| `prompts.py` | System prompts. |

## Why the grid

A posterior over a fixed 41-point grid is exactly reproducible, cheap to update, and
serialisable as a `list[float]`. That last property is what lets a session be persisted and
resumed — the measurement core and the storage decision turn out to be the same decision.
