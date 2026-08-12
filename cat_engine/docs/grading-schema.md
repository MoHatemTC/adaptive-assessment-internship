# Grading schema (MCQ · Code · Open/Voice)

**Audience:** testing / content authors and QA.

This document describes **how responses become scores** that update CAT estimates.
Bank authoring shapes are in [`bank-schema.md`](bank-schema.md).

Engine entry point: `GraderAgent.grade(item, response)`  
→ list of `GradedOutcome` objects rolled up onto session mains.

---

## 1. Shared outcome shape (all modalities)

Every graded item produces one or more outcomes:

```json
{
  "variable": "PY.1",
  "score": 1.0,
  "weight": 1.0,
  "confidence": 1.0,
  "source_item_id": "PY-Q001",
  "modality": "mcq"
}
```

| Field | Meaning |
|-------|---------|
| `variable` | Sub-competency (or main) this evidence applies to |
| `score` | Evidence quality in `[0, 1]` |
| `weight` | How much of an observation this is worth in `[0, 1]` |
| `confidence` | Grader confidence in `[0, 1]` |
| `modality` | `"mcq"` \| `"code"` \| `"open"` |

Rollup: outcomes whose variables belong under a session main (e.g. `PY.1` under `PY`)
are aggregated into that main’s posterior on the shared θ scale.

**Zero weight** = no evidence (infrastructure failure, unscorable). Posterior unchanged;
observation count does **not** increase.

---

## 2. MCQ grading

### Candidate response

- Type: **integer option index** (0-based)
- Must satisfy `0 <= chosen < len(options)`

### Scoring

```
score = 1.0 if chosen == answer_index else 0.0
weight = measures[].weight   # per measured variable
confidence = 1.0
```

One outcome is emitted **per** `measures[]` entry, all sharing the same binary score.

### GradedResponse.detail

```json
{
  "chosen_index": 2,
  "correct": true,
  "answer_index": 2
}
```

### Author checklist

- `answer_index` matches the intended option
- Prefer 4 options; keep distractors mutually exclusive
- Align `misconceptions[i]` with `options[i]`

---

## 3. Code grading

### Candidate response

- Type: **source code string**
- Must define `function_name` from the item payload

### Pipeline

1. Run all `tests` in E2B sandbox: `fn(*test["input"])` vs `expected`
2. Static analysis of the submission
3. Optional LLM diagnosis (policy-dependent; default approach **B**)
4. Criterion scores → competency evidence using `measures` loadings
5. Map to `GradedOutcome`s (`score`, `weight=evidence_strength`, `confidence`)

### Criterion IDs (fixed)

| Criterion | Typical evidence |
|-----------|------------------|
| `functional_correctness` | Weighted test pass ratio |
| `edge_case_handling` | Tests + static signals |
| `algorithm_choice` | Tests + static (+ LLM if enabled) |
| `code_quality` | Static (+ LLM if enabled) |

Approach presets (`settings.code_approach`):

| Approach | Role of LLM |
|----------|-------------|
| `A` | Objective / tests+static only |
| `B` | **Default** — test-anchored with LLM diagnosis share |
| `C` | Model-led |

### Public trial runs (not graded)

- Only tests with `"visibility": "public"`
- Counted against `code_trial_runs_per_question`
- Must **not** move the CAT posterior

### GradedResponse.detail

`SubmissionResult`-shaped object, including:

- `overall_score` (`null` if unusable run — not the same as 0.0)
- `passed_tests` / `total_tests`
- `criterion_scores`
- `compiled`, `flags`, `diagnostic`, `misconception_codes`

### Author checklist

- `input` is always a **list of positional arguments**
- Include both public examples and hidden adversarial cases
- Hidden tests must catch naive solutions that pass only public cases
- `c` in envelope `cat` must be `0.0`

---

## 4. Open / voice grading

### Candidate response (before GraderAgent)

Open items are **evaluated first**, then passed as `GradedVoiceResponse`:

```text
audio / transcript
  → VoiceResponsePackage (turns, transcript, confidence)
  → LLM rubric evaluation (or heuristic fallback)
  → English-only clamp if needed
  → GradedVoiceResponse { package, evaluation, rubric }
  → GraderAgent._grade_open → GradedOutcome[]
```

### Rubric evaluation output (`VoiceEvaluation`)

```json
{
  "item_id": "open_py_011",
  "rubric_id": "inline_open_py_011",
  "rubric_version": "1.0",
  "evaluation_confidence": 0.85,
  "overall_rationale": "…",
  "flags": [],
  "criterion_evidence": [
    {
      "criterion_id": "technical_accuracy",
      "competency_id": "PY.1",
      "raw_score": 6.5,
      "maximum_score": 8,
      "confidence": 0.9,
      "prompt_dependency": "independent",
      "quote": "…",
      "quote_turn_id": "t0",
      "description": "…"
    }
  ]
}
```

| Field | Notes |
|-------|-------|
| `raw_score` | Points awarded on that criterion |
| `maximum_score` | From bank rubric |
| `prompt_dependency` | `independent` \| `probe_supported` \| `probe_dependent` |
| `quote` | Verbatim from a **candidate** turn, or `null` |

### Score aggregation to CAT

Criterion scores are normalized by `maximum_score`, weighted, and projected onto
`competency_id` / `measures` variables as fractional `score ∈ [0, 1]` with an
evidence `weight` / `confidence`.

**English-only policy (hard):**

- Answers must be English
- Non-English (or strongly flagged ASR dumps) → `NON_ENGLISH_SPEECH` flag and
  near-zero clamp on technical criteria (typically ≤ 15% of max when fully non-English)

### Author checklist

- Fill `reference_answer`, `expected_answer_points`, and criterion `descriptor`s
- Set `competency_id` on each criterion to the measured sub (e.g. `BE.2`)
- Prefer the four standard criterion IDs for consistent weighting
- Write pitfalls that describe **wrong mechanisms**, not just “incomplete”

### External rubric file (optional alternate)

Path pattern: `cat_engine/engine/data/voice_rubrics/rubric_*.json`

```json
{
  "rubric_id": "rubric_open_be_002",
  "version": "1.0",
  "item_id": "BE-O003",
  "competency": "BE",
  "criteria": [
    {
      "criterion_id": "technical_accuracy",
      "competency_id": "BE.2",
      "weight": 0.4,
      "required": true,
      "maximum_score": 8.0,
      "descriptor": "…"
    }
  ],
  "expected_answer_points": ["…"],
  "common_pitfalls": ["…"],
  "reference_answer": "…"
}
```

Inline rubrics on the open payload are preferred for new multi-competency banks.

---

## 5. What testing team should verify (QA)

| Check | Pass criteria |
|-------|----------------|
| MCQ key | Choosing `answer_index` yields score 1; wrong index yields 0 |
| Code public/hidden | Naive solution fails ≥ 1 hidden test |
| Code entrypoint | `function_name` matches starter / reference |
| Open rubric | Strong English answer scores high; empty / off-topic near zero |
| Open English | Non-English transcript does not earn technical credit |
| Multi-main | Items with `BE.*` and `PY.*` both load; tracks appear separately in setup |
| Cross-load | Primary (max weight) owns the track; secondary still updates its main if selected |
| IDs | No duplicate `item_id`; all `measures[].variable` follow `MAIN` / `MAIN.N` |

---

## 6. Mapping from bank → grader (quick reference)

| Modality | Response type into `grade()` | Score source |
|----------|------------------------------|--------------|
| `mcq` | `int` option index | Binary vs `answer_index` |
| `code` | `str` source | Sandbox tests + criteria → competency evidence |
| `open` | `GradedVoiceResponse` | Rubric LLM / heuristic + English clamp |

Envelope fields used by **selection** (not graders): `measures`, `cat.{a,b,c}`, `status`, `modality`.
Payload fields used by **graders**: `mcq.*`, `code.*`, `open.*`.
