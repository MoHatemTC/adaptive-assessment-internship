# `cat_engine/evaluation/runs/`

**Evidence, not output. Do not delete.**

18 MB of simulation artefacts, tracked deliberately. Every report in `../reports/` and every
number in `docs/evidence.md` traces to a run here, by cohort filename, SHA-256 and persona
recorded in each `*.manifest.json`.

This is why they are tracked while `eval-results/` is gitignored: `eval-results/` is
regenerable scratch from exploratory sweeps, and these are the specific runs a published
claim points at. Regenerating them would produce different session ids and different
timestamps, so a citation could no longer be checked against the thing it cites.

| Directory | What it is |
|---|---|
| `codex_baseline/` | The C-shipped baseline, DGP-2. |
| `codex_postfix/` | The same after remediation. |
| `codex_cycle2/`, `codex_cycle3_fixed/`, `codex_cycle3_trace/` | The intermediate cycles, per DGP and persona. |
| `codex_cycle4_fixed/` | The expanded independent-cohort cycle. |

Each holds `*.jsonl` (one line per simulated session), `*.manifest.json` (the exact
configuration and cohort), and a `report/` of the analysis.
