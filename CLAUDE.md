# CLAUDE.md — working with the adaptive assessment engine

This repo is an adaptive (CAT) assessment engine. Two packages, one rule for which to
touch:

- **`adaptive_engine/`** — THE PUBLIC API. A stateless, typed boundary for host
  backends: authoring (`derive_graph` / `validate_content` / `build_assessment` /
  `revise_assessment`) and runtime (`compile_assessment` / `start_assessment` /
  `advance_assessment`). Pure functions; the caller persists definitions and states.
- **`cat_engine/`** — the measurement engine underneath, plus the DEPRECATED legacy
  stateful surface (`AssessmentModule`, session stores, registry, wiring). The
  `adaptive_engine` wrapper delegates every measurement decision to
  `cat_engine.engine`; the legacy lifecycle is kept only until the host migrates.

## Integrating this engine into a host backend

Read [docs/integration-guide.md](docs/integration-guide.md) first — it is the complete
contract (schemas, findings, error codes, state rules). The short version:

```python
from adaptive_engine import authoring, compile_assessment, start_assessment, advance_assessment

definition = authoring.build_assessment("my-assessment", "v1", items)   # persist it
compiled   = compile_assessment(definition)                              # cache by (id, version)
decision   = start_assessment(compiled, initial_competencies={...})      # persist decision.state
decision   = advance_assessment(compiled, state, response)               # …after every answer
```

Non-negotiable rules when integrating:

1. **Import only from `adaptive_engine`.** Never from `cat_engine.facade`, `.stores`,
   `.wiring`, or `.registry` — those are the deprecated stateful path, scheduled for
   deletion (a test pins that the stateless path never loads them).
2. **Only `decision.question` reaches a candidate.** It is the one item shape that
   structurally cannot carry answer keys. Never render any other item model, and never
   reveal correctness mid-run — feedback changes what the assessment measures.
3. **Persist `decision.state` after every call; treat it as opaque JSON.** If a call
   raises, nothing was recorded — keep the old state and retry.
4. **Changed content ⇒ new version.** States are pinned to a content hash;
   `revise_assessment` enforces this at authoring time.
5. **The engine does no I/O.** MCQ grades internally; code/open/voice answers arrive as
   host-graded `GradedAnswer` (or `{competency_id: GradedAnswer}`) — grade in YOUR
   infrastructure, pass results in. Infrastructure failure ⇒ don't call advance (or
   `weight=0`); never `score=0`.
6. **One measurement policy per process** (engine limitation, fails loudly with
   `ConfigConflict`). Leave `policy=None` for shipped defaults, or isolate per worker.
7. **Serialize per run.** No locks inside; a racing duplicate answer is rejected as
   `stale_response` with nothing recorded.

A complete reference host lives in [take_assessment.py](take_assessment.py) — CLI, state
persisted to disk between questions, resume-on-rerun, plus a monitoring panel showing
the engine's per-answer updates.

## Working on this repo

- **Env**: `uv sync` then `uv pip install -r requirements.txt` (pytest-env is required —
  pytest.ini's env block supplies placeholder credentials so no test can bill anything).
- **Tests**: `uv run python -m pytest` (whole repo; `adaptive_engine/tests` is the
  wrapper's contract, `cat_engine/tests` pins the engine). Postgres-gated files skip
  without a DSN. Everything must stay green.
- **Lint**: `uv run ruff check .` must pass. Line length 100; config in pyproject.toml.
- **Style**: this codebase documents *why*, heavily — docstrings state the reasoning and
  the measurements behind decisions. Match that; don't strip it.

Invariants you must not break (tests pin most of them):

- Measurement math lives in `cat_engine/engine` ONLY. `adaptive_engine` must never
  re-implement selection, convergence, grading rollup, or graph logic — it delegates.
  (A previous from-scratch copy was reviewed and rejected; see
  [docs/adaptive-engine-design.md](docs/adaptive-engine-design.md).)
- `advance_assessment` never mutates its input state; same inputs ⇒ same decision.
- Budget stops are never reported as convergence — `converged` means a measurement
  criterion was met, nothing else.
- Graph evidence never moves a posterior; only directly observed responses do.
- Derived prerequisite edges ship inert (`validation_status: "unvalidated"`); enabling
  one requires real-session evidence, not code.
- Public inputs are typed models (`BankItem`, `CompetencyGraph`, `CompetencyDeclaration`,
  …) — no `dict[str, Any]` on new boundary functions; raw dicts coerce via pydantic.
- `cat_engine` seam changes must be backward-compatible (defaults preserve behavior;
  the ~1,250 legacy tests prove it).

Current status and phase plan: [docs/adaptive-engine-design.md](docs/adaptive-engine-design.md).
Pending phase 2: thread `policy=` through the stop rules (removes the one-policy-per-
process limit), optional `now=` clock injection. Phase 3: delete the legacy stateful
surface once the host migrates.
