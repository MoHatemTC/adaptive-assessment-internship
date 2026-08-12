# `cat_engine/evaluation/reports/`

The written findings of the propagation study. Markdown, meant to be read in order.

Each names the run artefacts it draws on — under `../runs/` — with the exact cohort
filename, SHA-256 and persona, so a claim can be traced to the sessions that produced it.

| File | What it says |
|---|---|
| `baseline_report.md` | The C-shipped baseline, before remediation. |
| `baseline_comparison.md` | Baseline against the expanded independent cohort. |
| `final_report.md` | The conclusion, and which artefacts support it. |
| `issues.md` | The issue log — what was found, where, and whether it was fixed. |

The headline finding lives in `docs/preregistration/S1_Screening_Report.md`: corroboration,
the plan's main lever, is inoperable on this bank because the graph is a forest of chains
with every parent having exactly one direct child. That is a graph-authoring finding rather
than a tuning one.
