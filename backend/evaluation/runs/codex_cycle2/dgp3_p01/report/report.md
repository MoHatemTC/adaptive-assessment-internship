# Approach B vs Approach C — evaluation results

Cohort size per arm: 100 simulees (300 (candidate, main) units).

Gate tiers follow the validation document's B4: one primary endpoint at alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. At 18 untiered statistical gates the chance of a spurious failure would be 60.3%.

## 1. DGP-0 — the null control

No prerequisite structure exists in this data. Any Approach C benefit here is an artefact of the generator, not of the graph.


## 2. Absolute quality gates (per arm, no comparison)

The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C relative, so both approaches can pass by being equally unfit. These apply to whichever approach ships.

### DGP-3

| gate | rule | C-shipped |
|---|---|---:|
| ABS-01 | >= 0.80 overall | 0.6333 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.5309 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.8931 PASS |
| ABS-04 | no true-theta decile below 0.65 | 0.3667 FAIL |
| ABS-05 | >= 0.75 | 0.7142 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.0577 PASS |
| ABS-07 | P90 modelled duration <= 90 minutes | 84.9000 PASS |

## 3. Primary endpoint — exact-level accuracy, common band scale

| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|

## 4. DAG safety — one-sided 95% upper bounds

Verified against the cohort's known node truth, so every inference and every block is checked. A live study would need ~100-155 verified events per gate even at a true rate of zero (validation A1).

### DGP-3 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 1022 / 1022 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 2879 | 0.0000 | 0.0010 | 0 events | PASS |
| U-02 | 3 | 9 / 211 | 0.0427 | 0.0732 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 211 | 0.0000 | 0.0141 | 95% UCB < 2% | PASS |

## 5. Descriptive — reported with no verdict

### DGP-3

| metric | C-shipped |
|---|---:|
| theta_rmse | 0.7699 |
| theta_mae | 0.6525 |
| within_one_level | 0.9933 |
| questions_per_candidate | 27.6900 |
| duration_minutes | 58.5000 |
| heldout_auc | 0.9144 |
| heldout_brier | 0.1174 |
| heldout_log_loss | 0.4071 |
| heldout_ece | 0.0479 |
| final_se_median | 0.7309 |
| interval_coverage_95 | 0.9367 |
| information_realization_ratio | 1.0358 |
| modality_blueprint_compliance | 0.9967 |
| coverage_satisfied_rate | 1.0000 |

## 6. Power — what n this study actually needs

| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |
|---|---:|---:|---:|---:|---:|---:|
