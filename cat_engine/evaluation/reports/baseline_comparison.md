# Baseline and remediation comparison

## Paired C-shipped smoke

The fresh paired-truth DGP-2/P01 smoke contains 100 candidates and 300 candidate-main units.
It is adequate for release smoke and for finding large defects; it is not a release-scale
confidence study.

| Metric | Baseline | Post-fix | Gate |
|---|---:|---:|---:|
| Exact band accuracy | 61.67% | 62.00% | harness quality bar 80% |
| Marginal reliability | 0.947 | 0.919 | >= 0.85 |
| SE calibration | 1.325 | 1.066 | 0.95-1.10 |
| 95% interval coverage | 87.0% | 95.67% | 93-97% |
| Direct coverage | 100% | 100% | 100% |
| Modality compliance | 96.33% | 100% | >= 99% |
| Questions, mean / median | 23.66 / 24 | 26.71 / 27 | descriptive |
| Duration P50 / P90 | 53.54 / 83.55 min | 59.13 / 84.58 min | P90 <= 90 min |

The expanded four-cohort cycle reports pooled interval coverage of 94.75%, with every
cohort inside 93-97%. Exact-band accuracy remains 61.00-64.33%, and 21.67-27.00% of
candidate-main units reach the question budget.
All operational decisions are now explicitly provisional regardless of model convergence.

The expanded artifacts are under `runs/codex_cycle4_fixed`; every manifest records the exact
cohort filename, SHA-256, and persona. This also closes an analyzer bug that previously
selected the lexicographically last persona whenever several files shared a DGP label.

## Experimental arms

No valid Random/Fisher/KL/current/CFAT paired selector benchmark exists in the checked-in
artifacts, so selector non-inferiority and regret comparisons are `NOT_RUN`. CFAT is not
implemented evaluation-only. It would be misleading to turn the existing graph-propagation
sweeps into a selector comparison: those runs force-enable otherwise inert edges and measure
a different intervention.

The historical propagation study is nevertheless useful safety evidence. With edges forced
on it observed pooled wrong-inference rates around 15-17% in DGP-2, and 7.1% even with a
perfect graph plus grader noise, against a 3% gate. This supports the current decision to keep
inference and blocking disabled; it does not rescue the shipped measurement failures.

Before any selector claim, run the same frozen cohort through Random, Fisher, KL,
information-per-minute, CFAT, CFAT-per-minute, and constrained CFAT arms and use a
candidate-clustered paired bootstrap with a pre-registered two-point non-inferiority margin.
