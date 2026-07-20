# Code-question adaptive assessment — approach study

Adaptive testing for **coding** questions. A binary pass/fail is not enough for code: two
learners can fail the same problem because one missed an edge case and the other chose the
wrong data structure, and those call for different next questions.

So the system combines deterministic execution evidence with LLM interpretation of that
evidence — and this repository exists to measure **how much interpretation to delegate**.

## The three branches

They are identical except for one table, `code_evaluation/scoring.SOURCE_WEIGHTS`, so any
measured difference is attributable to that and nothing else.

| Branch | Who scores each criterion |
|---|---|
| `code-cat-approach-A` | **Objective only.** Tests and static analysis. The model is never called. |
| `code-cat-approach-B` | **Test-anchored LLM.** The model interprets; functional correctness stays pinned to execution. This is §26 of the architecture doc. |
| `code-cat-approach-C` | **LLM-led.** The model scores every criterion; only contradictions of execution are blocked. |

`code-cat-base` is the shared parent. Branch B sits on it, since B is the doc's pattern
and therefore the default.

## What is never delegated

Grading is execution. The model may interpret **why** a submission failed; it may not
decide **whether** it did. That anchor is enforced twice — once when the model's reply is
validated, once when sources are combined — because a model asserting that failing code
works is the failure mode that puts a wrong score on a real candidate.

| Stage | Owner |
|---|---|
| Compilation, test execution, runtime limits | E2B sandbox |
| Passed-test ratio, per-test outcomes | code |
| Structural signals (loops, guards, hardcoding, mutation) | code |
| Competency interpretation, misconception diagnosis | **LLM**, evidence-constrained |
| Criterion score aggregation | code |
| Learner competency update | code |
| Stopping | code |
| Candidate filtering and ranking | code |
| Choice among the Top-K shortlist | **LLM**, constrained |
| Fallback, audit | code |

Learner code is untrusted input. It only ever runs inside an E2B sandbox with no network
access, and the sandbox is destroyed after each submission.

## Ground truth, and why it had to be built

For multiple-choice items, accuracy is measurable: a simulated candidate has a known
ability and a response model to generate answers from. Code has no equivalent — a
submission is not drawn from a distribution.

So ground truth is **constructed**. `bank/` holds 10 questions with 84 tests, and
`bank/seeded/` holds 60 submissions each carrying a declared defect:

```
correct · missing_empty_guard · off_by_one · wrong_algorithm · syntax_error · hardcoded
```

Every seeded submission declares which tests it must fail, which competencies should read
as weak, which should still read as strong, and which misconception it displays. Those
declarations are verified by executing every artifact — the reference solutions all pass,
and every defect fails exactly the tests it claims. An approach is then scored on whether
it recovers what was seeded.

## Measuring an approach

```bash
python score_approach.py --approach B --out runs/
python score_approach.py --approach A --limit 12    # A makes no LLM calls
```

Reported per approach:

- **separation** — do the seeded-weak competencies score *below* the seeded-strong ones?
  The headline. An evaluator that cannot separate a boundary-condition defect from correct
  boundary handling is not diagnosing anything, whatever its scores look like. Reported as
  a margin, not a boolean, so a near-miss is distinguishable from an inversion.
- **misconception** — recall and false positives against the seeded code.
- **integrity** — anchoring violations, and any disagreement between execution and the
  bank's declared ground truth. *A non-zero mismatch means the bank is wrong, not the
  approach* — fix it before reading anything else.
- **cost** — tokens and latency per submission.

## Running the app

```bash
pip install -r requirements.txt
cp .env.example .env      # add E2B_API_KEY and LiteLLM credentials
streamlit run app.py
```

Two modes:

- **Assessment** — take the test as a candidate would, to judge whether the questions and
  the difficulty trajectory feel right. No automated metric sees that.
- **Inspector** — run any seeded submission and see every layer: raw execution, static
  signals, the model's diagnosis, the criterion arithmetic, and what reached the learner
  model. The design rests on the claim that the model interprets without controlling the
  score; this makes the claim checkable rather than asserted.

## Learner model

A Beta belief over mastery per competency, updated by evidence weight. §11.1 names GPCM
and the graded response model as the destinations and they are the right ones — but both
need calibrated item parameters, which need real learner responses, which do not exist
yet. Beta is honest in the meantime: a real posterior with a real variance, whose standard
error falls only as evidence accumulates. It can be replaced without touching anything
upstream of `competency_state.update`.

Competencies with no evidence report mastery as `—`, never 0.5. A Beta(1,1) prior has a
mean of exactly 0.5, which would otherwise read as a measured mid-level result rather than
as absence of evidence.

## Not yet implemented

- §11.1 GPCM / graded response calibration (Phase 5) — needs real learner data.
- Exposure and overlap limits (§14) — the bank is too small for them to bind.
- `MUTATES_INPUT` and `QUADRATIC_WHEN_LINEAR_POSSIBLE` are detected structurally but not
  seeded, because a return-value-only test harness cannot observe either, and labelling a
  seeded defect with them would have made the ground truth false.
