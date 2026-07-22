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

**Setup** — two steps.

1. **Main competencies.** Ten AI/ML engineer competencies (`C1`..`C10`), derived from the
   bank. Select one or more; sub-competencies are never chosen by the candidate.
2. **Self-rating.** 1–5 per selected competency, with high or low confidence. The rating
   seeds the starting estimate; the confidence sets how wide it is. Neither is ever
   reported as a measurement.

Engine controls live behind **Tester options**, not beside the self-rating — an examinee has
no business choosing how their own questions get selected. They stay visible in the sidebar
during a run, locked, because selection policy is fixed when the session begins and a tester
should be able to see which policy a running session is under.

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

## Running the examples before submitting

A coding question offers **Run example cases** alongside **Submit solution**. The two are
deliberately separate: one changes the measured ability and the other cannot.

- **Public cases only.** Every code item publishes 3–4 examples and hides the rest. The
  filter is in `code_adaptive/trial.py`, not in the UI, so a UI change cannot widen it —
  the hidden cases are the whole reason a submission cannot be tuned to the examples.
- **Nothing is graded.** A trial returns a report, never a graded outcome, and there is no
  code path from it into the learner model. `CODE_TRIAL_RUNS_PER_QUESTION` bounds how many
  runs a question allows, so the feature stays a check rather than a search procedure.

Without this, the first time a candidate's code ever executes is the moment it is graded,
and a typo they would have caught in five seconds is measured as not knowing the material.

## Tracing

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (Streamlit Cloud: *Secrets*) and every
model call is traced as a generation, grouped by assessment session id, tagged with the
track, the variable under test and the item. `LANGFUSE_HOST` and `LANGFUSE_ENVIRONMENT`
are optional.

Tracing is **never load-bearing**. A missing package, an unset key, an unreachable
collector or a moved API all mean *no tracing* and change nothing else — `Diagnostics`
reports whether it is on. **Candidate source code is never sent**: a submission is a
person's work, and the item id, score and test counts answer every question monitoring
exists to answer.

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
