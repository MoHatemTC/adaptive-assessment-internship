# Unified CAT backend (mcq + code + open/voice)

This branch runs a single combined CAT backend and a single canonical bank:

- **Backend**: `voice_assessment/app/main.py` (FastAPI)
- **Bank**: `latest banks and rubric/python_exam_bank_expanded.json`

All modalities (`mcq`, `code`, `open`) flow through the same orchestrator loop.
Open/voice grading remains LiteLLM-based (transcribe + rubric evaluation).
Gemini Live is optional and only used by realtime interview/chat endpoints.

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

## Run (single backend)

```bash
cd voice_assessment && source .venv/bin/activate
./scripts/run_backend.sh        # http://127.0.0.1:8765

# Unified CAT web UI
open http://127.0.0.1:8765/cat
```

The `/cat` page creates CAT sessions via `/api/cat/*` and dynamically renders
the widget for the current modality:

- MCQ radio selection
- Code editor submission
- Open/voice recording (multipart audio) or transcript fallback

## Tests

```bash
cd voice_assessment
pytest -q
```

## Layout

- `app/main.py` — single backend entrypoint for CAT + live routes
- `app/services/orchestrator/` — shared CAT loop for all modalities
- `app/services/code_adaptive/` — code grading/evidence pipeline
- `app/services/voice/` — open rubric evaluation + evidence projection
- `app/services/voice_live/` — live interview/chat transport + LiteLLM transcription
- `../latest banks and rubric/python_exam_bank_expanded.json` — canonical mixed bank

Compatibility note:

- `app/data/question_bank.json` is no longer the runtime source of truth on this branch.
- Both orchestrator bank and code question repository read from
  `latest banks and rubric/python_exam_bank_expanded.json`.
