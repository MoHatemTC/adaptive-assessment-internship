# Adaptive Competency Assessment — combined engines

Two adaptive testing engines in one backend, ready for an orchestrator to sequence them.

- **MCQ** — 3PL item response theory, EAP on a fixed θ grid over [−4, 4].
- **Code** — candidate code executed in a sandbox, Beta posterior over mastery on [0, 1].

Both were built and chosen by measurement, each on its own branch
(`cat-engine-production`, `code-cat-engine-production`). This branch brings them together
unchanged so the orchestration layer can be built on top of two engines that already work,
rather than alongside two that are still moving.

```
backend/
  app/
    config/settings.py               both engines' policy, prefixed cat_* and code_*
    schemas/
      adaptive.py                    MCQ boundary: Item, AbilityState, CompetencyResult
      code_adaptive.py               code boundary: Question, SessionState, reports
    services/
      adaptive/                      MCQ engine — irt, convergence, bank, selection,
                                     llm, rephrase, prompts, session
      code_adaptive/                 code engine — irt, competency, execution,
                                     static_analysis, scoring, weights, evidence,
                                     llm_evaluator, prompts, selection, bank, session
      orchestrator/engine.py         the shared contract. No policy yet — see below.
    data/
      item_bank.json                 calibrated MCQ items
      code_bank.json                 25 coding questions, 218 tests
      rubrics/                       loose | mid | tight grading instructions
  tests/                             101 tests, no network, no sandbox, no database
```

## The design rule both engines share

**The model never writes a measurement.** It interprets evidence and picks from a
shortlist the engine has already ranked. Grading, ability estimation and stopping are
deterministic code in both modalities, so every number in a report is reconstructable from
inputs recorded beside it.

| Decision | MCQ | Code |
|---|---|---|
| Grading | code — exact index comparison | code — sandboxed execution |
| Ability estimate | code — EAP on a θ grid | code — Beta posterior |
| Stopping | code — SE target | code — SE target |
| Rank the viable items | code — KL early, E[Fisher] later | code — KL early, E[Fisher] later |
| **Which viable item to ask** | **LLM**, from the shortlist | **LLM**, from the shortlist |
| Interpreting a wrong answer | — | **LLM** — misconception diagnosis |
| Code quality | — | **LLM** — no objective ground truth exists |

The code engine's selection deliberately mirrors the MCQ engine's: KL information for the
first three items, posterior-expected Fisher thereafter. With no guessing parameter the
exact 3PL information reduces to `a²·P·(1−P)`, so the two are the same criterion on
different scales rather than two different ideas.

## What the orchestrator must decide

`services/orchestrator/engine.py` defines only what the two engines genuinely have in
common — stateless calls, serialisable state, `begin → next → record → summarise` — and
both satisfy it today. **It contains no orchestration policy on purpose.** These are the
questions the architecture has to answer, and none of them has a safe default:

**The scales do not combine.** MCQ estimates θ on [−4, 4] and stops at SE 0.65; code
estimates mastery on [0, 1] and stops at 0.15. Neither target means anything on the other
scale. Converting between them is possible — both are monotone in ability — but it is a
modelling decision that changes every band a candidate is shown, and it must be made
deliberately rather than implied by a helper.

**An item means different things.** An MCQ item carries `a`, `b`, `c` calibrated from real
response data. A code question carries an authored difficulty and discrimination with no
calibration behind them. Information computed from the two is not equally trustworthy.

**Evidence strength is uniform in one and weighted in the other.** Every MCQ answer counts
the same. Code evidence is weighted by what was actually demonstrated, and an
infrastructure failure carries zero.

**Which modality assesses which competency**, in what order, and whether a competency
measured by one is considered measured at all by the other.

Until those are settled, an orchestrator that treats a competency as known because either
engine touched it will overstate what has been measured — which is the specific failure
both engines are individually built to prevent.

## Tests

```bash
cd backend && PYTHONPATH=. pytest
```

101 tests. Measurement invariants for both engines, the scoring guards that each exist
because they failed once, session flow with every boundary stubbed, and the shared
contract. No network, no sandbox, no database.

## Configuration

See `backend/.env.example`. Settings are prefixed by which engine reads them — `cat_*` for
MCQ, `code_*` for code. The prefixes are not decoration: a standard-error target means
different things on the two scales, and sharing a name would invite exactly the confusion
the orchestrator has to avoid.
