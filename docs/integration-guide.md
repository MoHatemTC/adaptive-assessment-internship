# Integrating the adaptive engine — the complete host-backend guide

This is everything an outside service needs to author and run adaptive assessments
through `adaptive_engine`. The engine is a **library, not a service**: it exposes pure
functions, holds nothing between calls, and does no I/O. Your backend owns users,
sessions, persistence, transport, and external grading; the engine owns measurement.

```text
             AUTHORING (once per assessment)                RUNTIME (per candidate)
 questions ──► derive_graph ──► build_assessment ──► definition ──► compile_assessment
                                     │ (you store it)                     │
                                     ▼                                    ▼
                              revise_assessment              start_assessment ──► decision
                                                                    ▲                │
                                                        (you store decision.state)   │
                                                                    │                ▼
                                                             advance_assessment ◄── answer
```

Install: the repo's `adaptive-engine`/`cat-engine` distribution, Python ≥ 3.12.
Runtime dependencies used by the stateless path: `pydantic`, `numpy`.

---

## 1. Concepts in one minute

- **Definition** — the immutable content of one assessment: items (questions), the
  competency graph, targets, and measurement policy. You create it with the authoring
  API, version it, and persist it. Identified by `(assessment_id, version)` **and** a
  content hash the engine derives.
- **Compiled assessment** — `compile_assessment(definition)`: validated content plus the
  wired engine. In-memory, immutable, safe to share across concurrent runs. Cache it by
  `(assessment_id, version)` if you like; the engine keeps no cache of its own.
- **Adaptive state** — one candidate's complete run memory, returned from every call as
  `decision.state`. JSON-compatible; you persist it and send it back. The engine can
  continue it on any process, deterministically.
- **Decision** — what every runtime call returns: `status` (`question` / `completed` /
  `terminated`), the new `state`, and either a candidate-safe `question` or a `report`.

## 2. Authoring an assessment

```python
from adaptive_engine import authoring, BankItem, CompetencyDeclaration

definition = authoring.build_assessment(
    "python-backend", "v1", items,
    declaration=[CompetencyDeclaration(id="C1.1", title="Core Python", critical=True)],
)
your_db.save_definition(definition.model_dump(mode="json"))
```

`build_assessment` derives the competency graph from the questions (see §2.3), runs the
full validation suite, and compiles once as a final gate. What it returns, the runtime
runs.

### 2.1 The item schema (`BankItem`)

Items are the engine's own schema — send models or the equivalent JSON:

```json
{
  "item_id": "C1-Q001",
  "modality": "mcq",                        // mcq | code | open | voice
  "status": "active",                       // "retired" items are never administered
  "competency": "Software Engineering",     // display labels
  "sub_competency": "C1.1 · Core Python",
  "measures": [                             // which competencies this item evidences
    {"variable": "C1.1", "weight": 1.0},    // sub-competency id, loading in (0, 1]
    {"variable": "C6.3", "weight": 0.5}     // multi-competency items are supported
  ],
  "cat": {"a": 2.13, "b": -2.43, "c": 0.09},// IRT: discrimination (0,3], difficulty
                                            // [-4,4], guessing floor [0,1)
  "estimated_time_seconds": 75,
  "mcq": {                                  // payload key must match the modality
    "stem": "Which is a standard collision-resolution strategy?",
    "options": ["...", "...", "..."],
    "answer_index": 2                       // grading data — never presented
  }
}
```

Non-mcq payloads: `code` (`prompt`, `language`, `function_name`, `starter_code`,
optionally `reference_solution` — grader-side only), `open`/`voice` (`question`,
`answer_format`). The engine grades **only mcq** itself; everything else is graded by
you (§3.3).

Competency ids follow a two-level convention: `C1` is a main competency (what gets
reported), `C1.1` is a sub-competency of `C1` (what items measure and the graph tracks).
A sub-competency can serve several mains — declare that via `CompetencyDeclaration.mains`.

### 2.2 The declaration (optional)

Questions can't say which sub-competencies are *critical* (must receive direct evidence
before their main may converge) or which serve multiple mains. The declaration carries
exactly that plus display titles. Without one, every node is treated as critical — the
strict default, refused loudly at build time if the question budget can't cover it.

### 2.3 Graph derivation (`derive_graph`)

The general-purpose questions→graph operation. Nodes come from `measures`; edge weights
from **co-measurement** (how often two competencies appear on the same items):

- sub → main edges are always `CONTRIBUTES_TO`, weights normalized to sum to 1 per main;
- sub → sub relations at/above `relation_threshold` (default 0.5) become `PREREQUISITE`,
  below it `CONTRIBUTES_TO`, below `edge_floor` (default 0.05) no edge;
- **every derived prerequisite edge ships inert** (`validation_status: "unvalidated"`,
  inference and blocking off) — a co-occurrence edge has less authority than an
  expert-authored one, and enabling one requires evidence from real sessions;
- the thresholds used are stamped into `graph.derivation`, so the artifact always says
  which rules built it.

You may instead pass a hand-authored `CompetencyGraph` to `build_assessment(graph=...)`
— it is held to the same pairing checks. The graph JSON uses the engine's wire spelling
(`from`/`to` on edges).

### 2.4 Validation findings (`validate_content`)

Returns typed findings; `build_assessment` raises `InvalidDefinition` on any error:

| code | severity | meaning |
|---|---|---|
| `item_invalid` | error | an item the `BankItem` schema rejects (message names the field) |
| `item_id_duplicate` | error | two items share an id |
| `no_valid_items` / `no_active_items` | error | nothing usable to administer |
| `graph_invalid` | error | the graph itself is malformed (bad edge endpoint, cycle, …) |
| `graph_bank_mismatch` | error | the graph describes different competencies than the items |
| `measured_node_absent` | error | an item measures a node the graph doesn't contain |
| `coverage_unreachable` | error | a main requires more sub-nodes than the question budget can cover — narrow with a declaration (`critical: false`) or raise the budget |
| `required_node_unmeasured` | error | a required node no active item measures |
| `no_graph` | warning | no graph supplied; coverage gating is off |

### 2.5 Updating an assessment (`revise_assessment`)

```python
updated = authoring.revise_assessment(
    definition, version="v2",
    add_items=[...], update_items=[...], retire_item_ids=["C1-Q007"],
    graph="rederive",      # or "keep", or an explicit CompetencyGraph
)
```

Always returns a **new** definition and requires a **new version** — the runtime pins
states to a content hash, so a silently edited same-version definition would be rejected
mid-run anyway (§3.5). Retired items stay in the definition (history stays whole) but
are never administered again. In-flight runs keep using the version they started on;
route new runs to the new version.

## 3. Running an assessment

```python
from adaptive_engine import (
    AdaptiveState, AssessmentDefinition, InitialCompetency, QuestionResponse,
    advance_assessment, compile_assessment, start_assessment,
)

definition = AssessmentDefinition.model_validate(your_db.load_definition(aid, version))
compiled = compile_assessment(definition)          # cache by (assessment_id, version)

decision = start_assessment(
    compiled,
    initial_competencies={"C1": InitialCompetency(level=3, confidence=0.8)},
)
your_db.save_state(run_id, decision.state.model_dump(mode="json"))

while decision.status == "question":
    item = decision.question.item                  # candidate-safe — render it
    answer = ...                                   # option index, or your graded result
    state = AdaptiveState.model_validate(your_db.load_state(run_id))
    decision = advance_assessment(
        compiled, state=state,
        response=QuestionResponse(question_id=item.item_id, answer=answer),
    )
    your_db.save_state(run_id, decision.state.model_dump(mode="json"))

render(decision.report)
```

### 3.1 What the candidate may see

`decision.question` is a `PresentingDTO`: `variable` (the competency being probed),
`criterion` (selection phase), and `item` — **the only item shape safe to send to a
candidate**. It carries `item_id`, `modality`, display labels, `estimated_time_seconds`,
and per modality: `stem`+`options` (mcq), `prompt`/`language`/`function_name`/
`starter_code` (code), `question`/`answer_format` (open/voice). It structurally cannot
carry answer keys, hidden tests, or reference solutions. Never send any other item shape
to a candidate, and never reveal correctness mid-run — feedback changes what the
assessment measures.

### 3.2 Initial beliefs (optional)

`InitialCompetency(level=1..5, confidence=0..1)` per target competency seeds where the
search starts. It is never counted as evidence and a few contradicting answers override
it. `confidence ≥ 0.5` selects the narrow (trusted) prior.

### 3.3 Answers and external grading

- **mcq** — `answer=<option index>` (int). Graded deterministically inside the engine.
- **code / open / voice** — you run grading (sandbox, rubric, human) and pass the
  *result*, never the raw submission:

```python
answer = GradedAnswer(score=0.85, confidence=0.9)              # one grade for all measures
answer = {                                                     # or per-competency evidence
    "C1.2": GradedAnswer(score=0.95, confidence=0.9),
    "C1.5": GradedAnswer(score=0.30, confidence=0.8),
}
```

`score` is 0..1 correctness; `confidence` is how sure the grader is; optional `weight`
overrides how much of a full observation this carries (default: the item's declared
loading × confidence). `weight=0` means "graded, no usable evidence" — the engine
records nothing rather than counting a hollow observation, so an infrastructure failure
must map to *not calling advance at all* or to `weight=0`, never to `score=0`.

### 3.4 Decisions and reports

| `status` | meaning | `question` | `report` | `converged` |
|---|---|---|---|---|
| `question` | present it, collect an answer | set | — | `False` |
| `completed` | every competency converged | — | set | `True` |
| `terminated` | ended without full convergence | — | set | `False` |

`stop_reason` (session-level): `all_variables_finalised`, `item_budget`, `time_limit`,
`no_candidates_available`. The report's rows carry per-competency results — the fields
worth rendering:

- `level` (1–5) and `band` label, **with** `credible_interval_95` and `p_reported_band`
  — at the precision target, the reported band is right ~85% of the time at a band
  centre and worse near a boundary; showing the level alone overstates the measurement;
- `decision_status` — `"not_assessed"` when a competency was never asked about (render
  that, not a level), `"provisional"` otherwise;
- `observations`, `converged`, per-competency `stop_reason` (`band_probability` and
  `precision` claim convergence; `question_budget`/`bank_exhausted`/`time_budget` are
  budget outcomes and never mean "measured");
- `graph_coverage_satisfied` / `graph_unmeasured_nodes` — which required skills were
  actually checked;
- `modalities_used`, `aberrant_response_count`.

### 3.5 The state contract

`decision.state` is your only responsibility between calls:

- Persist `state.model_dump(mode="json")` after **every** call; restore with
  `AdaptiveState.model_validate(payload)`. Treat the blob as opaque — never edit it.
- It contains no user identity and no secrets, but it does encode the run's measurement
  trail; store it with the same care as answers.
- It is pinned to `(assessment_id, assessment_version, content_hash)`. Editing a
  definition without bumping its version makes every in-flight state fail with
  `state_mismatch` — that is the guard working, not a bug. Ship content changes as new
  versions.
- Determinism: same compiled assessment + state + response ⇒ same decision, on any
  process (selection randomness travels in the state). The only wall-clock behavior is
  the time-limit rule.

### 3.6 Errors

Every engine error carries a stable `code`. If `advance_assessment` raises, **nothing
was recorded** — the caller's stored state is untouched and the same question can be
retried. Suggested HTTP mapping:

| code | raise | when | suggested HTTP |
|---|---|---|---|
| `invalid_definition` | `InvalidDefinition` | authoring/compile content errors | 422 |
| `invalid_initial_competency` | `InvalidInitialCompetency` | bad level/confidence, unknown competency | 422 |
| `invalid_answer` | `InvalidAnswer` | answer doesn't fit the item's modality | 422 |
| `stale_response` | `StaleResponse` | answers a question that is not the current one (double-submit, out-of-order, retry of an answered question) | 409 |
| `assessment_finished` | `AssessmentFinished` | run already completed/terminated | 409 |
| `state_mismatch` | `StateMismatch` | state belongs to another assessment/version, or content changed behind the version | 409 |
| `invalid_state` | `InvalidState` | corrupted/foreign state blob | 409 or 500 |

### 3.7 Concurrency

The engine holds no locks — serialize per run in your backend (one writer per run_id).
If two replicas race anyway, the loser is detected: its response answers a question that
is no longer current and gets `stale_response`, with nothing recorded.

## 4. Operational limits (current, deliberate)

- **One measurement policy per process.** `MeasurementPolicy` overrides route through
  the engine's config layer, which refuses a second *different* policy in one process
  (loud `ConfigConflict`). Run differently-tuned assessments in separate workers, or
  leave `policy=None` for the shipped defaults.
- The engine performs no network I/O and must stay that way: grade non-mcq modalities in
  your infrastructure and pass results in.
- Timing fields inside the state read the real clock (the time budget should); exact
  replay holds for everything except the time-limit rule.

## 5. Reference implementation

[`take_assessment.py`](../take_assessment.py) at the repo root is a complete host in
~250 lines: it loads content, persists state to disk between every question (the same
save/load cycle an HTTP backend does), resumes after interruption, and prints a
monitoring panel showing what the engine did after each answer. Start there.

Deeper background: [`docs/adaptive-engine-design.md`](adaptive-engine-design.md) (why
the wrapper is shaped this way), [`adaptive_engine/README.md`](../adaptive_engine/README.md)
(package overview), `METHODS.md` (the legacy stateful surface — deprecated; do not build
new callers on it).
