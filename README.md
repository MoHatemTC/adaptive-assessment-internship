# Adaptive Competency Assessment

An agent-driven CAT engine that measures a candidate across many competencies using
**multiple-choice, coding, and open/voice items in the same session**, choosing whichever
item — of whichever modality — will narrow the weakest estimate fastest, and finishing each
competency as soon as it is measured.

It is **one module a host project imports**. There is no web framework in it and no port:
the host owns transport, this owns the measurement. See [docs/module.md](cat_engine/docs/module.md) for
the embedding contract.

```python
from cat_engine import AssessmentModule, CatConfig

cat = AssessmentModule(CatConfig(active_bank="AIE"))

state = await cat.begin(intake={"C1": 3})
while state.presenting:
    state = await cat.answer(state.session_id, mcq=2)   # or code= / transcript= / audio=
report = state.report
```

```bash
pip install cat-engine                 # base: mcq, open, scoping, banks
pip install cat-engine[sandbox,live]   # grading code, and realtime interviews
```

Or copy `cat_engine/` into the host tree and import it — everything it needs is inside.

```text
cat_engine/
  facade.py                          AssessmentModule — the surface a host calls
  wiring.py                          builds an Orchestrator out of in-process parts
  config.py, settings.py             CatConfig; measurement policy vs topology
  errors.py                          CatError — every code the HTTP API used
  contracts/                         the DTOs a host receives; independent of engine/
  catalogue.py                       reading banks; two views, two types
  grading.py                         one response → GradedOutcome[], per modality
  scope/                             a competency selection → a sub-graph and an allowlist
  ingest/                            one uploaded file → a registered bank
  stores/                            sessions and banks, pluggable
  live/                              interview rooms, and the browser client for them
  engine/                            THE ENGINE
    config/settings.py               cat_* (MCQ), code_* (code), orchestrator_*, graph_*
    schemas/orchestration.py         BankItem, VariableState, AssessmentState, reports
    services/adaptive/               MCQ engine — 3PL, EAP, KL/Fisher, convergence
    services/code_adaptive/          code engine — sandbox, static analysis, scoring
    services/voice/                  open/voice — rubric evaluation and projection
    services/competency_graph/       graph, propagation, policy, coverage
    services/orchestrator/           the loop, selection, the bank store, reporting
    data/                            registered banks and their competency graphs
  validation.py                      is the shipped DATA sound? bank floor, edge validity
  scripts/                           one calibration script the engine names by path
  tests/                             ~1,250 deterministic tests, no billed calls
METHODS.md                           every method, its inputs and its outputs
cat_engine/docs/module.md            what a host project needs to know
cat_engine/docs/api.md               which calls to make, in which order, and why
```

It was seven services over a shared engine library until
[ADR-0004](cat_engine/docs/architecture.md#why-one-module); that record explains what the return
cost as well as what it bought.

## The loop

```text
seed → fill queue → choose competency → present → grade → update → finalise → repeat
```

1. **Seed** — one θ posterior per competency, from intake or flat.
2. **Fill queue** — for each **open** competency, the Picking Agent chooses one candidate.
   Finalised ones are skipped entirely: no pick, no model call.
3. **Choose** — the lowest-certainty open competency with a candidate ready.
4. **Present → Grade** — the grader routes by modality and returns `GradedOutcome`s.
5. **Update** — one fractional θ update per outcome. A code submission updates *several*
   competencies, because it genuinely evidences several.
6. **Finalise** — per competency, independently. The queue slot is released.
7. **Repeat** until every competency is finalised, or the budget runs out.

## The one idea that makes it work

Every modality reduces to the same statement:

```python
GradedOutcome(variable, score, weight, confidence)
```

and every outcome updates the same posterior through one likelihood:

```text
L(θ) = [ P(θ)^s · (1 − P(θ))^(1−s) ] ^ w
```

Two properties earn this its place:

**It reduces exactly.** At `s ∈ {0,1}, w = 1` it *is* the MCQ engine's Bernoulli likelihood,
term for term. The MCQ path is unchanged by construction, and `test_orchestration.py`
asserts **float equality** against `irt.posterior_update` to keep it that way.

**Zero weight means zero update.** `w = 0` makes `L` identically 1. The rule that an
infrastructure failure must never move a candidate's estimate falls out of the arithmetic
instead of being special-cased — it cannot be forgotten at a call site, because there is
nothing to forget.

## Who decides what

| Decision | Owner |
|---|---|
| MCQ grading | **code** — exact index comparison |
| Code grading | **code + LLM** — tests 60%, static 15%, model 25% (the measured split) |
| Ability estimate | **code** — Bayes on a θ grid |
| Which competency to probe | **code** — lowest certainty |
| When a competency is finished | **code** — precision, band stability, or budget |
| Which items are viable, and their rank | **code** — KL early, E[Fisher] later |
| **Which viable item to administer** | **LLM** — from a code-ranked shortlist |
| Interpreting a wrong answer | **LLM** — misconception diagnosis |

The model picks from a shortlist the engine ranked and interprets code. It does not grade
multiple choice, write an estimate, choose a competency, or stop an assessment. A pick below
75% of the best available information is overridden; any model failure falls back to the
engine's own choice.

## Banks are content

`cat.upload_bank(bank_id, content)` registers one, and it is the only way in. Items and graph
go together — a bank without its graph pairs
with nothing, and the coverage gate then asks a graph authored for a different bank what a
competency requires, marking every required node unmeasured and vetoing convergence for the
whole session.

Everything a posted bank must satisfy is checked before it is written: parameter bounds,
duplicate ids, bank/graph pairing, measured variables having nodes, required nodes having
items, and coverage being reachable within the question budget. Those are the same
invariants the engine suite asserts of the five checked-in banks.

The checked-in banks (`DA`, `PY`, `AIE`, `AIE-JR-V3`, `JAI-600`) are read-only seeds; a
posted bank of the same id shadows one, and deleting the shadow restores it. Each carries a
content hash, and an assessment pins the version it began under.

```json
{"item_id": "code_q_003", "modality": "code", "status": "active",
 "measures": [{"variable": "T1.4", "weight": 0.6}, {"variable": "T1.1", "weight": 0.4}],
 "cat": {"a": 1.35, "b": -0.2007, "c": 0.0},
 "payload": {"function_name": "...", "tests": [...], "rubric_criteria": [...]}}
```

`cat` is **always on θ** — the invariant that makes cross-modality ranking valid.

## Calibration — the one provisional part

MCQ items carry `a, b, c` calibrated against real responses. **Code questions do not.** They
carry an authored mastery-scale difficulty mapped onto θ in `orchestrator/calibration.py`:
`b_θ = logit(difficulty)`, `a_θ = discrimination`, `c = 0` — nobody guesses their way to a
passing test suite. This is a **modelling decision, not a derivation**, confined to one
module so real calibration later touches one file.

`cat_engine.catalogue.parity(bank_id)` guards it, and separates the two reasons a modality can lose a
ranking: `rarely_selected` (loses *with* loading applied — expected) from `miscalibrated`
(loses even at full loading — the parameters are wrong).

## Tests

```bash
python -m pytest                                     # 1180 passed, 75 skipped
BANK_DATABASE_URL=... SESSION_DATABASE_URL=... \
  python -m pytest                                   # 1254 passed, 1 skipped
```

One suite, one rootdir. The 75 skips are the three Postgres-gated files; supplying both DSNs
runs them too and the single remaining skip is data-dependent — no shipped bank has an item
measuring two mains.

```bash
ruff check .                                         # All checks passed!
```

Configured in `pyproject.toml` under `[tool.ruff]`. The rule set was chosen to match what
this codebase already does rather than to impose a house style, so it is clean rather than
mostly clean — every remaining suppression names the reason on the line above it. `EXE002` is
the one rule ignored wholesale: the repository sits on an exFAT volume with no permission
bits, so all 194 files read as executable and the finding is about the mount, not the code.

**Nothing here can spend money.** The sandbox and the model are stubbed in `conftest.py`, so
an accidental `pytest` makes no billed call because there is no code path to one. The suite
opens zero non-loopback sockets even with live credentials in the environment.

The tests cover the binary-update identity, fractional updates, calibration mapping, parity
diagnostics, queue/finalisation invariants, mixed-modality sessions, the candidate boundary,
bank validation, and that the facade and a raw `Orchestrator` produce identical reports for
one seeded assessment. Sandbox failures are explicitly tested to ensure they move no
candidate estimate.

## Status

The engine supports MCQ, code, and open/voice items. Operational score bands remain
**provisional** until independent response data passes the repository's psychometric and
human-grader gates — `decision_status` says so on every reported competency. The two
imported human-test banks are particularly explicit: their CAT parameters are traceable
syntheses from semantically related prior-bank strata, not empirical calibration of the new
item wording.

Prerequisite propagation ships **inert**. A completed screening study measured its
wrong-inference rate at **22.4%** (95% UCB 24.4%) against a 3% gate, and false blocking at
8.7%; enabling an edge is a per-edge decision with an experimental design behind it. The
coverage gate — the one propagation feature that is on — is a different mechanism and was
measured under 3%. See [docs/operations.md](cat_engine/docs/operations.md).

## Where to read next

- [METHODS.md](METHODS.md) — every method, its inputs, its outputs, and what it raises
- [docs/api.md](cat_engine/docs/api.md) — which calls to make in which order, and why some
  things are deliberately not in the return value
- [docs/module.md](cat_engine/docs/module.md) — the embedding contract: what a host supplies
- [docs/architecture.md](cat_engine/docs/architecture.md) — the loop, the measurement, the graph
- [docs/configuration.md](cat_engine/docs/configuration.md) — every setting and what it moves
- [docs/operations.md](cat_engine/docs/operations.md) — running it, first checks, enabling inference
- [docs/evidence.md](cat_engine/docs/evidence.md) — the measurements behind the defaults, and
  what has never been measured
