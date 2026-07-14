# Masaar MCQ CAT Test Harness

Standalone Streamlit app to validate the Masaar IRT/CAT adaptive engine on MCQ-only questions.

## Quick start

```bash
cd masaar-mcq-cat-test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

By default the app loads **`enriched_bank.json`** (122 unique items across 5 competencies).
You can also upload any bank matching the JSON format below.

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
pip install -r requirements.txt
streamlit run app.py
```

Setup screen shows connection status and a toggle for LLM selection.
If OpenAI fails, the app falls back to the same procedure executed deterministically in Python.

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
| `enriched_bank.json` | **Preferred** — 122 unique MCQs (Excel + new), 5 tracks, full CAT parameter variety |
| `sample_bank.json` | Small 21-item smoke bank (2 competencies) |

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `engine.py` | IRT/CAT core (EAP update, KL→Fisher selection) |
| `certainty.py` | Combined assessment certainty % |
| `selection_pipeline.py` | LLM procedural + deterministic item selection |
| `llm_client.py` | OpenAI client for procedural selection |
| `bank_synth.py` | Gap-fill only when a bank lacks coverage |
| `assessment.log` | SELECT/UPDATE/SYNTH traces |
