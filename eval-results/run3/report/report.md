# Approach B vs Approach C — evaluation results

Cohort size per arm: 1600 simulees (4800 (candidate, main) units).

Gate tiers follow the validation document's B4: one primary endpoint at alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. At 18 untiered statistical gates the chance of a spurious failure would be 60.3%.

## 1. DGP-0 — the null control

No prerequisite structure exists in this data. Any Approach C benefit here is an artefact of the generator, not of the graph.

| contrast | level accuracy ref | level accuracy test | degradation (pp) | questions ref | questions test | reduction |
|---|---:|---:|---:|---:|---:|---:|
| C-off:C-hybrid | 0.7169 | 0.7046 | +1.23 | 20.03 | 20.24 | -0.011 |
| C-off:C-shipped | 0.7169 | 0.7113 | +0.56 | 20.03 | 21.30 | -0.064 |
| C-off:C-full | 0.7169 | 0.7181 | -0.12 | 20.03 | 21.47 | -0.072 |
| B:C-off | 0.7098 | 0.7169 | -0.71 | 17.11 | 20.03 | -0.170 |
| B:C-hybrid | 0.7098 | 0.7046 | +0.52 | 17.11 | 20.24 | -0.183 |

## 2. Absolute quality gates (per arm, no comparison)

The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C relative, so both approaches can pass by being equally unfit. These apply to whichever approach ships.

### DGP-0

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.7098 FAIL | 0.7181 FAIL | 0.7046 FAIL | 0.7169 FAIL | 0.7113 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4145 FAIL | 0.5685 FAIL | 0.5601 FAIL | 0.5685 FAIL | 0.5804 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.9225 PASS | 0.9279 PASS | 0.9243 PASS | 0.9284 PASS | 0.9287 PASS |
| ABS-04 | no true-theta decile below 0.65 | 0.4208 FAIL | 0.5542 FAIL | 0.5542 FAIL | 0.5583 FAIL | 0.5583 FAIL |
| ABS-05 | >= 0.75 | 0.6573 FAIL | 0.6609 FAIL | 0.6616 FAIL | 0.6597 FAIL | 0.6650 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.0189 PASS | 1.0482 PASS | 1.0665 PASS | 1.0875 PASS | 1.0435 PASS |
| ABS-07 | P90 modelled duration <= 90 minutes | 98.0830 FAIL | 79.7750 PASS | 79.3250 PASS | 59.5000 PASS | 80.5000 PASS |

### DGP-2

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6117 FAIL | 0.6102 FAIL | 0.6135 FAIL | 0.6067 FAIL | 0.6148 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4789 FAIL | 0.4466 FAIL | 0.4632 FAIL | 0.4542 FAIL | 0.4690 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.9420 PASS | 0.9497 PASS | 0.9462 PASS | 0.9496 PASS | 0.9499 PASS |
| ABS-04 | no true-theta decile below 0.65 | 0.3417 FAIL | 0.3438 FAIL | 0.3521 FAIL | 0.3646 FAIL | 0.3500 FAIL |
| ABS-05 | >= 0.75 | 0.6794 FAIL | 0.6993 FAIL | 0.6954 FAIL | 0.6947 FAIL | 0.7025 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.3344 FAIL | 1.4219 FAIL | 1.3995 FAIL | 1.4485 FAIL | 1.4243 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 95.3330 FAIL | 83.0000 PASS | 83.0000 PASS | 58.5000 PASS | 83.0000 PASS |

## 3. Primary endpoint — exact-level accuracy, common band scale

| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| DGP-0 | C-off:C-hybrid | 0.7169 | 0.7046 | +1.23 | +2.38 | 2.0 | NOT SHOWN | 0.237 | 0.955 |
| DGP-0 | C-off:C-shipped | 0.7169 | 0.7113 | +0.56 | +1.69 | 2.0 | NON-INFERIOR | 0.234 | 0.961 |
| DGP-0 | C-off:C-full | 0.7169 | 0.7181 | -0.12 | +0.98 | 2.0 | NON-INFERIOR | 0.228 | 0.963 |
| DGP-0 | B:C-off | 0.7098 | 0.7169 | -0.71 | +0.44 | 2.0 | NON-INFERIOR | 0.217 | 0.961 |
| DGP-0 | B:C-hybrid | 0.7098 | 0.7046 | +0.52 | +1.83 | 2.0 | NON-INFERIOR | 0.270 | 0.953 |
| DGP-2 | C-off:C-hybrid | 0.6067 | 0.6135 | -0.69 | +0.38 | 2.0 | NON-INFERIOR | 0.206 | 0.963 |
| DGP-2 | C-off:C-shipped | 0.6067 | 0.6148 | -0.81 | +0.27 | 2.0 | NON-INFERIOR | 0.204 | 0.967 |
| DGP-2 | C-off:C-full | 0.6067 | 0.6102 | -0.35 | +0.73 | 2.0 | NON-INFERIOR | 0.207 | 0.968 |
| DGP-2 | B:C-off | 0.6117 | 0.6067 | +0.50 | +1.67 | 2.0 | NON-INFERIOR | 0.234 | 0.959 |
| DGP-2 | B:C-hybrid | 0.6117 | 0.6135 | -0.19 | +1.08 | 2.0 | NON-INFERIOR | 0.262 | 0.956 |

## 4. DAG safety — one-sided 95% upper bounds

Verified against the cohort's known node truth, so every inference and every block is checked. A live study would need ~100-155 verified events per gate even at a true rate of zero (validation A1).

### DGP-0 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 199 / 3171 | 0.0628 | 0.0703 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 3171 | 0.0000 | 0.0009 | 95% UCB < 2% | PASS |

### DGP-0 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 36062 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 260 / 4766 | 0.0546 | 0.0603 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 4766 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-0 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 33726 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 248 / 4792 | 0.0517 | 0.0573 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 4792 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-0 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 275 / 4781 | 0.0575 | 0.0634 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 4781 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-0 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 35482 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 259 / 4773 | 0.0543 | 0.0600 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 4773 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-2 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 441 / 2968 | 0.1486 | 0.1598 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 2968 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-2 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 311 / 1386 | 0.2244 | 0.2436 | 95% UCB < 3% | FAIL |
| C-DAG-04 | 3 | 262 / 3006 | 0.0872 | 0.0961 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 10325 / 10885 | 0.9486 | 0.9520 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 36657 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 788 / 4747 | 0.1660 | 0.1751 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 4747 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-2 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 9900 / 9900 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 34149 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 721 / 4782 | 0.1508 | 0.1595 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 4782 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-2 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 811 / 4767 | 0.1701 | 0.1793 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 4767 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

### DGP-2 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 10790 / 10790 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 35991 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 781 / 4757 | 0.1642 | 0.1733 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 4757 | 0.0000 | 0.0006 | 95% UCB < 2% | PASS |

## 5. Descriptive — reported with no verdict

### DGP-0

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.5341 | 0.5463 | 0.5720 | 0.5645 | 0.5434 |
| theta_mae | 0.3997 | 0.4173 | 0.4393 | 0.4244 | 0.4191 |
| within_one_level | 0.9967 | 0.9971 | 0.9965 | 0.9956 | 0.9975 |
| questions_per_candidate | 17.1125 | 21.4731 | 20.2444 | 20.0275 | 21.3019 |
| duration_minutes | 81.5410 | 50.8330 | 47.8330 | 47.5830 | 49.5830 |
| heldout_auc | 0.9342 | 0.9302 | 0.9295 | 0.9301 | 0.9307 |
| heldout_brier | 0.1047 | 0.1059 | 0.1068 | 0.1066 | 0.1055 |
| heldout_log_loss | 0.3357 | 0.3393 | 0.3419 | 0.3409 | 0.3380 |
| heldout_ece | 0.0407 | 0.0219 | 0.0240 | 0.0245 | 0.0221 |
| final_se_median | 0.5282 | 0.5240 | 0.5360 | 0.5212 | 0.5238 |
| interval_coverage_95 | 0.9465 | 0.9456 | 0.9483 | 0.9427 | 0.9460 |
| information_realization_ratio | 0.9547 | 1.0615 | 1.0649 | 1.0565 | 1.0615 |
| modality_blueprint_compliance | 0.9279 | 0.9983 | 0.9983 | 0.7788 | 0.9983 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### DGP-2

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.7104 | 0.7392 | 0.7506 | 0.7520 | 0.7402 |
| theta_mae | 0.5845 | 0.6165 | 0.6242 | 0.6213 | 0.6180 |
| within_one_level | 0.9969 | 0.9965 | 0.9956 | 0.9967 | 0.9969 |
| questions_per_candidate | 17.4662 | 21.8125 | 20.4737 | 20.2744 | 21.5950 |
| duration_minutes | 83.3330 | 51.3330 | 48.0830 | 47.7090 | 49.5830 |
| heldout_auc | 0.9392 | 0.9393 | 0.9377 | 0.9380 | 0.9389 |
| heldout_brier | 0.1022 | 0.0982 | 0.0997 | 0.0996 | 0.0984 |
| heldout_log_loss | 0.3319 | 0.3196 | 0.3254 | 0.3245 | 0.3211 |
| heldout_ece | 0.0471 | 0.0289 | 0.0303 | 0.0299 | 0.0285 |
| final_se_median | 0.5346 | 0.5224 | 0.5360 | 0.5211 | 0.5214 |
| interval_coverage_95 | 0.8683 | 0.8352 | 0.8490 | 0.8306 | 0.8363 |
| information_realization_ratio | 0.9572 | 1.0615 | 1.0649 | 1.0557 | 1.0613 |
| modality_blueprint_compliance | 0.9129 | 0.9988 | 0.9992 | 0.7685 | 0.9992 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## 6. Power — what n this study actually needs

| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |
|---|---:|---:|---:|---:|---:|---:|
| DGP-0 B:C-hybrid | 0.2702 | 4177 | 5786 | 5.78pp | 2.89pp | 2.04pp |
| DGP-0 B:C-off | 0.2167 | 3349 | 4639 | 5.18pp | 2.59pp | 1.83pp |
| DGP-0 C-off:C-full | 0.2283 | 3530 | 4889 | 5.31pp | 2.66pp | 1.88pp |
| DGP-0 C-off:C-hybrid | 0.2373 | 3668 | 5081 | 5.42pp | 2.71pp | 1.92pp |
| DGP-0 C-off:C-shipped | 0.2344 | 3623 | 5018 | 5.38pp | 2.69pp | 1.90pp |
| DGP-2 B:C-hybrid | 0.2619 | 4048 | 5607 | 5.69pp | 2.85pp | 2.01pp |
| DGP-2 B:C-off | 0.2342 | 3620 | 5014 | 5.38pp | 2.69pp | 1.90pp |
| DGP-2 C-off:C-full | 0.2069 | 3198 | 4430 | 5.06pp | 2.53pp | 1.79pp |
| DGP-2 C-off:C-hybrid | 0.2056 | 3179 | 4403 | 5.04pp | 2.52pp | 1.78pp |
| DGP-2 C-off:C-shipped | 0.2040 | 3153 | 4367 | 5.02pp | 2.51pp | 1.78pp |
