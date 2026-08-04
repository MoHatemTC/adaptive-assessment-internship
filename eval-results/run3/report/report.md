# Approach B vs Approach C — evaluation results

Cohort size per arm: 821 simulees (2463 (candidate, main) units).

Gate tiers follow the validation document's B4: one primary endpoint at alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. At 18 untiered statistical gates the chance of a spurious failure would be 60.3%.

## 1. DGP-0 — the null control

No prerequisite structure exists in this data. Any Approach C benefit here is an artefact of the generator, not of the graph.

| contrast | level accuracy ref | level accuracy test | degradation (pp) | questions ref | questions test | reduction |
|---|---:|---:|---:|---:|---:|---:|
| C-off:C-hybrid | 0.7198 | 0.7093 | +1.06 | 20.10 | 20.26 | -0.008 |
| C-off:C-shipped | 0.7198 | 0.7146 | +0.53 | 20.10 | 21.34 | -0.061 |
| C-off:C-full | 0.7198 | 0.7162 | +0.37 | 20.10 | 21.52 | -0.071 |
| B:C-off | 0.7036 | 0.7198 | -1.62 | 17.22 | 20.10 | -0.167 |
| B:C-hybrid | 0.7036 | 0.7093 | -0.57 | 17.22 | 20.26 | -0.177 |

## 2. Absolute quality gates (per arm, no comparison)

The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C relative, so both approaches can pass by being equally unfit. These apply to whichever approach ships.

### DGP-0

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.7036 FAIL | 0.7162 FAIL | 0.7093 FAIL | 0.7198 FAIL | 0.7146 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4190 FAIL | 0.5688 FAIL | 0.5596 FAIL | 0.5749 FAIL | 0.5749 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.9248 PASS | 0.9295 PASS | 0.9260 PASS | 0.9306 PASS | 0.9302 PASS |
| ABS-04 | no true-theta decile below 0.65 | 0.4350 FAIL | 0.5569 FAIL | 0.5569 FAIL | 0.5854 FAIL | 0.5569 FAIL |
| ABS-05 | >= 0.75 | 0.6603 FAIL | 0.6610 FAIL | 0.6606 FAIL | 0.6597 FAIL | 0.6653 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.0291 PASS | 1.0651 PASS | 1.0772 PASS | 1.0791 PASS | 1.0609 PASS |
| ABS-07 | P90 modelled duration <= 90 minutes | 99.0830 FAIL | 81.2500 PASS | 79.2500 PASS | 59.7500 PASS | 81.2500 PASS |

### DGP-2

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6087 FAIL | 0.6134 FAIL | 0.6142 FAIL | 0.6099 FAIL | 0.6142 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4538 FAIL | 0.4439 FAIL | 0.4555 FAIL | 0.4389 FAIL | 0.4621 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.9435 PASS | 0.9502 PASS | 0.9467 PASS | 0.9507 PASS | 0.9505 PASS |
| ABS-04 | no true-theta decile below 0.65 | 0.3399 FAIL | 0.3478 FAIL | 0.3557 FAIL | 0.3162 FAIL | 0.3557 FAIL |
| ABS-05 | >= 0.75 | 0.6813 FAIL | 0.6959 FAIL | 0.6942 FAIL | 0.6947 FAIL | 0.7015 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.3062 FAIL | 1.3870 FAIL | 1.3719 FAIL | 1.4265 FAIL | 1.3945 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 95.0830 FAIL | 83.0000 PASS | 83.0000 PASS | 58.2500 PASS | 83.0000 PASS |

## 3. Primary endpoint — exact-level accuracy, common band scale

| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| DGP-0 | C-off:C-hybrid | 0.7198 | 0.7093 | +1.06 | +2.80 | 2.0 | NOT SHOWN | 0.236 | 0.957 |
| DGP-0 | C-off:C-shipped | 0.7198 | 0.7146 | +0.53 | +2.19 | 2.0 | NOT SHOWN | 0.235 | 0.963 |
| DGP-0 | C-off:C-full | 0.7198 | 0.7162 | +0.37 | +1.98 | 2.0 | NON-INFERIOR | 0.229 | 0.965 |
| DGP-0 | B:C-off | 0.7036 | 0.7198 | -1.62 | -0.08 | 2.0 | NON-INFERIOR | 0.214 | 0.963 |
| DGP-0 | B:C-hybrid | 0.7036 | 0.7093 | -0.57 | +1.14 | 2.0 | NON-INFERIOR | 0.266 | 0.955 |
| DGP-2 | C-off:C-hybrid | 0.6099 | 0.6142 | -0.43 | +0.99 | 2.0 | NON-INFERIOR | 0.208 | 0.964 |
| DGP-2 | C-off:C-shipped | 0.6099 | 0.6142 | -0.43 | +1.03 | 2.0 | NON-INFERIOR | 0.207 | 0.969 |
| DGP-2 | C-off:C-full | 0.6099 | 0.6134 | -0.35 | +1.14 | 2.0 | NON-INFERIOR | 0.210 | 0.969 |
| DGP-2 | B:C-off | 0.6087 | 0.6099 | -0.12 | +1.50 | 2.0 | NON-INFERIOR | 0.229 | 0.961 |
| DGP-2 | B:C-hybrid | 0.6087 | 0.6142 | -0.55 | +1.22 | 2.0 | NON-INFERIOR | 0.261 | 0.959 |

## 4. DAG safety — one-sided 95% upper bounds

Verified against the cohort's known node truth, so every inference and every block is checked. A live study would need ~100-155 verified events per gate even at a true rate of zero (validation A1).

### DGP-0 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 114 / 1649 | 0.0691 | 0.0803 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1649 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

### DGP-0 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 18522 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 138 / 2443 | 0.0565 | 0.0648 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 2443 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-0 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 17311 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 128 / 2457 | 0.0521 | 0.0601 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 2457 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-0 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 141 / 2455 | 0.0574 | 0.0658 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 2455 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-0 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 18227 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 135 / 2447 | 0.0552 | 0.0634 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 2447 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-2 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 216 / 1601 | 0.1349 | 0.1498 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1601 | 0.0000 | 0.0019 | 95% UCB < 2% | PASS |

### DGP-2 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 162 / 696 | 0.2328 | 0.2606 | 95% UCB < 3% | FAIL |
| C-DAG-04 | 3 | 140 / 1700 | 0.0824 | 0.0942 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 5615 / 5926 | 0.9475 | 0.9522 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 19417 | 0.0000 | 0.0001 | 0 events | PASS |
| U-02 | 3 | 396 / 2504 | 0.1582 | 0.1706 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 2504 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-2 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 5369 / 5369 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 18022 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 354 / 2527 | 0.1401 | 0.1520 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 2527 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-2 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 423 / 2520 | 0.1679 | 0.1806 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 2520 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

### DGP-2 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 5857 / 5857 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 19025 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 387 / 2511 | 0.1541 | 0.1665 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 2511 | 0.0000 | 0.0012 | 95% UCB < 2% | PASS |

## 5. Descriptive — reported with no verdict

### DGP-0

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.5395 | 0.5555 | 0.5785 | 0.5601 | 0.5528 |
| theta_mae | 0.4059 | 0.4203 | 0.4397 | 0.4241 | 0.4217 |
| within_one_level | 0.9968 | 0.9959 | 0.9951 | 0.9968 | 0.9963 |
| questions_per_candidate | 17.2205 | 21.5225 | 20.2631 | 20.1035 | 21.3386 |
| duration_minutes | 77.8330 | 50.0000 | 47.3330 | 47.0830 | 48.5830 |
| heldout_auc | 0.9360 | 0.9312 | 0.9302 | 0.9307 | 0.9311 |
| heldout_brier | 0.1032 | 0.1051 | 0.1061 | 0.1062 | 0.1054 |
| heldout_log_loss | 0.3313 | 0.3364 | 0.3398 | 0.3396 | 0.3373 |
| heldout_ece | 0.0415 | 0.0223 | 0.0252 | 0.0261 | 0.0239 |
| final_se_median | 0.5284 | 0.5252 | 0.5362 | 0.5219 | 0.5246 |
| interval_coverage_95 | 0.9415 | 0.9436 | 0.9480 | 0.9427 | 0.9452 |
| information_realization_ratio | 0.9551 | 1.0612 | 1.0648 | 1.0564 | 1.0613 |
| modality_blueprint_compliance | 0.9298 | 0.9976 | 0.9976 | 0.7840 | 0.9976 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### DGP-2

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.6956 | 0.7218 | 0.7372 | 0.7408 | 0.7256 |
| theta_mae | 0.5726 | 0.6016 | 0.6116 | 0.6118 | 0.6047 |
| within_one_level | 0.9968 | 0.9964 | 0.9961 | 0.9972 | 0.9976 |
| questions_per_candidate | 17.6947 | 21.9065 | 20.4805 | 20.3006 | 21.6414 |
| duration_minutes | 78.5830 | 50.8330 | 46.8330 | 46.8330 | 48.5000 |
| heldout_auc | 0.9415 | 0.9409 | 0.9387 | 0.9399 | 0.9398 |
| heldout_brier | 0.0996 | 0.0965 | 0.0986 | 0.0974 | 0.0974 |
| heldout_log_loss | 0.3237 | 0.3139 | 0.3218 | 0.3183 | 0.3177 |
| heldout_ece | 0.0451 | 0.0273 | 0.0301 | 0.0282 | 0.0278 |
| final_se_median | 0.5353 | 0.5226 | 0.5368 | 0.5221 | 0.5220 |
| interval_coverage_95 | 0.8785 | 0.8434 | 0.8600 | 0.8331 | 0.8469 |
| information_realization_ratio | 0.9578 | 1.0612 | 1.0648 | 1.0557 | 1.0610 |
| modality_blueprint_compliance | 0.9124 | 0.9980 | 0.9992 | 0.7708 | 0.9992 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## 6. Power — what n this study actually needs

| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |
|---|---:|---:|---:|---:|---:|---:|
| DGP-0 B:C-hybrid | 0.2663 | 4117 | 5703 | 5.74pp | 2.87pp | 2.03pp |
| DGP-0 B:C-off | 0.2144 | 3314 | 4590 | 5.15pp | 2.57pp | 1.82pp |
| DGP-0 C-off:C-full | 0.2294 | 3546 | 4912 | 5.33pp | 2.66pp | 1.88pp |
| DGP-0 C-off:C-hybrid | 0.2363 | 3653 | 5060 | 5.41pp | 2.70pp | 1.91pp |
| DGP-0 C-off:C-shipped | 0.2351 | 3634 | 5033 | 5.39pp | 2.70pp | 1.91pp |
| DGP-2 B:C-hybrid | 0.2611 | 4037 | 5591 | 5.68pp | 2.84pp | 2.01pp |
| DGP-2 B:C-off | 0.2292 | 3543 | 4907 | 5.32pp | 2.66pp | 1.88pp |
| DGP-2 C-off:C-full | 0.2103 | 3250 | 4502 | 5.10pp | 2.55pp | 1.80pp |
| DGP-2 C-off:C-hybrid | 0.2079 | 3214 | 4451 | 5.07pp | 2.54pp | 1.79pp |
| DGP-2 C-off:C-shipped | 0.2071 | 3202 | 4434 | 5.06pp | 2.53pp | 1.79pp |
