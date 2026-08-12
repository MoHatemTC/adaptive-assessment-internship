# `cat_engine/engine/services/`

The engines. Five of them, plus the orchestrator that runs them in one session.

"Services" here means the service layer in the ordinary sense — code that does something, as
opposed to `schemas/` which only describes. It predates the microservices and outlived them.

## Directories

| Directory | Responsibility |
|---|---|
| [`orchestrator/`](orchestrator/README.md) | The loop. Which competency to probe, which item, when to stop, and the bank store everything reads from. |
| [`adaptive/`](adaptive/README.md) | The MCQ engine: 3PL IRT, EAP on a grid, KL/Fisher selection, convergence. |
| [`code_adaptive/`](code_adaptive/README.md) | The code engine: sandboxed execution, static analysis, model diagnosis, and the scoring that combines them. **The only thing here that executes anything.** |
| [`voice/`](voice/README.md) | Open and spoken answers: rubric evaluation, evidence projection, quote matching. |
| [`voice_live/`](voice_live/README.md) | Realtime interview rooms and their transport. Produces transcripts, never scores. |
| [`competency_graph/`](competency_graph/README.md) | The graph layer: propagation, coverage, policy. Changes what is asked and when to stop; never what is estimated. |

## Files

| File | Responsibility |
|---|---|
| `litellm_http.py` | The shared LiteLLM HTTP clients. One place, so SSL verification and timeouts are configured once. |
| `observability.py` | Langfuse tracing, as an option that can never take the engine down — absent, unconfigured or broken, it degrades to no tracing. |

## The three things that are never delegated

A model picks an item from a shortlist the engine ranked, and interprets code. It does not
grade multiple choice, does not write an estimate, does not choose which competency to probe,
and **cannot stop an assessment**. Every number in a report is computed by deterministic code
from inputs recorded beside it.
