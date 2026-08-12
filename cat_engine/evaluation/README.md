# B/C evaluation harness

Executable core of `APPROACH_B_C_EVALUATION_METRICS_TEST_PLAN.md`, as amended by
`BC_Test_Plan_Validation_and_Amendments.md`.

The same file set ships on both approach branches. Everything Approach C adds is
feature-detected, so one runner produces joinable records from an engine that has a
competency graph and one that does not.

## What it runs

| Phase | Module | Answers |
|---|---|---|
| 0 | `bank_check.py` | Can this bank support the SE target, the coverage requirement and the modality blueprint at once? |
| 0a | `prestudy.py` | Band count x stopping rule, before the B/C experiment freezes them |
| 0b | `edge_validity.py` | P(pass child \| fail parent) per prerequisite edge, offline; plus the Layer 4 replay precondition |
| 0c | `make_cohort.py` | The frozen simulee cohorts, one per DGP arm |
| 1 | `../tests/test_bc_invariants.py` | INV-01..INV-06 and posterior isolation |
| 2b | `run_arm.py` at small n | The discordance psi that sets the real sample size |
| 3 | `run_arm.py`, `analyse.py` | The comparison, its gates, and its power |

## Running it

```bash
PYTHON=/path/to/python \
C_BACKEND=/path/to/cat-engine-graph-augmented/backend \
B_BACKEND=/path/to/cat-engine-voice-code-mcq-streamlit/backend \
OUT=eval-results \
./evaluation/run_all.sh 4000 42
```

Approach B and Approach C cannot be imported into one process — both define `app.*` — so
each arm runs as its own process against its own checkout, writing to a shared results
directory. That is why the cohort is a file: it is what makes two processes score the same
people.

## The four arms, and why there are four

The plan compares B against C. Between these two branches that comparison is confounded by
seven things section 6 puts on the freeze list — band boundaries, the stable-band SE
ceiling, difficulty corroboration, time-aware selection, the modality blueprint, the item
budget, and the picker's utility layer. A two-arm contrast would attribute all of it to the
DAG.

| arm | what it is |
|---|---|
| `B` | Approach B's branch, its own defaults |
| `C-off` | Approach C's code with the graph master switch off |
| `C-shipped` | Approach C's branch defaults: coverage gate on, propagation inert |
| `C-full` | inference, blocking, filtering, utility on, prerequisite edges force-enabled |

`B` vs `C-off` prices the branch divergence. `C-off` vs `C-full` prices the DAG, with
everything else identical by construction. Both are reported.

## The four DGP arms

`DGP-0` has no prerequisite structure at all and is the control: if Approach C shows a
benefit there, the benefit is an artefact of the generator. `DGP-1` gives the world exactly
the graph's edges. `DGP-2` drops a quarter of them from the truth and adds an equal number
the graph never asserted. `DGP-3` adds a slip ceiling and grader error.

## Design decisions worth knowing

**A response is a pure function of (simulee, item).** Not of the step, the session, or the
order. Approach C administers a different sequence by construction, so any generator
consumed in administration order would give the two arms different answers to the same
question and put sampling noise back into a paired design.

**Levels are compared on a common scale.** Approach B maps theta to a level by rounding
`3 + theta` into 1.0-wide bands; Approach C uses explicit 1.6-wide cuts. On the same
posterior those disagree about roughly a third of candidates — an order of magnitude more
than the 2pp margin M-03 is gated on. `bands.py` supplies one rule for both arms; each
arm's native level is reported beside it, descriptively.

**The clock is virtual.** The orchestrator reads wall clock, and a simulated session runs
in milliseconds, so under a real clock the 90-minute limit can never fire, the modality
time reservation never binds, and E-04 measures the simulator's speed. The clock advances
by each item's own expected duration instead.

**Node truth makes the safety gates computable.** In production, verifying an inference
costs a forced question, and the validation document's A1 shows that means ~100-155
verified events per gate even at a true rate of zero. The cohort file holds every node's
true mastery, so every inference and every block is verified for free. That is the reason
to run this layer before a pilot rather than after one.

**Gates are tiered.** One primary endpoint at alpha = 0.05; safety as one-sided upper
bounds; deterministic invariants as counts; everything else descriptive with intervals and
no verdict. Twenty-six untiered gates at 0.05 would fail an acceptable Approach C 74% of
the time by noise alone.

## What is simulated and what is real

Simulated: whether an answer is right, how many tests pass, what the rubric scores are.

Real: grading, criterion-to-competency projection, evidence weighting, the fractional
likelihood, the posterior update, item selection, convergence, the graph, the coverage
gate, and the report. Only the sandbox and the model are replaced, with the same two stubs
the repository's own test suite uses.

## Known limitations

- The code path submits one fixed source, so static analysis contributes a constant and a
  code score varies with the test outcome alone. This makes code slightly more reliable in
  simulation than in production, equally for both arms.
- Layers 2, 5, 6 and 7 of the plan — grader gold set, DeepEval, shadow, pilot — need
  expert labels, a judge model and real candidates. None is runnable here, and none is
  faked. `edge_validity.py` reports the Layer 4 precondition rather than the replay.
- C-DAG-13 (cross-main leakage) is not computed: it needs the shared-node list carried
  through to the cohort, which the current cohort schema does not hold.

## C-shipped release audit and remediation

This directory contains the release audit of commit
`0f86002f15378daef6dab7a233f5ccec0d0e4810` and its production-code remediation. Evaluation-only
release-spec tests live in `invariants/test_c_shipped_invariants.py`; paired pre-fix and
post-fix DGP-2 artifacts live in `runs/codex_baseline/` and `runs/codex_postfix/`. Decision
artifacts are under `reports/`.

```bash
cd backend
python -m pytest evaluation/invariants/test_c_shipped_invariants.py -q
python -m evaluation.run_arm --arm C-shipped \
  --cohort eval-results/cohorts/cohort_DGP-2_P01_n2000_seed42.json \
  --out evaluation/runs/codex_postfix --limit 100 --max-steps 60 --trace-every 10
python -m evaluation.analyse --runs evaluation/runs/codex_postfix \
  --cohorts eval-results/cohorts/cohort_DGP-2_P01_n2000_seed42.json \
  --out evaluation/runs/codex_postfix/report
```

Pass an exact cohort file whenever the cohort directory contains more than one persona for
the same DGP. The analyzer now rejects that ambiguity instead of silently choosing the
lexicographically last cohort.

The baseline's immediate deterministic/security `BLOCK` is resolved. A final production
classification remains withheld because missing human-gold, fairness, full adversarial,
report, and release-scale evidence is recorded as `NOT_RUN` or `NOT_ESTIMABLE`; it is never
imputed.
