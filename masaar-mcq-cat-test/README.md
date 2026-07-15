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

## LLM math with coded selection (OpenAI)

MCQ **grading stays deterministic** (exact index match). The **LLM updates theta/SE/certainty after each answer**, then code selects the next item:

1. Code grades the submitted answer.
2. Previous theta/SE, item IRT parameters, and correctness are sent to OpenAI.
3. LLM returns updated `theta_hat`, `SE`, certainty, and math notes.
4. Code validates/clamps the LLM math output.
5. Code picks the next question deterministically using KL/Fisher selection.

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

Setup screen shows connection status and a toggle for LLM math.
If OpenAI fails, the app falls back to coded EAP math and deterministic selection.

For Streamlit deployment, use `masaar-mcq-cat-test/streamlit_app.py` as the main file path. See `DEPLOYMENT.md`.

## Langfuse tracing

When Langfuse keys are present in `.env`, the app records comparable trace events for:

- session start
- next-item selection
- LLM selection responses
- answer updates
- competency finalization

Each trace includes `approach_id`, `math_actor`, and `selection_actor` metadata so the three experiment branches can be compared in Langfuse.

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
| `tracing.py` | Optional Langfuse trace events for approach comparison |
| `bank_synth.py` | Gap-fill only when a bank lacks coverage |
| `assessment.log` | SELECT/UPDATE/SYNTH traces |
