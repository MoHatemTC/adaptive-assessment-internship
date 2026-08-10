# Unified multi-competency, multi-modality question bank schema

**Audience:** testing / content authors who will deliver one bank file containing
**multiple main competencies** and **MCQ + code + open** items.

**Engine path:** `backend/app/data/question_bank.json`  
**Loader:** `JsonUnifiedBank` (`backend/app/services/orchestrator/bank.py`)  
**Envelope model:** `BankItem` (`backend/app/schemas/orchestration.py`)

Companion document: [`grading-schema.md`](grading-schema.md) (how each modality is scored).

---

## 1. Top-level file

```json
{
  "schema_version": 2,
  "competency": "optional display label when the bank is single-track",
  "items": []
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `schema_version` | **yes** | Integer; current engine expects `2` |
| `competency` | no | Human label only; not used for selection |
| `items` | **yes** | Non-empty array of item envelopes |

Invalid items are logged and skipped at load time. An empty valid set raises.

---

## 2. Competency ID conventions (critical)

Main competencies are **derived**, not listed separately.

| Concept | Rule | Examples |
|---------|------|----------|
| Main code | Prefix before first `.` in `measures[].variable` | `PY`, `BE`, `DE`, `C1` |
| Sub-competency | `MAIN.N` (dot + segment) | `PY.1`, `BE.2`, `C1.3` |
| Display name | `competency` string on the item | `"Python Engineering"` |
| Sub display | `sub_competency` string | `"PY.1 · Syntax, variables & built-in types"` |

Rules for authors:

1. Use a **stable short main code** (`PY`, `BE`, …). Do not put spaces in variable IDs.
2. Every item must measure at least one variable via `measures`.
3. **Primary measure** = highest `weight`. That decides track ownership when an item cross-loads.
4. Cross-loading is allowed (e.g. a pandas item with `DE.1` @ 0.7 and `PY.2` @ 0.3).
5. If a competency graph is used later, graph node IDs must match these variable IDs exactly.

How the engine discovers mains:

- `JsonUnifiedBank.variables()` → main codes from all measure prefixes
- Candidate setup offers those mains as assessable tracks

---

## 3. Item envelope (every modality)

```json
{
  "item_id": "PY-Q001",
  "modality": "mcq",
  "status": "active",
  "competency": "Python Engineering",
  "sub_competency": "PY.1 · Syntax, variables & built-in types",
  "measures": [
    { "variable": "PY.1", "weight": 1.0 }
  ],
  "cat": { "a": 1.9, "b": -2.44, "c": 0.11 },
  "mcq": { }
}
```

| Field | Type | Rules |
|-------|------|-------|
| `item_id` | string | **Unique** across the whole file |
| `modality` | `"mcq"` \| `"code"` \| `"open"` \| `"voice"` | Must match which payload key is present |
| `status` | string | Use `"active"` to include in selection; anything else is ignored |
| `competency` | string | Main / track display name |
| `sub_competency` | string | Human label for the primary sub |
| `measures` | array | ≥ 1 entries |
| `measures[].variable` | string | Sub-id `MAIN.N` or bare main |
| `measures[].weight` | float | `(0, 1]` loading on that variable |
| `cat.a` | float | Discrimination `(0, 3]` |
| `cat.b` | float | Difficulty on θ `[-4, 4]` |
| `cat.c` | float | Guessing floor `[0, 1)`; use `0.0` for code/open/voice |
| `estimated_time_seconds` | float | Optional but **strongly wanted** for code and voice. Selection ranks on information per MINUTE, so an item without one is scored against a per-modality default rather than its real cost |
| `minimum_success_confidence` | float | Optional. A stricter grader-confidence floor for THIS item before the graph may infer from it. Only ever tightens the global floor |

Exactly **one** of `mcq`, `code`, `open`, or `voice` must be non-null and match `modality`.

### `open` versus `voice`

They grade identically — same evaluator, same four rubric criteria, same competency
projection. The difference is how the answer is collected and how the report describes it:

- `open` may be typed. `voice` is a spoken interview, and selects the live-interview path.
- The report distinguishes them, so a reader can tell 35 minutes of spoken interview from
  a typed essay. That is the whole reason they are separate.

A `voice` payload may use `evaluation_criteria` and `sample_strong_answer` in place of
`expected_answer_points` and `reference_answer`; the evaluator accepts either.

### CAT parameter guidance for authors

| Modality | `c` | Notes |
|----------|-----|-------|
| MCQ | typically `0.05`–`0.25` | 4-option MCQ often ≈ `0.25` max guess |
| Code | **`0.0`** | No guessing floor |
| Open / voice | **`0.0`** | No guessing floor |

`b` should span easy → hard within each main so CAT can escalate. Rough bands:

| Band | Approx. `b` |
|------|-------------|
| very easy | ≤ −1.5 |
| easy | −1.5 … −0.5 |
| medium | −0.5 … +0.5 |
| hard | +0.5 … +1.5 |
| very hard | ≥ +1.5 |

---

## 4. Multi-main example (one file)

```json
{
  "schema_version": 2,
  "items": [
    {
      "item_id": "PY-Q100",
      "modality": "mcq",
      "status": "active",
      "competency": "Python Engineering",
      "sub_competency": "PY.1 · Syntax",
      "measures": [{ "variable": "PY.1", "weight": 1.0 }],
      "cat": { "a": 1.8, "b": -1.2, "c": 0.15 },
      "mcq": {
        "stem": "Which literal is an empty dict?",
        "options": ["[]", "{}", "()", "set()"],
        "answer_index": 1,
        "rationale": "An empty dict is written with curly braces {}.",
        "misconceptions": [
          "[] is an empty list, not a dict.",
          "CORRECT",
          "() is an empty tuple.",
          "set() creates an empty set; {} is a dict."
        ]
      }
    },
    {
      "item_id": "PY-C010",
      "modality": "code",
      "status": "active",
      "competency": "Python Engineering",
      "sub_competency": "PY.5 · Functions, arguments & scope",
      "measures": [
        { "variable": "PY.5", "weight": 0.8 },
        { "variable": "PY.1", "weight": 0.2 }
      ],
      "cat": { "a": 1.4, "b": 0.3, "c": 0.0 },
      "code": {
        "title": "Add two numbers",
        "prompt": "Implement `add(a, b)` returning the sum of a and b.",
        "language": "python",
        "function_name": "add",
        "starter_code": "def add(a, b):\n    ...\n",
        "difficulty": 0.45,
        "discrimination": 1.4,
        "rubric_criteria": [
          { "criterion_id": "functional_correctness", "maximum_score": 8 },
          { "criterion_id": "edge_case_handling", "maximum_score": 5 },
          { "criterion_id": "algorithm_choice", "maximum_score": 4 },
          { "criterion_id": "code_quality", "maximum_score": 3 }
        ],
        "tests": [
          {
            "test_id": "t1",
            "visibility": "public",
            "weight": 1.0,
            "input": [1, 2],
            "expected": 3
          },
          {
            "test_id": "h1",
            "visibility": "hidden",
            "weight": 1.0,
            "input": [-1, 1],
            "expected": 0
          }
        ],
        "reference_solution": "def add(a, b):\n    return a + b\n"
      }
    },
    {
      "item_id": "BE-Q001",
      "modality": "mcq",
      "status": "active",
      "competency": "Backend Engineering",
      "sub_competency": "BE.1 · HTTP & REST",
      "measures": [{ "variable": "BE.1", "weight": 1.0 }],
      "cat": { "a": 1.6, "b": -0.5, "c": 0.2 },
      "mcq": {
        "stem": "Which status code means resource created?",
        "options": ["200", "201", "204", "400"],
        "answer_index": 1
      }
    },
    {
      "item_id": "BE-O003",
      "modality": "open",
      "status": "active",
      "competency": "Backend Engineering",
      "sub_competency": "BE.2 · Auth",
      "measures": [{ "variable": "BE.2", "weight": 1.0 }],
      "cat": { "a": 1.2, "b": 0.8, "c": 0.0 },
      "open": {
        "question": "Explain how JWT access tokens differ from refresh tokens.",
        "answer_format": "spoken or written technical answer (~150-300 words)",
        "reference_answer": "…",
        "expected_answer_points": [
          "Access tokens are short-lived and authorize API calls.",
          "Refresh tokens are longer-lived and obtain new access tokens."
        ],
        "rubric_criteria": [
          {
            "criterion_id": "technical_accuracy",
            "competency_id": "BE.2",
            "maximum_score": 8,
            "descriptor": "Correctly distinguishes access vs refresh token purpose and lifetime."
          },
          {
            "criterion_id": "completeness",
            "competency_id": "BE.2",
            "maximum_score": 5,
            "descriptor": "Covers lifetime, storage/use, and rotation/revocation at a high level."
          },
          {
            "criterion_id": "reasoning_and_justification",
            "competency_id": "BE.2",
            "maximum_score": 4,
            "descriptor": "Explains why the split exists (blast radius / UX)."
          },
          {
            "criterion_id": "clarity_and_communication",
            "competency_id": "BE.2",
            "maximum_score": 3,
            "descriptor": "Clear, structured English."
          }
        ],
        "common_pitfalls": [
          "Treats refresh tokens as API authorization credentials."
        ]
      }
    }
  ]
}
```

A machine-readable starter template also lives at:

`docs/examples/multi_competency_bank.example.json`

---

## 5. MCQ payload

| Field | Required | Notes |
|-------|----------|-------|
| `stem` | **yes** | Question text (markdown OK) |
| `options` | **yes** | Array of ≥ 2 strings |
| `answer_index` | **yes** | 0-based index into `options` |
| `rationale` | recommended | Why the key is correct |
| `misconceptions` | recommended | Parallel to `options`; use `"CORRECT"` on the key |
| `difficulty` | optional | Author label (`very_easy` … `very_hard`) |
| `discrimination` | optional | Author label |
| `guess_resistance` | optional | Author label |
| `est_dev_pass_rate_pct` | optional | Calibration hint |
| `angle` | optional | Topic facet inside the sub |

**Grading:** candidate chooses an option index; score is `1.0` if equal to `answer_index`, else `0.0`. See grading schema.

---

## 6. Code payload

| Field | Required | Notes |
|-------|----------|-------|
| `prompt` | **yes** | Full problem statement |
| `function_name` | **yes** | Entrypoint called by the sandbox |
| `tests` | **yes** | Non-empty list of executable cases |
| `language` | recommended | Usually `"python"` |
| `title` | recommended | Short title |
| `starter_code` | recommended | Scaffold shown to candidate |
| `reference_solution` | recommended | For authors / QA only; never shown to candidates |
| `difficulty` | recommended | Prefer numeric `0..1` for the code engine; labels also appear in current bank |
| `discrimination` | optional | Numeric preferred for parity checks |
| `rubric_criteria` | **yes for production** | See below |
| `prerequisites` | optional | Informational |
| `estimated_time_seconds` | optional | UI hint |

### Test case object

| Field | Required | Notes |
|-------|----------|-------|
| `test_id` | **yes** | Unique within the item |
| `input` | **yes** | **Positional args list** passed to `function_name(*input)` |
| `expected` | **yes** | Exact expected return value (JSON-serializable) |
| `visibility` | **yes** | `"public"` or `"hidden"` |
| `weight` | recommended | Default `1.0`; scales contribution to pass ratio |
| `criterion_weights` | optional | Map criterion_id → weight for this test |
| `competency_weights` | optional | Map variable id → weight for this test |

**Public vs hidden:** trial runs before submit only use `visibility == "public"`. Graded runs use all tests.

### Code rubric criteria (allowed IDs)

Exactly these four (engine-enforced):

- `functional_correctness`
- `edge_case_handling`
- `algorithm_choice`
- `code_quality`

Example:

```json
"rubric_criteria": [
  { "criterion_id": "functional_correctness", "maximum_score": 8 },
  { "criterion_id": "edge_case_handling", "maximum_score": 5 },
  { "criterion_id": "algorithm_choice", "maximum_score": 4 },
  { "criterion_id": "code_quality", "maximum_score": 3 }
]
```

`maximum_score` is a **relative weight**, not a hard cap on the 0–1 CAT score.

Competency loadings for code scoring are taken from the item envelope `measures` (copied into `competencies` at grade time).

---

## 7. Open / voice payload

Prefer **inline rubrics** on the item (current PY bank style). External files under
`backend/app/data/voice_rubrics/` are legacy/alternate and must still use matching
`competency_id` values.

| Field | Required | Notes |
|-------|----------|-------|
| `question` | **yes** | Prompt spoken / shown to the candidate |
| `answer_format` | recommended | e.g. `"spoken"` or length guidance |
| `reference_answer` | **yes for production** | Grader yardstick (not shown to candidate) |
| `expected_answer_points` | **yes for production** | Bullet checklist the grader uses |
| `rubric_criteria` | **yes for production** | See below |
| `common_pitfalls` | recommended | Wrong mechanisms to penalize |
| `rubric_id` | optional | Stable id; defaults to `inline_{item_id}` |
| `difficulty` / `discrimination` / `angle` | optional | Author metadata |
| `estimated_time_seconds` | optional | UI hint |

### Open rubric criterion object

| Field | Required | Notes |
|-------|----------|-------|
| `criterion_id` | **yes** | Prefer the standard four (below) |
| `maximum_score` | **yes** | Points scale for that criterion |
| `descriptor` | **yes** | What full marks requires |
| `competency_id` | recommended | Defaults to primary `measures[0].variable` |
| `weight` | optional | Relative share; defaults: 0.40 / 0.25 / 0.20 / 0.15 |
| `required` | optional | Default true except clarity |

**Standard open criterion IDs** (recommended):

| `criterion_id` | Default weight | Required? |
|----------------|----------------|-----------|
| `technical_accuracy` | 0.40 | yes |
| `completeness` | 0.25 | yes |
| `reasoning_and_justification` | 0.20 | yes |
| `clarity_and_communication` | 0.15 | no |

Answers are graded in **English only**. Non-English speech is clamped near zero.

---

## 8. Coverage checklist for testing team

For **each main** you ship (`PY`, `BE`, …):

1. At least one active item in **each modality** you want assessed (mcq / code / open / voice), or accept that modality will never appear.
2. Difficulty (`cat.b`) spans easy → hard so CAT can escalate.
3. Sub-competencies have enough items that early repeats are unlikely within ~12 questions.
4. All `item_id`s unique; all `measures[].variable` use the agreed ID scheme.
5. Code items: public + hidden tests; public tests never leak the full key.
6. Open items: reference answer + expected points + rubric descriptors filled in.
7. **`sub_competency` labels must be consistent per variable id.** The same `C6.8` must mean the same thing in every modality. This is not cosmetic: the engine keys on the ID, so two authors numbering differently silently records one candidate's answer as evidence about a different skill. It happened — see [`banks/AIE.md`](banks/AIE.md).
8. `estimated_time_seconds` on every code and voice item. Selection divides information by it, so an unstated time is a guess that decides the modality mix.
9. Optional: companion `competency_graph.json` using the same IDs (`MAIN`, `MAIN.N`). If you ship prerequisite edges, ship them with `allow_upward_inference` and `allow_downward_blocking` set to `false` until they have been validated against real response data.

---

## 9. Deliverables expected from testing team

| Artifact | Format | Notes |
|----------|--------|-------|
| Question bank | single `question_bank.json` | Schema version 2, multi-main OK |
| Competency map | spreadsheet or markdown | Main code, title, list of subs |
| (Optional) Graph | `competency_graph.json` | Prerequisites / contributes_to |
| QA notes | markdown | Known weak items, calibration TODOs |

Banks are registered, not replaced. Add a row to `BANK_REGISTRY` in
`app/services/orchestrator/registry.py` naming the bank file and its competency graph, and
set `ACTIVE_BANK` to make it the default. Existing banks stay on disk and stay selectable —
a session names its bank at creation and the choice is locked for that session.

A bank and its graph are selected **together**. Pairing a bank with another bank's graph
makes every required coverage node unmeasurable and vetoes convergence for the whole
session, which presents as a measurement fault rather than a configuration one.

Before shipping, check the coverage arithmetic: covering `R` required sub-competencies
costs `R` questions when each item measures one node, and the per-competency cap is 12.
`tests/test_bank_registry.py` fails the build when `R > cap - 2`.
