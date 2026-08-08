# Adaptive Competency Assessment — orchestrated engine

An agent-driven CAT engine that measures a candidate across many competencies using
**multiple-choice, coding, and open/voice items in the same session**, choosing whichever item — of
whichever modality — will narrow the weakest estimate fastest, and finishing each
competency as soon as it is measured.

Open/voice answers prefer **Gemini Live** through LiteLLM. A deployment may explicitly
enable typed fallback with `ALLOW_TEXT_FALLBACK=true`, which keeps remote and accessibility
testing possible when realtime audio is unavailable. Picker and open rubric grading also
go through LiteLLM.

Banks and rubrics live under `backend/app/data/` and are paired through the bank registry.
The original `DA`, `PY`, and `AIE` banks remain available; `AIE-JR-V3` and `JAI-600` are
additional human-test banks. See `streamlit/README.md` for their calibration limitations
and local/hosted setup.

```text
backend/
  app/
    config/settings.py               cat_* (MCQ), code_* (code), orchestrator_*
    schemas/
      orchestration.py               BankItem, VariableState, AssessmentState, reports
      adaptive.py · code_adaptive.py the two engines' own boundaries
    services/
      adaptive/                      MCQ engine — 3PL, EAP, KL/Fisher, convergence
      code_adaptive/                 code engine — sandbox, static analysis, scoring
      orchestrator/
        outcome.py                   GradedOutcome + the fractional theta update
        calibration.py               modality parameters -> theta  (PROVISIONAL)
        bank.py                      unified bank + information-parity diagnostic
        variables.py                 Examinee Variables, one latent ability each
        picker.py                    Picking Agent — KL->E[Fisher], mixed modality
        queue.py                     one pending candidate per open variable
        grader.py                    Grader Agent — routes by modality
        orchestrator.py              the loop
        prompts.py                   the single delegated decision
    data/question_bank*.json         registered assessment banks (old and new)
  scripts/build_unified_bank.py      fuses both source banks; --check verifies
  tests/                             deterministic, API, and Streamlit regressions
```

## The loop

```text
seed → fill queue → choose variable → present → grade → update → finalise → repeat
```

1. **Seed** — one θ posterior per variable, from intake or flat.
2. **Fill queue** — for each **open** variable, the Picking Agent chooses one candidate.
   Finalised variables are skipped entirely: no pick, no model call.
3. **Choose variable** — the lowest-certainty open variable with a candidate ready.
4. **Present → Grade** — the Grader Agent routes by modality and returns `GradedOutcome`s.
5. **Update** — one fractional θ update per outcome. A code submission updates *several*
   variables, because it genuinely evidences several.
6. **Finalise** — per variable, independently. On finalisation the queue slot is released.
7. **Repeat** until every variable is finalised, or the budget runs out.

## The one idea that makes it work

Every modality reduces to the same statement:

```python
GradedOutcome(variable, score, weight, confidence)
```

and every outcome updates the same posterior through one likelihood:

```text
L(θ) = [ P(θ)^s · (1 − P(θ))^(1−s) ] ^ w
```

Two properties earn this its place:

**It reduces exactly.** At `s ∈ {0,1}, w = 1` it *is* the MCQ engine's Bernoulli
likelihood, term for term. The MCQ path is unchanged by construction, and
`test_orchestration.py` asserts **float equality** against `irt.posterior_update` to keep
it that way. If that test ever fails, unifying the scale has silently altered a shipped,
measured engine.

**Zero weight means zero update.** `w = 0` makes `L` identically 1. The rule that an
infrastructure failure must never move a candidate's estimate now falls out of the
arithmetic instead of being special-cased — it cannot be forgotten at a call site, because
there is nothing to forget.

## Who decides what

| Decision | Owner |
|---|---|
| MCQ grading | **code** — exact index comparison |
| Code grading | **code + LLM** — tests 60%, static 15%, model 25% (the measured split) |
| Ability estimate | **code** — Bayes on a θ grid |
| Which variable to probe | **code** — lowest certainty |
| When a variable is finished | **code** — precision, band stability, or budget |
| Which items are viable, and their rank | **code** — KL early, E[Fisher] later |
| **Which viable item to administer** | **LLM** — from a code-ranked shortlist |
| Interpreting a wrong answer | **LLM** — misconception diagnosis |

The model picks from a shortlist the engine ranked and interprets code. It does not grade
multiple choice, write an estimate, choose a variable, or stop an assessment. A pick below
75% of the best available information is overridden; any model failure falls back to the
engine's own choice.

## Calibration — the one provisional part

MCQ items carry `a, b, c` calibrated against real responses. **Code questions do not.**
They carry an authored mastery-scale difficulty, mapped onto θ in `calibration.py`:

- `b_θ = logit(difficulty)` — the θ at which P = 0.5 under a logistic link. The bank's
  authored [0.15, 0.70] maps to [−1.73, +0.85], inside the MCQ items' band.
- `a_θ = discrimination` passes through; `c = 0` — nobody guesses their way to a passing
  test suite.

This is a **modelling decision, not a derivation**, confined to one module so real
calibration later touches one file.

`UnifiedBank.information_parity()` guards it, and separates the two reasons a modality can
lose a ranking:

| | meaning | action |
|---|---|---|
| `rarely_selected` | loses *with* loading applied | expected — a question loading 0.2 on a variable genuinely tells you less |
| `miscalibrated` | loses even at full loading | the item parameters are wrong |

On the shipped bank: **9 of 15** mixed variables rarely select code (correct — those code
questions only lightly load), and **1** is miscalibrated — `T1.4`, where the *MCQ* items
are intrinsically weaker than the code ones. A test pins that count so a regression shows
up as a change.

## The bank

One file, one envelope, modality payload nested. `cat` is **always on θ** — the invariant
that makes cross-modality ranking valid.

```json
{"item_id": "code_q_003", "modality": "code", "status": "active",
 "competency": "Python & Software Engineering",
 "sub_competency": "T1.4 · Error handling & robustness",
 "measures": [{"variable": "T1.4", "weight": 0.6}, {"variable": "T1.1", "weight": 0.4}],
 "cat": {"a": 1.35, "b": -0.2007, "c": 0.0},
 "code": {"function_name": "...", "tests": [...], "rubric_criteria": [...]}}
```

`measures` is the architecture's *required variables*. Both source banks already used the
same `competency` and `sub_competency` strings, so the taxonomy needed no migration —
`sub_competency` was already the join key. Open-ended adds `"modality": "open"` and needs
no envelope change.

Rebuild and verify:

```bash
cd backend && PYTHONPATH=. python scripts/build_unified_bank.py         # rebuild
cd backend && PYTHONPATH=. python scripts/build_unified_bank.py --check # verify
```

## Usage

```python
from app.services.orchestrator import Orchestrator, GraderAgent, JsonUnifiedBank
from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository

engine = Orchestrator(JsonUnifiedBank(),
                      GraderAgent(CodeAdaptiveSession(JsonQuestionRepository())))
state = engine.begin(["T1.1", "T1.4", "T2.1"], intake={"T1.1": 3})

while True:
    state = await engine.fill_queue(state)
    stop, reason = engine.should_stop(state)
    if stop:
        break
    item, candidate = engine.next_item(state)
    # present item.payload; collect an option index (mcq) or source text (code)
    state, graded = engine.record_response(state, item, response)

report = engine.summarise(state, reason)
```

`AssessmentState` is serialisable and the orchestrator holds nothing between calls, so an
assessment can span HTTP requests and resume on a different worker.

## Tester harness

A Streamlit UI supports candidate-safe human testing by default and a trusted, instrumented
tester mode. See `streamlit/README.md`.

```bash
./scripts/run_streamlit.sh
```

## Tests

```bash
cd backend && PYTHONPATH=. pytest
```

The default suite makes no billed model or sandbox calls. It covers binary-update identity,
fractional updates, calibration mapping, parity diagnostics, queue/finalisation invariants,
mixed-modality sessions, API boundaries, and Streamlit flows. Sandbox infrastructure
failures are explicitly tested to ensure they move no candidate estimate.

## Calibration status

The engine supports MCQ, code, and open/voice items. Operational score bands remain
provisional until independent response data passes the repository's psychometric and
human-grader gates. The two imported human-test banks are particularly explicit about this:
their CAT parameters are traceable syntheses from semantically related prior-bank strata,
not empirical calibration of the new item wording.
