# Masaar MCQ CAT Test Harness

Standalone Streamlit app to validate the Masaar IRT/CAT adaptive engine on MCQ-only questions.

## Quick start

```bash
cd masaar-mcq-cat-test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # add OPENAI_API_KEY — the app will not start without it
streamlit run app.py
```

The app loads **`enriched_bank_cat.json`** (120 calibrated items across 5 competencies).
The bank is fixed and the LLM is required — see [The LLM is required](#the-llm-is-required).

## Tuned CAT behaviour (post live-session fixes)

Live `assessment.log` showed failures this build addresses:

| Failure | Fix |
|---|---|
| θ̂≈2.5 but items only had `b∈{-1,0,1}` → Fisher≈0.02, SE stalled ~0.71 | Extended difficulty ladder `very_easy…very_hard` → `b∈{-2…+2}` + richer bank |
| Certainty % fell after correct answers | Certainty is now monotonic in SE reduction (intake only blends at q=0) |
| Streamlit crash on `sim_comp` overwrite | Store sim results under `sim_comp_name` |
| Clone synthesis recycling stems | Curated unique bank; clone path only fills gaps |

Selection: **KL information for questions 1–3**, then **posterior-expected 3PL Fisher**.
Stop when `SE ≤ 0.65` or `12` questions (recalibrated from unreachable 0.45@10 with coarse banks).

## LLM procedural selection (OpenAI)

MCQ **grading stays deterministic** (exact index match). The **LLM selects the next item** by executing a fixed procedure on an engine-scored shortlist:

1. Engine ranks unserved items by **KL** (q&lt;3) or **3PL Fisher** (q≥3)
2. Top 5 candidates sent to OpenAI with θ̂, SE, served history, IRT params
3. LLM must follow tie-break rules (info → |b−θ| → sub-competency coverage → discrimination)
4. LLM returns `selected_id` **only from the shortlist** — invalid ids fall back to engine
5. LLM also returns `adaptation_note` explaining why the item refines the estimate

### Configure

```bash
cp .env.example .env
# edit .env:
# OPENAI_API_KEY=sk-proj-...
# OPENAI_MODEL=gpt-4o-mini
# LANGFUSE_SECRET_KEY=sk-lf-...
# LANGFUSE_PUBLIC_KEY=pk-lf-...
# LANGFUSE_BASE_URL=https://cloud.langfuse.com
pip install -r requirements.txt
streamlit run app.py
```

Setup screen shows connection status and a toggle for LLM selection.
If OpenAI fails, the app falls back to the same procedure executed deterministically in Python.

## The LLM is required

There is no engine-only mode and no "use the LLM" toggle. Each branch exists to measure
what happens when the LLM owns part of the CAT; a run that quietly used coded selection
instead produces a number that looks like a result and answers a different question. The
connection is probed automatically on load and the app stops with an actionable error if
it fails.

The bank is likewise fixed to `enriched_bank_cat.json` — no uploader. The three branches
are only comparable if they run identical items, and an arbitrary uploaded bank may carry
no numeric IRT parameters at all, in which case the engine silently falls back to label
maps and the CAT degrades without saying so.

## LLM calls and cost

Measured, not estimated: `llm_client` reads `usage` off every API response, and
`measure_llm_cost.py` drives the real controller against the real API.

```bash
python measure_llm_cost.py --competencies 2   # real calls, real tokens
python measure_llm_cost.py --dry-run          # structure only, no spend
```

Per **competency** (up to 12 questions), on `gpt-4o-mini` at $0.15/$0.60 per 1M tokens:

| Branch | LLM calls | Per question |
|---|---:|---:|
| `approach-1-code-math-llm-pick` | 12 | 1 (selection) |
| `approach-2-llm-math-code-pick` | 12 | 1 (ability update) |
| `approach-3-llm-full-cat` | ~24 | 2 (update, then selection) |

A full assessment is 5 competencies. See each branch's README for its measured USD figure.
Pricing lives in `MODEL_PRICING_USD_PER_1M` in `llm_client.py`; an unpriced model reports
no cost rather than a wrong one. The setup screen has a live **LLM usage & cost** panel.

## Langfuse tracing

When Langfuse keys are present in `.env`, the app records comparable trace events for:

- session start
- next-item selection
- LLM selection responses
- answer updates
- competency finalization

LLM calls are recorded as **generations carrying model, `usage_details` and
`cost_details`**, so cost per approach is visible in the Langfuse dashboard rather than
showing 0.

### Trace tags

Every trace is named `masaar-cat-<approach_id>` and tagged, so the branches can be sliced
against each other:

| Tag | Example |
|---|---|
| approach id | `approach-1-code-math-llm-pick` |
| math actor | `math:code` |
| selection actor | `selection:llm` |
| bank | `bank:enriched_bank_cat` |
| model | `model:gpt-4o-mini` |

Tags are namespaced `key:value` so `math:llm` groups approaches 2 and 3 regardless of how
they select. Add your own with `CAT_TRACE_TAGS="run:pilot-3,cohort:2026-summer"`. Metadata
carries `approach_id`, `math_actor`, `selection_actor` and `bank_id`.

> Tags previously never reached Langfuse. `trace_event` called
> `client.update_current_trace()`, which does not exist on the langfuse v4 client: it
> raised `AttributeError` into a bare `except: pass`, so every trace landed with `tags=[]`
> and was named after its observation. v4 sets trace-level attributes via
> `langfuse.propagate_attributes()`.

```bash
python test_usage_tracing.py   # metering + cost attribution; no API key or Langfuse needed
```

## Question bank JSON

```json
{
  "id": "q001",
  "competency": "Backend Engineering",
  "sub_competency": "API Design",
  "difficulty": "medium",
  "discrimination": "medium",
  "stem": "Which HTTP status code indicates a successful resource creation?",
  "options": ["200 OK", "201 Created", "204 No Content", "301 Moved Permanently"],
  "answer_index": 1
}
```

| Field | Notes |
|---|---|
| `difficulty` | `very_easy` / `easy` / `medium` / `hard` / `very_hard` → b ∈ {-2,-1,0,1,2}. Legacy easy/medium/hard still work. |
| `discrimination` | `low` / `medium` / `high` → a ∈ {0.6, 1.0, 1.5} |
| `c` | Not in JSON — set as `1/len(options)` |

### Bundled banks

| File | Contents |
|---|---|
| `enriched_bank_cat.json` | **The bank the app runs.** 120 MCQs, 5 competencies × 24, calibrated `a`/`b`/`c` per item, cueing-controlled |
| `enriched_bank.json` | Earlier 122-item bank, kept for reference only — labels only (no numeric IRT) and heavily cued. Not loadable from the UI; see below |
| `sample_bank.json` | Small 21-item smoke bank, reference only |

## `enriched_bank_cat.json` — the CAT-ready bank

Built from `AI_Internship_Technical_QuestionBank.xlsx` (62 MCQ rows, options rewritten;
58 new items authored to fill the ladder). Every item carries **numeric `a`, `b`, `c`**, so
`engine.resolve_a/resolve_b` use them directly instead of collapsing onto the label maps.

### Why the earlier bank was not usable for CAT

| Property | `enriched_bank.json` | `enriched_bank_cat.json` |
|---|---|---|
| Numeric IRT params | none — 5 discrete `b` values via label fallback | per-item, `b` continuous over ±2.4 |
| Key position A/B/C/D | 3 / 112 / 7 / 0 — **92% "B"** | 30 / 30 / 30 / 30 — 25% each |
| Longest option is the key | 109/122 (**89%**, p≈6e-51) | 31/120 (26%, p=0.83 — chance) |
| Options within 1.4× length | 7/122 | 120/120 |

A candidate who always picked "the long B" scored ~89% on the old bank without reading a stem.

### Design rules

- **24 items per competency**: 8 sub-competencies × 3, ladder 5/5/4/5/5 across `very_easy…very_hard`.
- **`a` is independent of `b`.** Discrimination is spread across every difficulty band
  (a very_easy item can still separate sharply). Pinning high `a` to high `b` starves the
  low-θ end of information and leaves weak candidates measured with blunt items —
  `validate_bank.py` fails on `corr(difficulty, a) > 0.75` for this reason.
- **Within a band, the sharpest item takes the outer edge** of the `b` range, since
  information is scarcest at the ladder's extremes.
- **Distractors are misconceptions, not filler.** Each is a specific error a candidate
  could hold; `misconceptions[i]` records which, with `"CORRECT"` at the keyed index.
- `rationale` and `misconceptions` are extra fields — the app ignores them.

### Regenerating and checking

```bash
python build_enriched_bank.py <authored_items_dir> enriched_bank_cat.json
python validate_bank.py enriched_bank_cat.json   # exit 1 on any violation
python simulate_cat.py enriched_bank_cat.json 300
```

`validate_bank.py` treats "no cueing" as a hypothesis to test rather than a judgement call:
chi-square on key position, exact binomial on longest-is-key **and** shortest-is-key, and a
mean length-rank z-test (a bank can pass "is longest" while the key is reliably *second*
longest — equally learnable). It also walks the ability range and fails if the best available
item at any θ is too blunt to measure there.

### Measured behaviour (300 simulated candidates per point)

| Operating condition | RMSE(θ̂) | Reaches SE ≤ 0.65 in 12 items |
|---|---|---|
| No self-rating (pessimistic bound) | 0.72 | 16% |
| Honest self-rating (expected case) | 0.63 | 35% |

θ̂ is **essentially unbiased at every ability level** (bias −0.11…+0.10 in the expected case),
including θ=±2.5.

**Known limit, not a bank defect:** SE ≤ 0.65 is reached ~80–90% of the time at θ≈+2 but
rarely below θ≈0. With 4-option MCQs, `c`=0.25, so as ability falls P(correct) → 0.25 and the
response becomes mostly guessing — 3PL information genuinely vanishes at low θ. No bank fixes
this. Raising precision for weak candidates needs more items (`MAX_QUESTIONS`), fewer
guessable options (5 options → c=0.20), or accepting `SE_TARGET` > 0.65 at the low end.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `engine.py` | IRT/CAT core (EAP update, KL→Fisher selection) |
| `certainty.py` | Combined assessment certainty % |
| `selection_pipeline.py` | LLM procedural + deterministic item selection |
| `llm_client.py` | OpenAI client for procedural selection |
| `tracing.py` | Optional Langfuse trace events for approach comparison |
| `bank_synth.py` | Gap-fill only when a bank lacks coverage |
| `build_enriched_bank.py` | Assigns per-item `a`/`b`/`c`; rebalances key positions (seeded) |
| `validate_bank.py` | Gates schema, ladder, information coverage, and cueing. Exit 1 on failure |
| `simulate_cat.py` | Replays the real engine against known-θ candidates to score recovery |
| `assessment.log` | SELECT/UPDATE/SYNTH traces |
