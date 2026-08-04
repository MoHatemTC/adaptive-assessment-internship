# Approach B vs Approach C — evaluation results

Cohort size per arm: 960 simulees (2880 (candidate, main) units).

Gate tiers follow the validation document's B4: one primary endpoint at alpha = 0.05, safety rates as one-sided upper bounds, everything else descriptive. At 18 untiered statistical gates the chance of a spurious failure would be 60.3%.

## 1. DGP-0 — the null control

No prerequisite structure exists in this data. Any Approach C benefit here is an artefact of the generator, not of the graph.

| contrast | level accuracy ref | level accuracy test | degradation (pp) | questions ref | questions test | reduction |
|---|---:|---:|---:|---:|---:|---:|
| C-off:C-full | 0.6917 | 0.6785 | +1.32 | 21.99 | 23.62 | -0.074 |
| C-off:C-shipped | 0.6917 | 0.6854 | +0.62 | 21.99 | 22.49 | -0.023 |
| B:C-off | 0.6215 | 0.6917 | -7.01 | 21.32 | 21.99 | -0.032 |
| B:C-full | 0.6215 | 0.6785 | -5.69 | 21.32 | 23.62 | -0.108 |

## 2. Absolute quality gates (per arm, no comparison)

The plan has no absolute bar anywhere: every gate in section 27 is B-versus-C relative, so both approaches can pass by being equally unfit. These apply to whichever approach ships.

### DGP-0

| gate | rule | B | C-full | C-off | C-shipped |
|---|---|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6215 FAIL | 0.6785 FAIL | 0.6917 FAIL | 0.6854 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.4610 FAIL | 0.5802 FAIL | 0.5245 FAIL | 0.6084 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.7137 FAIL | 0.7009 FAIL | 0.7218 FAIL | 0.7165 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.2986 FAIL | 0.4479 FAIL | 0.4549 FAIL | 0.4618 FAIL |
| ABS-05 | >= 0.75 | 0.6548 FAIL | 0.6605 FAIL | 0.6605 FAIL | 0.6714 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.4078 FAIL | 1.1845 FAIL | 1.2179 FAIL | 1.1338 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 66.1670 PASS | 56.3330 PASS | 47.3330 PASS | 48.3330 PASS |

### DGP-1

| gate | rule | B | C-full | C-off | C-shipped |
|---|---|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6441 FAIL | 0.6278 FAIL | 0.6562 FAIL | 0.6340 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.2129 FAIL | 0.1613 FAIL | 0.1548 FAIL | 0.1742 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.7059 FAIL | 0.6807 FAIL | 0.6841 FAIL | 0.6844 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.4583 FAIL | 0.2951 FAIL | 0.2917 FAIL | 0.3125 FAIL |
| ABS-05 | >= 0.75 | 0.6585 FAIL | 0.6866 FAIL | 0.6815 FAIL | 0.6967 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.3723 FAIL | 1.2944 FAIL | 1.2920 FAIL | 1.2815 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 64.4170 PASS | 56.5830 PASS | 48.3330 PASS | 48.5830 PASS |

### DGP-2

| gate | rule | B | C-full | C-off | C-shipped |
|---|---|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6479 FAIL | 0.6538 FAIL | 0.6562 FAIL | 0.6542 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.3510 FAIL | 0.2053 FAIL | 0.1589 FAIL | 0.2318 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.7156 FAIL | 0.6816 FAIL | 0.6909 FAIL | 0.6828 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.4549 FAIL | 0.3472 FAIL | 0.3021 FAIL | 0.3056 FAIL |
| ABS-05 | >= 0.75 | 0.6604 FAIL | 0.6819 FAIL | 0.6798 FAIL | 0.6927 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.3748 FAIL | 1.2616 FAIL | 1.2787 FAIL | 1.2471 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 65.4420 PASS | 57.3500 PASS | 48.1080 PASS | 48.5830 PASS |

### DGP-3

| gate | rule | B | C-full | C-off | C-shipped |
|---|---|---:|---:|---:|---:|
| ABS-01 | >= 0.80 overall | 0.6191 FAIL | 0.6371 FAIL | 0.6462 FAIL | 0.6368 FAIL |
| ABS-02 | >= 0.70 in every reported band | 0.2868 FAIL | 0.1473 FAIL | 0.0930 FAIL | 0.1550 FAIL |
| ABS-03 | >= 0.85 to certify a level; >= 0.80 for routing | 0.7105 FAIL | 0.6687 FAIL | 0.6759 FAIL | 0.6704 FAIL |
| ABS-04 | no true-theta decile below 0.65 | 0.4410 FAIL | 0.3646 FAIL | 0.3021 FAIL | 0.3125 FAIL |
| ABS-05 | >= 0.75 | 0.6587 FAIL | 0.6857 FAIL | 0.6836 FAIL | 0.6959 FAIL |
| ABS-06 | RMSE / mean SE within [0.95, 1.10] — the direct detector for double counting | 1.4310 FAIL | 1.3018 FAIL | 1.3330 FAIL | 1.3030 FAIL |
| ABS-07 | P90 modelled duration <= 90 minutes | 65.6670 PASS | 56.7580 PASS | 48.3330 PASS | 48.3330 PASS |

## 3. Primary endpoint — exact-level accuracy, common band scale

| DGP | contrast | ref | test | degradation (pp) | one-sided 95% UB (pp) | margin | verdict | psi | corr |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| DGP-0 | C-off:C-full | 0.6917 | 0.6785 | +1.32 | +2.71 | 2.0 | NOT SHOWN | 0.219 | 0.757 |
| DGP-0 | C-off:C-shipped | 0.6917 | 0.6854 | +0.62 | +1.94 | 2.0 | NON-INFERIOR | 0.197 | 0.795 |
| DGP-0 | B:C-off | 0.6215 | 0.6917 | -7.01 | -5.73 | 2.0 | NON-INFERIOR | 0.188 | 0.776 |
| DGP-0 | B:C-full | 0.6215 | 0.6785 | -5.69 | -4.24 | 2.0 | NON-INFERIOR | 0.254 | 0.708 |
| DGP-1 | C-off:C-full | 0.6562 | 0.6278 | +2.85 | +4.27 | 2.0 | NOT SHOWN | 0.199 | 0.702 |
| DGP-1 | C-off:C-shipped | 0.6562 | 0.6340 | +2.22 | +3.40 | 2.0 | NOT SHOWN | 0.153 | 0.720 |
| DGP-1 | B:C-off | 0.6441 | 0.6562 | -1.22 | +0.10 | 2.0 | NON-INFERIOR | 0.179 | 0.659 |
| DGP-1 | B:C-full | 0.6441 | 0.6278 | +1.63 | +3.09 | 2.0 | NOT SHOWN | 0.238 | 0.613 |
| DGP-2 | C-off:C-full | 0.6562 | 0.6538 | +0.24 | +1.70 | 2.0 | NON-INFERIOR | 0.207 | 0.715 |
| DGP-2 | C-off:C-shipped | 0.6562 | 0.6542 | +0.21 | +1.39 | 2.0 | NON-INFERIOR | 0.164 | 0.732 |
| DGP-2 | B:C-off | 0.6479 | 0.6562 | -0.83 | +0.42 | 2.0 | NON-INFERIOR | 0.189 | 0.675 |
| DGP-2 | B:C-full | 0.6479 | 0.6538 | -0.59 | +0.97 | 2.0 | NON-INFERIOR | 0.256 | 0.635 |
| DGP-3 | C-off:C-full | 0.6462 | 0.6371 | +0.90 | +2.27 | 2.0 | NOT SHOWN | 0.197 | 0.688 |
| DGP-3 | C-off:C-shipped | 0.6462 | 0.6368 | +0.94 | +2.12 | 2.0 | NOT SHOWN | 0.156 | 0.702 |
| DGP-3 | B:C-off | 0.6191 | 0.6462 | -2.71 | -1.32 | 2.0 | NON-INFERIOR | 0.193 | 0.618 |
| DGP-3 | B:C-full | 0.6191 | 0.6371 | -1.81 | -0.37 | 2.0 | NON-INFERIOR | 0.251 | 0.593 |

## 4. DAG safety — one-sided 95% upper bounds

Verified against the cohort's known node truth, so every inference and every block is checked. A live study would need ~100-155 verified events per gate even at a true rate of zero (validation A1).

### DGP-0 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 378 / 2880 | 0.1313 | 0.1421 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2880 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-0 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 23485 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 225 / 2816 | 0.0799 | 0.0888 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2816 | 0.0000 | 0.0011 | 95% UCB < 2% | PASS |

### DGP-0 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 189 / 2841 | 0.0665 | 0.0747 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 2841 / 2841 | 1.0000 | 1.0000 | 95% UCB < 2% | FAIL |

### DGP-0 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 21720 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 177 / 2850 | 0.0621 | 0.0701 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2850 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-1 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 394 / 2880 | 0.1368 | 0.1478 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2880 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-1 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 1 / 1 | 1.0000 | 1.0000 | 95% UCB < 3% | FAIL |
| C-DAG-04 | 3 | 1071 / 22536 | 0.0475 | 0.0499 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 4040 / 11561 | 0.3494 | 0.3568 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 23049 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 340 / 2782 | 0.1222 | 0.1329 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2782 | 0.0000 | 0.0011 | 95% UCB < 2% | PASS |

### DGP-1 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 340 / 2830 | 0.1201 | 0.1307 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 2830 / 2830 | 1.0000 | 1.0000 | 95% UCB < 2% | FAIL |

### DGP-1 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 12621 / 12621 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 22153 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 333 / 2837 | 0.1174 | 0.1278 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2837 | 0.0000 | 0.0011 | 95% UCB < 2% | PASS |

### DGP-2 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 390 / 2880 | 0.1354 | 0.1464 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2880 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-2 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 8 / 8 | 1.0000 | 1.0000 | 95% UCB < 3% | FAIL |
| C-DAG-04 | 3 | 1140 / 22437 | 0.0508 | 0.0533 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 4061 / 9087 | 0.4469 | 0.4556 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 23220 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 304 / 2774 | 0.1096 | 0.1198 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2774 | 0.0000 | 0.0011 | 95% UCB < 2% | PASS |

### DGP-2 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 315 / 2825 | 0.1115 | 0.1217 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 2825 / 2825 | 1.0000 | 1.0000 | 95% UCB < 2% | FAIL |

### DGP-2 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 10141 / 10141 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 22213 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 295 / 2841 | 0.1038 | 0.1137 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2841 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-3 / B

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 434 / 2880 | 0.1507 | 0.1621 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2880 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

### DGP-3 / C-full

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 1 / 1 | 1.0000 | 1.0000 | 95% UCB < 3% | FAIL |
| C-DAG-04 | 3 | 1064 / 22252 | 0.0478 | 0.0502 | 95% UCB < 2% | FAIL |
| C-DAG-05 | 4 | 4227 / 11302 | 0.3740 | 0.3816 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 23147 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 340 / 2773 | 0.1226 | 0.1333 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2773 | 0.0000 | 0.0011 | 95% UCB < 2% | PASS |

### DGP-3 / C-off

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 0 / 0 | — | — | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 1 | 0.0000 | 0.9500 | 0 events | PASS |
| S-02 | 3 | 364 / 2835 | 0.1284 | 0.1392 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 2835 / 2835 | 1.0000 | 1.0000 | 95% UCB < 2% | FAIL |

### DGP-3 / C-shipped

| gate | tier | events / verified | rate | 95% UCB | rule | verdict |
|---|---:|---:|---:|---:|---|---|
| C-DAG-03 | 3 | 0 / 0 | — | — | 95% UCB < 3% | NOT MEASURED |
| C-DAG-04 | 3 | 0 / 0 | — | — | 95% UCB < 2% | NOT MEASURED |
| C-DAG-05 | 4 | 11705 / 11705 | 1.0000 | 1.0000 | descriptive: no strict edges in this graph | DESCRIPTIVE |
| C-DAG-11 | 1 | 0 / 22285 | 0.0000 | 0.0001 | 0 events | PASS |
| S-02 | 3 | 340 / 2840 | 0.1197 | 0.1302 | 95% UCB < 2% | FAIL |
| C-DAG-15 | 3 | 0 / 2840 | 0.0000 | 0.0010 | 95% UCB < 2% | PASS |

## 5. Descriptive — reported with no verdict

### DGP-0

| metric | B | C-full | C-off | C-shipped |
|---|---:|---:|---:|---:|
| theta_rmse | 0.7328 | 0.6231 | 0.6400 | 0.5951 |
| theta_mae | 0.5649 | 0.4788 | 0.4669 | 0.4477 |
| within_one_level | 0.9892 | 0.9951 | 0.9903 | 0.9941 |
| questions_per_candidate | 21.3156 | 23.6219 | 21.9896 | 22.4948 |
| duration_minutes | 59.1670 | 46.3330 | 42.3330 | 43.3330 |
| heldout_auc | 0.8369 | 0.8371 | 0.8413 | 0.8409 |
| heldout_brier | 0.0970 | 0.0903 | 0.0922 | 0.0905 |
| heldout_log_loss | 0.3172 | 0.2988 | 0.3040 | 0.2995 |
| heldout_ece | 0.0467 | 0.0255 | 0.0300 | 0.0247 |
| final_se_median | 0.5264 | 0.5275 | 0.5264 | 0.5259 |
| interval_coverage_95 | 0.8688 | 0.9194 | 0.9337 | 0.9378 |
| information_realization_ratio | 0.9661 | 1.0522 | 1.0570 | 1.0571 |
| modality_blueprint_compliance | 0.9740 | 0.8361 | 0.9590 | 0.9983 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 0.0000 | 1.0000 |

### DGP-1

| metric | B | C-full | C-off | C-shipped |
|---|---:|---:|---:|---:|
| theta_rmse | 0.7213 | 0.6799 | 0.6829 | 0.6736 |
| theta_mae | 0.5563 | 0.5458 | 0.5310 | 0.5374 |
| within_one_level | 0.9889 | 0.9962 | 0.9927 | 0.9941 |
| questions_per_candidate | 22.2979 | 23.4646 | 22.4969 | 22.9646 |
| duration_minutes | 58.4170 | 44.5410 | 43.2080 | 44.3330 |
| heldout_auc | 0.8102 | 0.8172 | 0.8158 | 0.8184 |
| heldout_brier | 0.0835 | 0.0743 | 0.0758 | 0.0742 |
| heldout_log_loss | 0.2784 | 0.2527 | 0.2584 | 0.2534 |
| heldout_ece | 0.0468 | 0.0253 | 0.0278 | 0.0236 |
| final_se_median | 0.5339 | 0.5261 | 0.5284 | 0.5241 |
| interval_coverage_95 | 0.8632 | 0.8792 | 0.8812 | 0.8837 |
| information_realization_ratio | 0.9690 | 1.0491 | 1.0561 | 1.0559 |
| modality_blueprint_compliance | 0.9785 | 0.7142 | 0.9722 | 0.9990 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 0.0000 | 1.0000 |

### DGP-2

| metric | B | C-full | C-off | C-shipped |
|---|---:|---:|---:|---:|
| theta_rmse | 0.7219 | 0.6626 | 0.6763 | 0.6554 |
| theta_mae | 0.5526 | 0.5330 | 0.5262 | 0.5255 |
| within_one_level | 0.9892 | 0.9969 | 0.9927 | 0.9951 |
| questions_per_candidate | 22.3031 | 23.6198 | 22.5042 | 23.0167 |
| duration_minutes | 58.5840 | 45.0000 | 43.0830 | 44.3330 |
| heldout_auc | 0.8078 | 0.8167 | 0.8161 | 0.8181 |
| heldout_brier | 0.0858 | 0.0756 | 0.0773 | 0.0758 |
| heldout_log_loss | 0.2853 | 0.2561 | 0.2629 | 0.2580 |
| heldout_ece | 0.0467 | 0.0241 | 0.0270 | 0.0213 |
| final_se_median | 0.5336 | 0.5273 | 0.5287 | 0.5239 |
| interval_coverage_95 | 0.8646 | 0.8931 | 0.8903 | 0.8972 |
| information_realization_ratio | 0.9689 | 1.0496 | 1.0561 | 1.0557 |
| modality_blueprint_compliance | 0.9778 | 0.7254 | 0.9726 | 0.9990 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 0.0000 | 1.0000 |

### DGP-3

| metric | B | C-full | C-off | C-shipped |
|---|---:|---:|---:|---:|
| theta_rmse | 0.7529 | 0.6833 | 0.7040 | 0.6845 |
| theta_mae | 0.5809 | 0.5509 | 0.5488 | 0.5452 |
| within_one_level | 0.9854 | 0.9958 | 0.9917 | 0.9941 |
| questions_per_candidate | 22.7552 | 23.6125 | 22.7229 | 23.1073 |
| duration_minutes | 59.1670 | 45.0000 | 43.3330 | 44.3330 |
| heldout_auc | 0.8026 | 0.8155 | 0.8137 | 0.8168 |
| heldout_brier | 0.0849 | 0.0722 | 0.0742 | 0.0726 |
| heldout_log_loss | 0.2831 | 0.2470 | 0.2539 | 0.2487 |
| heldout_ece | 0.0529 | 0.0276 | 0.0289 | 0.0244 |
| final_se_median | 0.5335 | 0.5248 | 0.5289 | 0.5233 |
| interval_coverage_95 | 0.8493 | 0.8799 | 0.8736 | 0.8809 |
| information_realization_ratio | 0.9438 | 1.0401 | 1.0446 | 1.0444 |
| modality_blueprint_compliance | 0.9729 | 0.7104 | 0.9760 | 0.9986 |
| coverage_satisfied_rate | 1.0000 | 1.0000 | 0.0000 | 1.0000 |

## 6. Power — what n this study actually needs

| contrast | observed psi | n for 2pp @ 80% | n for 2pp @ 90% | detectable margin at n=500 | at n=2000 | at n=4000 |
|---|---:|---:|---:|---:|---:|---:|
| DGP-0 B:C-full | 0.2542 | 3929 | 5442 | 5.61pp | 2.80pp | 1.98pp |
| DGP-0 B:C-off | 0.1882 | 2909 | 4030 | 4.82pp | 2.41pp | 1.71pp |
| DGP-0 C-off:C-full | 0.2194 | 3392 | 4699 | 5.21pp | 2.60pp | 1.84pp |
| DGP-0 C-off:C-shipped | 0.1972 | 3049 | 4223 | 4.94pp | 2.47pp | 1.75pp |
| DGP-1 B:C-full | 0.2379 | 3677 | 5093 | 5.42pp | 2.71pp | 1.92pp |
| DGP-1 B:C-off | 0.1788 | 2764 | 3829 | 4.70pp | 2.35pp | 1.66pp |
| DGP-1 C-off:C-full | 0.1986 | 3070 | 4253 | 4.96pp | 2.48pp | 1.75pp |
| DGP-1 C-off:C-shipped | 0.1535 | 2373 | 3286 | 4.36pp | 2.18pp | 1.54pp |
| DGP-2 B:C-full | 0.2559 | 3956 | 5479 | 5.62pp | 2.81pp | 1.99pp |
| DGP-2 B:C-off | 0.1889 | 2920 | 4045 | 4.83pp | 2.42pp | 1.71pp |
| DGP-2 C-off:C-full | 0.2066 | 3194 | 4424 | 5.05pp | 2.53pp | 1.79pp |
| DGP-2 C-off:C-shipped | 0.1639 | 2534 | 3509 | 4.50pp | 2.25pp | 1.59pp |
| DGP-3 B:C-full | 0.2514 | 3886 | 5383 | 5.58pp | 2.79pp | 1.97pp |
| DGP-3 B:C-off | 0.1931 | 2985 | 4134 | 4.89pp | 2.44pp | 1.73pp |
| DGP-3 C-off:C-full | 0.1965 | 3038 | 4208 | 4.93pp | 2.46pp | 1.74pp |
| DGP-3 C-off:C-shipped | 0.1559 | 2410 | 3338 | 4.39pp | 2.19pp | 1.55pp |
