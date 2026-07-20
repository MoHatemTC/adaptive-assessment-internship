# Human test protocol — three CAT approaches

## Run it

From the repo root, on any branch — the script resolves each approach to its own
worktree via `git worktree list`:

```bash
./run_human_test.sh            # model from each worktree's .env (kimi-k2.6)
./run_human_test.sh kimi-k2.5  # same test, other model
./run_human_test.sh --stop
```

First run needs a Streamlit env and per-worktree credentials; the script prints the
exact commands if either is missing.

| | Approach | Math | Picks item | Stops |
|---|---|---|---|---|
| :8501 | 1 — code math, LLM picks | code | **LLM** (from a code-ranked shortlist) | code |
| :8502 | 2 — LLM math, code picks | **LLM** (code-guarded) | code | code |
| :8503 | 3 — full LLM controller | **LLM** | **LLM** (from the whole pool) | **LLM** |

Same bank (`enriched_bank_cat.json`), same model, same competencies. Only the controller
differs — that is what makes the comparison mean anything.

## What the automated scorecard already answers

Do not spend human time re-measuring these; `score_approach.py` runs 40 simulated
candidates per branch against a coded arm on common random numbers and reports ability
recovery, selection agreement, information regret, |Δθ̂| vs the coded EAP, stop-rule
agreement, sub-competency coverage, fallback/invalid rate and metered cost.

Human testing is for what simulation cannot see.

## What only a human can judge

**1. Are the questions coherent to answer?**
Simulated candidates never read the stem. Approaches 1 and 3 let the LLM *rephrase* the
selected question. `rephrase_guard.py` blocks answer leaks and polarity flips
mechanically, but it cannot tell you whether the rewrite is still a good question. Watch
for: a stem that now telegraphs its answer, a code fragment mangled into prose, a
question that reads as harder or easier than the original.

**2. Does the difficulty trajectory feel adaptive?**
Answer deliberately — all-correct, then all-wrong, then honestly. The test should chase
you. Approach 3 is the one to watch: it picks from the whole pool with no code-enforced
KL→FI ranking, and the scorecard measures *what* it picked, not whether the sequence
felt like a coherent assessment.

**3. Is the reported result defensible?**
At the end, each app shows level, band, percentile and certainty. Ask: would you show
this to the candidate? Approach 3 owns its own stop decision — check whether it quits
while still visibly uncertain.

**4. Does it survive being used by a person?**
Refresh mid-test. Start a second assessment without resetting. Answer very fast. These
paths do not exist in simulation. (The counter-reset bug fixed on approach 3 was exactly
this class: only visible on a *second* assessment in the same session.)

## Per-branch: check the audit panel

Each app has a sidebar audit expander. It is the branch's own self-report — read it
against what you just experienced.

- **Approach 1** — "LLM selection audit": selections, fallbacks, **procedure deviations**
  (LLM picked something other than the engine's own choice). A nonzero deviation count
  with a good-feeling test is interesting, not alarming — check the information regret in
  the scorecard before treating it as a fault.
- **Approach 2** — "math audit": mean and worst |Δθ̂| vs the coded EAP, invariant
  violations, and the **fallback-rate banner**. If it says 100% fallback, you tested the
  engine, not the model — the run is void.
- **Approach 3** — "controller audit": invalid steps, mean/worst |Δθ̂|, stop decisions.
  An invalid step **ends the competency** here; there is no coded substitution by design.
  If a session dies mid-way, that is the branch behaving as specified, and it is a
  finding worth recording.

## Record per session

Approach · competency · how you answered (honest / all-correct / all-wrong) · questions
asked · final level + certainty · anything that felt wrong · audit-panel numbers.

Langfuse traces are tagged `run:human-test` plus the approach id, so a session can be
pulled up afterwards.

## Known caveats before you start

- **Latency is real.** kimi is a reasoning model: ~20s per LLM call. Approach 3 makes two
  calls per question, so expect ~40s between questions there. This is a genuine product
  finding, not a bug — but it is the single biggest thing a human will notice, and it
  will dominate subjective impressions unless you account for it.
- **Cost is unpriced.** The gateway refuses `/model/info` to this key, so token counts
  are metered but dollars are not computed. Set `LLM_PRICE_INPUT_PER_1M` and
  `LLM_PRICE_OUTPUT_PER_1M` in `.env` if you have the contracted rate.
- Each worktree needs its own `.env` (gitignored). The launcher refuses to start an app
  without one rather than let you test a branch that silently fell back to coded logic.
