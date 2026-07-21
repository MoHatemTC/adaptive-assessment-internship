# Deploying the three approaches to Streamlit Community Cloud

Deploy one app per branch, so all three run side by side against the same bank and the
same model and the only difference is the approach.

| Branch | App | Approach |
|---|---|---|
| `code-cat-approach-A` | code-cat-a | objective only — tests + static analysis, model never called |
| `code-cat-approach-B` | code-cat-b | test-anchored LLM |
| `code-cat-approach-C` | code-cat-c | LLM-led (the measured recommendation) |

## Per app

On https://share.streamlit.io → **New app** → **Deploy from existing repo**:

- **Repository** `MoHatemTC/adaptive-assessment-internship`
- **Branch** `code-cat-approach-A` (then `-B`, then `-C`)
- **Main file path** `code-cat-test/app.py`
- **Advanced settings → Python version** `3.12` — **not optional**, see below

## Secrets

Identical for all three. The branch pins the approach, so `CODE_CAT_APPROACH` is only
needed if you want to override it.

```toml
E2B_API_KEY = "e2b_..."
LITELLM_BASE_URL = "https://learner-os.sprints.ai/litellm/v1"
LITELLM_API_KEY = "sk-..."
LITELLM_MODEL = "kimi-k2.6"
LITELLM_TIMEOUT_SECONDS = "180"

# Optional — the branch already pins the approach.
# CODE_CAT_APPROACH = "C"
CODE_CAT_RUBRIC = "mid"
```

A `[cat]` table works too if you prefer grouping.

## Three things that will bite you

**Pin Python to 3.12.** Left unpinned, Cloud resolves 3.14, and a sibling deploy of the
MCQ app crashed there inside CPython 3.14's `dataclasses` while importing a module — a
race between `@dataclass` and Streamlit's hot reload. `requirements.txt` cannot pin the
interpreter; only the app's advanced settings can.

**Streamlit Cloud does not export secrets to the environment.** `st.secrets` is a TOML the
app reads, not environment variables. `cat/config.py` bridges them explicitly before
`Settings()` is constructed — without that the app comes up looking perfectly healthy and
fails on the first submission with no E2B key. If you fork or restructure, keep the
bridge.

**E2B needs outbound network.** Streamlit Cloud allows it, but every code submission
starts a sandbox, so a demo with several people submitting at once will open several
sandboxes concurrently against the same E2B quota. Approach A is the cheapest to demo: it
runs the sandbox but never calls the LLM.

## Cost per app, measured

| Approach | tokens / submission | LLM calls |
|---|---:|---|
| A | 0 | none |
| B | ~7,500 | 1 evaluation + 1 selection |
| C | ~6,900 | 1 evaluation + 1 selection |

kimi is a reasoning model at roughly 20s per call, so expect ~20-40s between submitting
code and seeing the diagnosis on B and C, and ~2s on A. That latency difference is the
most visible thing in a side-by-side demo and it is worth telling testers about up front,
or they will read it as A being better.

## What to look at, per app

The **Inspector** mode is the fastest way to see the difference: pick the same seeded
submission in all three apps and compare the "Model diagnosis" panel. On A it says the
model was never called; on B and C it names the misconception. That single comparison is
the 12% vs 100% recall result from `APPROACH_COMPARISON.md`, visible in one screen.

The **Assessment** mode is for judging whether the questions and the difficulty
trajectory feel right — which no automated metric measures.
