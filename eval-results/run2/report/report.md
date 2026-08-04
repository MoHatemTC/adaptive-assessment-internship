# Approach B vs Approach C — evaluation results

Cohort size per arm: 596 simulees (1788 (candidate, main) units).

Gate tiers follow the validation document's B4: one primary endpoint at alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. At 18 untiered statistical gates the chance of a spurious failure would be 60.3%.

## 1. DGP-0 — the null control

No prerequisite structure exists in this data. Any Approach C benefit here is an artefact of the generator, not of the graph.

| contrast | level accuracy ref | level accuracy test | degradation (pp) | questions ref | questions test | reduction |
|---|---:|---:|---:|---:|---:|---:|
| C-off:C-hybrid | 0.6762 | 0.6700 | +0.62 | 22.54 | 21.15 | +0.061 |
| C-off:C-shipped | 0.6762 | 0.6773 | -0.11 | 22.54 | 23.11 | -0.025 |
| C-off:C-full | 0.6762 | 0.6717 | +0.45 | 22.54 | 23.59 | -0.047 |
| B:C-hybrid | 0.5861 | 0.6700 | -8.39 | 22.13 | 21.15 | +0.044 |
| B:C-off | 0.5861 | 0.6762 | -9.00 | 22.13 | 22.54 | -0.018 |

## 2. Absolute quality gates (per arm, no comparison)

The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C relative, so both approaches can pass by being equally unfit. These apply to whichever approach ships.

### DGP-0

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.5861 FAIL | 0.6717 FAIL | 0.6700 FAIL | 0.6762 FAIL | 0.6773 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4631 FAIL | 0.6108 FAIL | 0.6116 FAIL | 0.6216 FAIL | 0.6257 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.6578 FAIL | 0.6194 FAIL | 0.6190 FAIL | 0.6381 FAIL | 0.6280 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.2849 FAIL | 0.3820 FAIL | 0.3876 FAIL | 0.3820 FAIL | 0.3820 FAIL |
| ABS-05 | >= 0.75 | 0.6609 FAIL | 0.6731 FAIL | 0.6694 FAIL | 0.6713 FAIL | 0.6817 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.5504 FAIL | 1.2215 FAIL | 1.2446 FAIL | 1.2669 FAIL | 1.1910 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 63.4170 PASS | 52.5830 PASS | 46.0830 PASS | 48.0410 PASS | 48.5830 PASS |

### DGP-1

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6122 FAIL | 0.6800 FAIL | 0.6700 FAIL | 0.7000 FAIL | 0.6811 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.3067 FAIL | 0.1800 FAIL | 0.1933 FAIL | 0.1733 FAIL | 0.1800 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.6254 FAIL | 0.5239 FAIL | 0.5418 FAIL | 0.5363 FAIL | 0.5473 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.3000 FAIL | 0.1889 FAIL | 0.2111 FAIL | 0.1889 FAIL | 0.1889 FAIL |
| ABS-05 | >= 0.75 | 0.6616 FAIL | 0.7019 FAIL | 0.6847 FAIL | 0.6939 FAIL | 0.7121 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.3877 FAIL | 1.1533 FAIL | 1.1821 FAIL | 1.1640 FAIL | 1.2098 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 64.4170 PASS | 52.3330 PASS | 46.0830 PASS | 48.5830 PASS | 48.5830 PASS |

### DGP-2

| gate | rule | B | C-full | C-hybrid | C-off | C-shipped |
|---|---|---:|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6390 FAIL | 0.6996 FAIL | 0.6967 FAIL | 0.7120 FAIL | 0.6996 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4887 FAIL | 0.3588 FAIL | 0.3588 FAIL | 0.3732 FAIL | 0.3402 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.6364 FAIL | 0.5357 FAIL | 0.5400 FAIL | 0.5637 FAIL | 0.5347 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.4177 FAIL | 0.3118 FAIL | 0.2765 FAIL | 0.2941 FAIL | 0.2647 FAIL |
| ABS-05 | >= 0.75 | 0.6632 FAIL | 0.7000 FAIL | 0.6847 FAIL | 0.6963 FAIL | 0.7102 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.4378 FAIL | 1.1915 FAIL | 1.1822 FAIL | 1.2228 FAIL | 1.1892 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 64.4170 PASS | 53.5830 PASS | 46.3330 PASS | 48.3330 PASS | 49.0830 PASS |

## 3. Primary endpoint — exact-level accuracy, common band scale

| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| DGP-0 | C-off:C-hybrid | 0.6762 | 0.6700 | +0.62 | +2.18 | 2.0 | NOT SHOWN | 0.176 | 0.624 |
| DGP-0 | C-off:C-shipped | 0.6762 | 0.6773 | -0.11 | +1.51 | 2.0 | NON-INFERIOR | 0.171 | 0.699 |
| DGP-0 | C-off:C-full | 0.6762 | 0.6717 | +0.45 | +2.07 | 2.0 | NOT SHOWN | 0.182 | 0.681 |
| DGP-0 | B:C-hybrid | 0.5861 | 0.6700 | -8.39 | -6.71 | 2.0 | NON-INFERIOR | 0.232 | 0.571 |
| DGP-0 | B:C-off | 0.5861 | 0.6762 | -9.00 | -7.55 | 2.0 | NON-INFERIOR | 0.173 | 0.651 |
| DGP-1 | C-off:C-hybrid | 0.7000 | 0.6700 | +3.00 | +5.11 | 2.0 | NOT SHOWN | 0.137 | 0.510 |
| DGP-1 | C-off:C-shipped | 0.7000 | 0.6811 | +1.89 | +3.78 | 2.0 | NOT SHOWN | 0.130 | 0.569 |
| DGP-1 | C-off:C-full | 0.7000 | 0.6800 | +2.00 | +4.00 | 2.0 | NOT SHOWN | 0.149 | 0.543 |
| DGP-1 | B:C-hybrid | 0.6122 | 0.6700 | -5.78 | -3.67 | 2.0 | NON-INFERIOR | 0.200 | 0.405 |
| DGP-1 | B:C-off | 0.6122 | 0.7000 | -8.78 | -7.00 | 2.0 | NON-INFERIOR | 0.154 | 0.453 |
| DGP-2 | C-off:C-hybrid | 0.7120 | 0.6967 | +1.53 | +3.06 | 2.0 | NOT SHOWN | 0.147 | 0.476 |
| DGP-2 | C-off:C-shipped | 0.7120 | 0.6996 | +1.24 | +2.71 | 2.0 | NOT SHOWN | 0.138 | 0.554 |
| DGP-2 | C-off:C-full | 0.7120 | 0.6996 | +1.24 | +2.71 | 2.0 | NOT SHOWN | 0.154 | 0.563 |
| DGP-2 | B:C-hybrid | 0.6390 | 0.6967 | -5.77 | -4.00 | 2.0 | NON-INFERIOR | 0.226 | 0.407 |
| DGP-2 | B:C-off | 0.6390 | 0.7120 | -7.30 | -5.89 | 2.0 | NON-INFERIOR | 0.161 | 0.475 |

## 4. DAG safety — one-sided 95% upper bounds

Verified against the cohort's known node truth, so every inference and every block is checked. A live study would need ~100-155 verified events per gate even at a true rate of zero (validation A1).

### DGP-0 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 288 / 1788 | 0.1611 | 0.1761 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1788 | 0.0000 | 0.0017 | 95% UCB < 2% | PASS |

### DGP-0 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 14153 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 128 / 1754 | 0.0730 | 0.0840 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1754 | 0.0000 | 0.0017 | 95% UCB < 2% | PASS |

### DGP-0 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 12678 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 131 / 1786 | 0.0733 | 0.0843 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1786 | 0.0000 | 0.0017 | 95% UCB < 2% | PASS |

### DGP-0 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 123 / 1758 | 0.0700 | 0.0808 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1758 | 0.0000 | 0.0017 | 95% UCB < 2% | PASS |

### DGP-0 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 13847 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 124 / 1767 | 0.0702 | 0.0810 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1767 | 0.0000 | 0.0017 | 95% UCB < 2% | PASS |

### DGP-1 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 103 / 900 | 0.1144 | 0.1334 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 900 | 0.0000 | 0.0033 | 95% UCB < 2% | PASS |

### DGP-1 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 118 / 1578 | 0.0748 | 0.0866 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 3516 / 4081 | 0.8616 | 0.8704 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 7223 | 0.0000 | 0.0004 | 0 events | PASS |
| U-02 | 3 | 29 / 876 | 0.0331 | 0.0449 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 876 | 0.0000 | 0.0034 | 95% UCB < 2% | PASS |

### DGP-1 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 3616 / 3616 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 6389 | 0.0000 | 0.0005 | 0 events | PASS |
| U-02 | 3 | 39 / 899 | 0.0434 | 0.0563 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 899 | 0.0000 | 0.0033 | 95% UCB < 2% | PASS |

### DGP-1 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 36 / 883 | 0.0408 | 0.0535 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 883 | 0.0000 | 0.0034 | 95% UCB < 2% | PASS |

### DGP-1 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 3981 / 3981 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 7035 | 0.0000 | 0.0004 | 0 events | PASS |
| U-02 | 3 | 36 / 889 | 0.0405 | 0.0531 | 3%-7% (nominal 5%) | PASS |
| C-DAG-15 | 3 | 0 / 889 | 0.0000 | 0.0034 | 95% UCB < 2% | PASS |

### DGP-2 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 223 / 1698 | 0.1313 | 0.1456 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1698 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

### DGP-2 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 121 / 2975 | 0.0407 | 0.0471 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 5663 / 6414 | 0.8829 | 0.8895 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 13585 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 124 / 1652 | 0.0751 | 0.0866 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1652 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

### DGP-2 / C-hybrid

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 5522 / 5522 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 12014 | 0.0000 | 0.0003 | 0 events | PASS |
| U-02 | 3 | 108 / 1697 | 0.0636 | 0.0743 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1697 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

### DGP-2 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| U-02 | 3 | 122 / 1661 | 0.0735 | 0.0848 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1661 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

### DGP-2 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 6238 / 6238 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 13283 | 0.0000 | 0.0002 | 0 events | PASS |
| U-02 | 3 | 121 / 1672 | 0.0724 | 0.0837 | 3%-7% (nominal 5%) | FAIL |
| C-DAG-15 | 3 | 0 / 1672 | 0.0000 | 0.0018 | 95% UCB < 2% | PASS |

## 5. Descriptive — reported with no verdict

### DGP-0

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.8135 | 0.6443 | 0.6877 | 0.6681 | 0.6263 |
| theta_mae | 0.6337 | 0.4855 | 0.5061 | 0.4943 | 0.4753 |
| within_one_level | 0.9832 | 0.9910 | 0.9860 | 0.9910 | 0.9916 |
| questions_per_candidate | 22.1326 | 23.5906 | 21.1527 | 22.5386 | 23.1107 |
| duration_minutes | 57.9170 | 44.3330 | 41.8330 | 42.3330 | 43.5830 |
| heldout_auc | 0.8055 | 0.8127 | 0.8138 | 0.8154 | 0.8156 |
| heldout_brier | 0.0878 | 0.0799 | 0.0828 | 0.0807 | 0.0785 |
| heldout_log_loss | 0.2911 | 0.2696 | 0.2777 | 0.2718 | 0.2655 |
| heldout_ece | 0.0471 | 0.0242 | 0.0278 | 0.0285 | 0.0238 |
| final_se_median | 0.5312 | 0.5279 | 0.5478 | 0.5279 | 0.5252 |
| interval_coverage_95 | 0.8389 | 0.9273 | 0.9267 | 0.9301 | 0.9301 |
| information_realization_ratio | 0.9687 | 1.0566 | 1.0610 | 1.0562 | 1.0555 |
| modality_blueprint_compliance | 0.9793 | 0.9989 | 0.9989 | 0.9782 | 0.9989 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### DGP-1

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.7365 | 0.6093 | 0.6564 | 0.6159 | 0.6368 |
| theta_mae | 0.5517 | 0.4757 | 0.4928 | 0.4708 | 0.4947 |
| within_one_level | 0.9822 | 0.9911 | 0.9844 | 0.9900 | 0.9889 |
| questions_per_candidate | 23.0900 | 23.9533 | 21.1933 | 22.9333 | 23.3433 |
| duration_minutes | 58.1670 | 44.8330 | 42.0830 | 43.5830 | 44.5830 |
| heldout_auc | 0.7730 | 0.7913 | 0.7888 | 0.7958 | 0.7921 |
| heldout_brier | 0.0697 | 0.0630 | 0.0658 | 0.0618 | 0.0623 |
| heldout_log_loss | 0.2397 | 0.2195 | 0.2281 | 0.2173 | 0.2176 |
| heldout_ece | 0.0462 | 0.0200 | 0.0287 | 0.0245 | 0.0230 |
| final_se_median | 0.5378 | 0.5269 | 0.5544 | 0.5300 | 0.5234 |
| interval_coverage_95 | 0.8856 | 0.9667 | 0.9567 | 0.9589 | 0.9600 |
| information_realization_ratio | 0.9710 | 1.0555 | 1.0609 | 1.0554 | 1.0549 |
| modality_blueprint_compliance | 0.9800 | 0.9989 | 0.9989 | 0.9856 | 0.9989 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

### DGP-2

| metric | B | C-full | C-hybrid | C-off | C-shipped |
|---|---:|---:|---:|---:|---:|
| theta_rmse | 0.7610 | 0.6289 | 0.6568 | 0.6474 | 0.6253 |
| theta_mae | 0.5719 | 0.4977 | 0.5048 | 0.4948 | 0.4990 |
| within_one_level | 0.9823 | 0.9935 | 0.9888 | 0.9906 | 0.9941 |
| questions_per_candidate | 22.9823 | 23.8710 | 21.1148 | 22.8428 | 23.3516 |
| duration_minutes | 57.9170 | 44.8330 | 42.0830 | 43.3330 | 44.5830 |
| heldout_auc | 0.7751 | 0.7929 | 0.7960 | 0.7930 | 0.7966 |
| heldout_brier | 0.0727 | 0.0628 | 0.0659 | 0.0640 | 0.0620 |
| heldout_log_loss | 0.2478 | 0.2187 | 0.2279 | 0.2235 | 0.2165 |
| heldout_ece | 0.0443 | 0.0215 | 0.0258 | 0.0260 | 0.0206 |
| final_se_median | 0.5371 | 0.5259 | 0.5547 | 0.5304 | 0.5231 |
| interval_coverage_95 | 0.8687 | 0.9264 | 0.9364 | 0.9276 | 0.9287 |
| information_realization_ratio | 0.9708 | 1.0561 | 1.0612 | 1.0557 | 1.0549 |
| modality_blueprint_compliance | 0.9747 | 0.9994 | 0.9994 | 0.9853 | 0.9994 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

## 6. Power — what n this study actually needs

| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |
|---|---:|---:|---:|---:|---:|---:|
| DGP-0 B:C-hybrid | 0.2315 | 3579 | 4958 | 5.35pp | 2.67pp | 1.89pp |
| DGP-0 B:C-off | 0.1728 | 2672 | 3701 | 4.62pp | 2.31pp | 1.63pp |
| DGP-0 C-off:C-full | 0.1823 | 2819 | 3904 | 4.75pp | 2.37pp | 1.68pp |
| DGP-0 C-off:C-hybrid | 0.1762 | 2723 | 3772 | 4.67pp | 2.33pp | 1.65pp |
| DGP-0 C-off:C-shipped | 0.1711 | 2646 | 3665 | 4.60pp | 2.30pp | 1.63pp |
| DGP-1 B:C-hybrid | 0.2000 | 3092 | 4282 | 4.97pp | 2.49pp | 1.76pp |
| DGP-1 B:C-off | 0.1544 | 2388 | 3307 | 4.37pp | 2.19pp | 1.54pp |
| DGP-1 C-off:C-full | 0.1489 | 2302 | 3188 | 4.29pp | 2.15pp | 1.52pp |
| DGP-1 C-off:C-hybrid | 0.1367 | 2113 | 2927 | 4.11pp | 2.06pp | 1.45pp |
| DGP-1 C-off:C-shipped | 0.1300 | 2010 | 2784 | 4.01pp | 2.00pp | 1.42pp |
| DGP-2 B:C-hybrid | 0.2261 | 3496 | 4842 | 5.29pp | 2.64pp | 1.87pp |
| DGP-2 B:C-off | 0.1614 | 2495 | 3455 | 4.47pp | 2.23pp | 1.58pp |
| DGP-2 C-off:C-full | 0.1537 | 2376 | 3291 | 4.36pp | 2.18pp | 1.54pp |
| DGP-2 C-off:C-hybrid | 0.1472 | 2276 | 3153 | 4.27pp | 2.13pp | 1.51pp |
| DGP-2 C-off:C-shipped | 0.1384 | 2140 | 2964 | 4.14pp | 2.07pp | 1.46pp |
