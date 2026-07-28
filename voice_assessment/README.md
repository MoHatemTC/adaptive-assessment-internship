# Voice / open-ended adaptive assessment

Standalone CAT for open / voice items. Measurement substrate is vendored from
`cat-engine-combined-streamlit`. Evaluation is async (LiteLLM rubric grader);
`record_response` stays sync. **Gemini Live (`gemini-3.1-flash-live-preview`) is
used only for the voice interviewer** — never for picking or grading.

## Setup

```bash
cd voice_assessment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill keys
```

| Key | Used by |
|---|---|
| `LITELLM_*` | Picker + open rubric grader |
| `GEMINI_API_KEY` | Live interviewer only |
| `LANGFUSE_*` | Optional tracing |

## Streamlit tester + realtime Live

```bash
# Terminal 1 — realtime mic ↔ Gemini Live
cd voice_assessment && source .venv/bin/activate
./scripts/run_live_server.sh    # http://127.0.0.1:8765

# Terminal 2 — CAT harness
streamlit run streamlit/main.py
```

Choose **Realtime live interview** → Open realtime interview → speak continuously
in the embedded panel → **Finish answer** → **Grade finished interview**.

Recording is optional (checkbox) and for testing only; saved under `recordings/`.
Typed transcript and recorded Whisper modes remain as tester fallbacks.

## Tests

```bash
cd voice_assessment
pytest -q
```

## Layout

- `app/services/orchestrator/` — vendored CAT loop (+ open calibration / grader)
- `app/services/voice/` — rubrics, async evaluate, sync grade, session runner
- `app/services/voice_live/` — Gemini Live interviewer wrapper
- `app/data/question_bank.json` — 107 open items (from `ai_ml_engineer_open_bank.json`)
- `app/data/open_grading_rubric.json` — global grading framework
- `app/data/voice_rubrics/` — per-item rubrics (criteria + weights from the global frame)

Rebuild from source files:

```bash
python scripts/build_open_bank.py \
  --bank /path/to/ai_ml_engineer_open_bank.json \
  --rubric /path/to/ai_ml_engineer_open_grading_rubric.json
```

Adaptation notes: `open.question` is copied to `open.prompt`; `cat.a` is clamped to
`[1.8, 2.3]`; `cat.c` is set to `0.15` (open rubric floor) for the fractional update.
