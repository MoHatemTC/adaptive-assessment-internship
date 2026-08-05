# S1 screening — results

- cells analysed: **72**
  - P01: 36/36 cells complete
  - P09: 36/36 cells complete
- baseline firing volume: **0.6700** verified inferences/session
- cells declared UNMEASURABLE (below 2% of baseline): **53**

## Within-candidate error correlation `r`

- point estimate: **0.76635** (cross-check 0.78161)
- 95% CI: [0.67587, 0.87719]
- clusters: 122 with 2+, 20 with one
- design effect: **6.653**
- §9 kill criterion (r > 0.4): **FIRES** on the one-sided upper bound of the 95% CI

## Main effects

### P01

**exact_level_accuracy** — 32 cells, pure error SD 0.020966 on 3 df, SE(effect) 0.007413

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.00666 | 0.62333 | 0.63000 | no |
| K | +0.00000 | 0.62667 | 0.62667 | no |
| C | -0.00167 | 0.62750 | 0.62583 | no |
| S | -0.00333 | 0.62833 | 0.62500 | no |
| L | +0.00000 | 0.62667 | 0.62667 | no |
| M | +0.00000 | 0.62667 | 0.62667 | no |
| E | +0.00000 | 0.62667 | 0.62667 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**questions_per_session** — 32 cells, pure error SD 0.202711 on 3 df, SE(effect) 0.071669

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | -0.00250 | 24.49500 | 24.49250 | no |
| K | +0.00000 | 24.49375 | 24.49375 | no |
| C | -0.00500 | 24.49625 | 24.49125 | no |
| S | -0.01250 | 24.50000 | 24.48750 | no |
| L | +0.00000 | 24.49375 | 24.49375 | no |
| M | +0.00000 | 24.49375 | 24.49375 | no |
| E | +0.00000 | 24.49375 | 24.49375 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**wrong_inference_rate** — 9 cells, pure error SD None on 0 df, SE(effect) None

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.04788 | 0.04598 | 0.09386 | no |
| K | — | — | — | n/a |
| C | -0.04883 | 0.09428 | 0.04545 | no |
| S | +0.14335 | 0.05665 | 0.20000 | no |
| L | +0.04788 | 0.04598 | 0.09386 | no |
| M | +0.04788 | 0.04598 | 0.09386 | no |
| E | -0.00946 | 0.07678 | 0.06732 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 0 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

### P09

**exact_level_accuracy** — 32 cells, pure error SD 0.01818 on 3 df, SE(effect) 0.006428

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | -0.00667 | 0.62458 | 0.61791 | no |
| K | +0.00000 | 0.62125 | 0.62125 | no |
| C | +0.00083 | 0.62083 | 0.62167 | no |
| S | +0.00083 | 0.62083 | 0.62167 | no |
| L | +0.00000 | 0.62125 | 0.62125 | no |
| M | +0.00000 | 0.62125 | 0.62125 | no |
| E | +0.00000 | 0.62125 | 0.62125 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**questions_per_session** — 32 cells, pure error SD 0.194567 on 3 df, SE(effect) 0.06879

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.04000 | 24.10375 | 24.14375 | no |
| K | +0.00000 | 24.12375 | 24.12375 | no |
| C | +0.02750 | 24.11000 | 24.13750 | no |
| S | -0.01250 | 24.13000 | 24.11750 | no |
| L | +0.00000 | 24.12375 | 24.12375 | no |
| M | +0.00000 | 24.12375 | 24.12375 | no |
| E | +0.00000 | 24.12375 | 24.12375 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**wrong_inference_rate** — 10 cells, pure error SD None on 0 df, SE(effect) None

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.00857 | 0.09202 | 0.10059 | no |
| K | — | — | — | n/a |
| C | +0.00645 | 0.09308 | 0.09953 | no |
| S | +0.02532 | 0.09124 | 0.11656 | no |
| L | +0.04282 | 0.07061 | 0.11343 | no |
| M | +0.04282 | 0.07061 | 0.11343 | no |
| E | +0.00182 | 0.09539 | 0.09721 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 0 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

## Cells

| cell | persona | n | verified | volume/sess | wrong rate | UCB | measurable |
|---|---|---:|---:|---:|---:|---:|:--:|
| c00_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c01_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c02_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c03_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r00_D-lo_K-lo_C-lo_S-lo_L-lo_M-lo_E-lo | P01 | 200 | 43 | 0.2150 | 0.0465 | 0.1393 | yes |
| r01_D-lo_K-lo_C-lo_S-lo_L-hi_M-lo_E-lo | P01 | 200 | 43 | 0.2150 | 0.0465 | 0.1393 | yes |
| r02_D-lo_K-lo_C-lo_S-hi_L-lo_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r03_D-lo_K-lo_C-lo_S-hi_L-hi_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r04_D-lo_K-lo_C-hi_S-lo_L-lo_M-hi_E-lo | P01 | 200 | 44 | 0.2200 | 0.0454 | 0.1363 | yes |
| r05_D-lo_K-lo_C-hi_S-lo_L-hi_M-hi_E-lo | P01 | 200 | 44 | 0.2200 | 0.0454 | 0.1363 | yes |
| r06_D-lo_K-lo_C-hi_S-hi_L-lo_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r07_D-lo_K-lo_C-hi_S-hi_L-hi_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r08_D-lo_K-hi_C-lo_S-lo_L-lo_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r09_D-lo_K-hi_C-lo_S-lo_L-hi_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r10_D-lo_K-hi_C-lo_S-hi_L-lo_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r11_D-lo_K-hi_C-lo_S-hi_L-hi_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r12_D-lo_K-hi_C-hi_S-lo_L-lo_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r13_D-lo_K-hi_C-hi_S-lo_L-hi_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r14_D-lo_K-hi_C-hi_S-hi_L-lo_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r15_D-lo_K-hi_C-hi_S-hi_L-hi_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r16_D-hi_K-lo_C-lo_S-lo_L-lo_M-hi_E-hi | P01 | 200 | 43 | 0.2150 | 0.0465 | 0.1393 | yes |
| r17_D-hi_K-lo_C-lo_S-lo_L-hi_M-hi_E-hi | P01 | 200 | 91 | 0.4550 | 0.1319 | 0.2049 | yes |
| r18_D-hi_K-lo_C-lo_S-hi_L-lo_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r19_D-hi_K-lo_C-lo_S-hi_L-hi_M-hi_E-lo | P01 | 200 | 15 | 0.0750 | 0.2000 | 0.4398 | yes |
| r20_D-hi_K-lo_C-hi_S-lo_L-lo_M-lo_E-hi | P01 | 200 | 44 | 0.2200 | 0.0454 | 0.1363 | yes |
| r21_D-hi_K-lo_C-hi_S-lo_L-hi_M-lo_E-hi | P01 | 200 | 44 | 0.2200 | 0.0454 | 0.1363 | yes |
| r22_D-hi_K-lo_C-hi_S-hi_L-lo_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r23_D-hi_K-lo_C-hi_S-hi_L-hi_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r24_D-hi_K-hi_C-lo_S-lo_L-lo_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r25_D-hi_K-hi_C-lo_S-lo_L-hi_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r26_D-hi_K-hi_C-lo_S-hi_L-lo_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r27_D-hi_K-hi_C-lo_S-hi_L-hi_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r28_D-hi_K-hi_C-hi_S-lo_L-lo_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r29_D-hi_K-hi_C-hi_S-lo_L-hi_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r30_D-hi_K-hi_C-hi_S-hi_L-lo_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r31_D-hi_K-hi_C-hi_S-hi_L-hi_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c00_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c01_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c02_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c03_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r00_D-lo_K-lo_C-lo_S-lo_L-lo_M-lo_E-lo | P09 | 200 | 49 | 0.2450 | 0.0612 | 0.1507 | yes |
| r01_D-lo_K-lo_C-lo_S-lo_L-hi_M-lo_E-lo | P09 | 200 | 49 | 0.2450 | 0.0612 | 0.1507 | yes |
| r02_D-lo_K-lo_C-lo_S-hi_L-lo_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r03_D-lo_K-lo_C-lo_S-hi_L-hi_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r04_D-lo_K-lo_C-hi_S-lo_L-lo_M-hi_E-lo | P09 | 200 | 50 | 0.2500 | 0.0800 | 0.1738 | yes |
| r05_D-lo_K-lo_C-hi_S-lo_L-hi_M-hi_E-lo | P09 | 200 | 107 | 0.5350 | 0.1495 | 0.2182 | yes |
| r06_D-lo_K-lo_C-hi_S-hi_L-lo_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r07_D-lo_K-lo_C-hi_S-hi_L-hi_M-hi_E-hi | P09 | 200 | 37 | 0.1850 | 0.1081 | 0.2305 | yes |
| r08_D-lo_K-hi_C-lo_S-lo_L-lo_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r09_D-lo_K-hi_C-lo_S-lo_L-hi_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r10_D-lo_K-hi_C-lo_S-hi_L-lo_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r11_D-lo_K-hi_C-lo_S-hi_L-hi_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r12_D-lo_K-hi_C-hi_S-lo_L-lo_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r13_D-lo_K-hi_C-hi_S-lo_L-hi_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r14_D-lo_K-hi_C-hi_S-hi_L-lo_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r15_D-lo_K-hi_C-hi_S-hi_L-hi_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r16_D-hi_K-lo_C-lo_S-lo_L-lo_M-hi_E-hi | P09 | 200 | 49 | 0.2450 | 0.0612 | 0.1507 | yes |
| r17_D-hi_K-lo_C-lo_S-lo_L-hi_M-hi_E-hi | P09 | 200 | 134 | 0.6700 | 0.1567 | 0.2178 | yes |
| r18_D-hi_K-lo_C-lo_S-hi_L-lo_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r19_D-hi_K-lo_C-lo_S-hi_L-hi_M-hi_E-lo | P09 | 200 | 56 | 0.2800 | 0.1250 | 0.2220 | yes |
| r20_D-hi_K-lo_C-hi_S-lo_L-lo_M-lo_E-hi | P09 | 200 | 50 | 0.2500 | 0.0800 | 0.1738 | yes |
| r21_D-hi_K-lo_C-hi_S-lo_L-hi_M-lo_E-hi | P09 | 200 | 50 | 0.2500 | 0.0800 | 0.1738 | yes |
| r22_D-hi_K-lo_C-hi_S-hi_L-lo_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r23_D-hi_K-lo_C-hi_S-hi_L-hi_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r24_D-hi_K-hi_C-lo_S-lo_L-lo_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r25_D-hi_K-hi_C-lo_S-lo_L-hi_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r26_D-hi_K-hi_C-lo_S-hi_L-lo_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r27_D-hi_K-hi_C-lo_S-hi_L-hi_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r28_D-hi_K-hi_C-hi_S-lo_L-lo_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r29_D-hi_K-hi_C-hi_S-lo_L-hi_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r30_D-hi_K-hi_C-hi_S-hi_L-lo_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r31_D-hi_K-hi_C-hi_S-hi_L-hi_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
