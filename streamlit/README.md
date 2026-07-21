# Tester harness

A Streamlit UI for **checking** the orchestrated engine, not for candidates. Every number a
decision rested on is on screen.

```bash
pip install -r streamlit/requirements.txt
streamlit run streamlit/main.py
```

`streamlit/requirements.txt` is **self-contained** — it carries the engine's dependencies as
well as the UI's, because Streamlit Cloud installs exactly one requirements file: the one
beside the entry point. It never reads `backend/requirements.txt`. A test asserts the two
agree, so drift fails the suite rather than the next deployment.

**Deploying to Streamlit Cloud:** set the main file to `streamlit/main.py`, and pin the
Python version in *Advanced settings*. Cloud currently defaults to 3.14, where an unbounded
resolve pulls pandas 3.x and crashes on import; the upper bounds here prevent that, but
pinning the interpreter removes the whole class of surprise.

Nothing in `backend/` changes — the UI reads the engine's public API and recomputes the
rest with the engine's own functions.

## Screens

**Setup** — pick the competencies to assess, self-rate each 1–5, and say whether that
rating is high or low confidence. The rating seeds the starting estimate; the confidence
sets how wide it is. Neither is ever reported as a measurement. Competencies are labelled
with how many MCQ and code items they carry, because the ones carrying both are where
cross-modality selection is worth testing.

**Assessment** — the question, plus five panels:

| Panel | Shows |
|---|---|
| **Queue** | one row per open competency: the queued item, its CAT parameters (a, b, c), the criterion in force, information, best available, regret, whether the engine or the model picked it, and why. Expand a competency for its full shortlist with each candidate's information, difficulty, discrimination and loading — and which one was administered. |
| **Mathematics** | one row per competency updated by each answer: score, weight, ability and standard error before → after, confidence, level and band, and Fisher information after. Downloadable as CSV. |
| **Trajectory** | ability per competency over the session, confidence per competency over the session, and a table of where each stands — including which criterion applies next and whether it has finalised. |
| **Engine log** | what the engine logged while deciding. A session that ran entirely on deterministic fallbacks looks identical to one that used the model unless these are visible. |
| **Diagnostics** | the configuration in force with what each setting means, bank coverage, and the information-parity report separating "rarely selected" (expected — low loading) from "miscalibrated" (a real problem with item parameters). |

**Report** — on convergence the session ends and every panel is retained. The report
separates a measured result from a budget outcome: a band from a competency that ran out of
questions is marked *provisional*, and `converged` is true only for a stop earned on
precision or a settled band.

## Two things worth knowing

**Selection algorithm is shown per competency.** `KL` for the first three observations,
`E[Fisher]` after, with the reason given in the Queue panel. The `FI after` column is always
Fisher information — not whichever criterion the phase happens to use, which would label one
quantity with another's name for the first three answers of every competency.

**Without an `E2B_API_KEY`, coding questions still get selected and shown**, but running one
reports a sandbox failure. That correctly moves no estimate, and is itself worth testing.
Without a `LITELLM_API_KEY` the picking agent runs deterministically — which is also the
fallback whenever the model is unavailable or returns something unusable.

## Tests

```bash
cd backend && PYTHONPATH=. pytest tests/test_streamlit_ui.py
```

Drives the whole flow through Streamlit's own test runner with the sandbox and model
stubbed: setup, answering both modalities, and the final report. Two are regressions for
bugs this harness found — a UI module named `app.py` shadowing the backend's `app` package,
and dataframe columns mixing ints with an em-dash placeholder, which fails Arrow
serialisation and silently forces a coerced render on every rerun.
