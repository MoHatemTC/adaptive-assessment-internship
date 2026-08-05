# S1 screening — results

- cells analysed: **108**
  - P01: 36/36 cells complete
  - P02: 36/36 cells complete
  - P09: 36/36 cells complete
- baseline firing volume: **0.8900** verified inferences/session
- cells declared UNMEASURABLE (below 2% of baseline): **80**

## Within-candidate error correlation `r`

- point estimate: **0.87984** (cross-check 0.87127)
- 95% CI: [0.7807, 0.95341]
- clusters: 235 with 2+, 34 with one
- design effect: **9.956**
- §9 kill criterion (r > 0.4): **FIRES** on the one-sided upper bound of the 95% CI

## Main effects

### P01

**exact_level_accuracy** — 32 cells, pure error SD 0.0 on 3 df, SE(effect) 0.0

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.00167 | 0.64916 | 0.65084 | **yes** |
| K | +0.00000 | 0.65000 | 0.65000 | no |
| C | +0.00000 | 0.65000 | 0.65000 | no |
| S | -0.00167 | 0.65084 | 0.64916 | **yes** |
| L | +0.00000 | 0.65000 | 0.65000 | no |
| M | +0.00000 | 0.65000 | 0.65000 | no |
| E | +0.00000 | 0.65000 | 0.65000 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**questions_per_session** — 32 cells, pure error SD 0.0 on 3 df, SE(effect) 0.0

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.08000 | 24.11375 | 24.19375 | **yes** |
| K | +0.00000 | 24.15375 | 24.15375 | no |
| C | +0.01250 | 24.14750 | 24.16000 | **yes** |
| S | +0.02750 | 24.14000 | 24.16750 | **yes** |
| L | +0.00000 | 24.15375 | 24.15375 | no |
| M | +0.00000 | 24.15375 | 24.15375 | no |
| E | +0.00000 | 24.15375 | 24.15375 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**wrong_inference_rate** — 9 cells, pure error SD None on 0 df, SE(effect) None

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.01368 | 0.14039 | 0.15407 | no |
| K | — | — | — | n/a |
| C | -0.01811 | 0.15604 | 0.13793 | no |
| S | +0.01248 | 0.14661 | 0.15909 | no |
| L | +0.01368 | 0.14039 | 0.15407 | no |
| M | +0.01368 | 0.14039 | 0.15407 | no |
| E | +0.00868 | 0.14413 | 0.15282 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 0 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

### P02

**exact_level_accuracy** — 32 cells, pure error SD 0.0 on 3 df, SE(effect) 0.0

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | -0.00167 | 0.61084 | 0.60917 | **yes** |
| K | +0.00000 | 0.61000 | 0.61000 | no |
| C | +0.00334 | 0.60833 | 0.61167 | **yes** |
| S | +0.00167 | 0.60917 | 0.61084 | **yes** |
| L | +0.00000 | 0.61000 | 0.61000 | no |
| M | +0.00000 | 0.61000 | 0.61000 | no |
| E | +0.00000 | 0.61000 | 0.61000 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**questions_per_session** — 32 cells, pure error SD 0.0 on 3 df, SE(effect) 0.0

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.01875 | 24.51125 | 24.53000 | **yes** |
| K | -0.00250 | 24.52188 | 24.51938 | **yes** |
| C | +0.00375 | 24.51875 | 24.52250 | **yes** |
| S | -0.03125 | 24.53625 | 24.50500 | **yes** |
| L | +0.00000 | 24.52062 | 24.52062 | no |
| M | +0.00000 | 24.52062 | 24.52062 | no |
| E | +0.00000 | 24.52062 | 24.52062 | **yes** |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**wrong_inference_rate** — 9 cells, pure error SD None on 0 df, SE(effect) None

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.02889 | 0.15033 | 0.17923 | no |
| K | — | — | — | n/a |
| C | -0.05142 | 0.18924 | 0.13782 | no |
| S | +0.11964 | 0.15309 | 0.27273 | no |
| L | +0.04235 | 0.14286 | 0.18521 | no |
| M | +0.04673 | 0.14042 | 0.18715 | no |
| E | -0.01896 | 0.17481 | 0.15585 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 0 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

### P09

**exact_level_accuracy** — 32 cells, pure error SD 0.009265 on 3 df, SE(effect) 0.003276

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | +0.00333 | 0.63000 | 0.63333 | no |
| K | +0.00000 | 0.63167 | 0.63167 | no |
| C | +0.00000 | 0.63167 | 0.63167 | no |
| S | -0.00333 | 0.63333 | 0.63000 | no |
| L | +0.00000 | 0.63167 | 0.63167 | no |
| M | +0.00000 | 0.63167 | 0.63167 | no |
| E | +0.00000 | 0.63167 | 0.63167 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**questions_per_session** — 32 cells, pure error SD 0.133104 on 3 df, SE(effect) 0.047059

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | -0.01250 | 23.77250 | 23.76000 | no |
| K | +0.00000 | 23.76625 | 23.76625 | no |
| C | +0.00000 | 23.76625 | 23.76625 | no |
| S | -0.01750 | 23.77500 | 23.75750 | no |
| L | +0.00000 | 23.76625 | 23.76625 | no |
| M | +0.00000 | 23.76625 | 23.76625 | no |
| E | +0.00000 | 23.76625 | 23.76625 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 3 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

**wrong_inference_rate** — 10 cells, pure error SD None on 0 df, SE(effect) None

| factor | effect | mean(low) | mean(high) | active |
|---|---:|---:|---:|:--:|
| D | -0.00093 | 0.14530 | 0.14437 | no |
| K | — | — | — | n/a |
| C | +0.00093 | 0.14437 | 0.14530 | no |
| S | +0.02963 | 0.13891 | 0.16854 | no |
| L | +0.04059 | 0.12048 | 0.16107 | no |
| M | +0.04059 | 0.12048 | 0.16107 | no |
| E | -0.00093 | 0.14530 | 0.14437 | no |

_|effect| > 2 x SE(effect), where SE comes from centre-point pure error with 0 degrees of freedom. A SCREENING HEURISTIC for choosing which factors deserve a response surface — not a hypothesis test at any alpha._

## Cells

| cell | persona | n | verified | volume/sess | wrong rate | UCB | measurable |
|---|---|---:|---:|---:|---:|---:|:--:|
| c00_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c01_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c02_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| c03_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r00_D-lo_K-lo_C-lo_S-lo_L-lo_M-lo_E-lo | P01 | 200 | 84 | 0.4200 | 0.1429 | 0.2212 | yes |
| r01_D-lo_K-lo_C-lo_S-lo_L-hi_M-lo_E-lo | P01 | 200 | 84 | 0.4200 | 0.1429 | 0.2212 | yes |
| r02_D-lo_K-lo_C-lo_S-hi_L-lo_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r03_D-lo_K-lo_C-lo_S-hi_L-hi_M-lo_E-hi | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r04_D-lo_K-lo_C-hi_S-lo_L-lo_M-hi_E-lo | P01 | 200 | 87 | 0.4350 | 0.1379 | 0.2139 | yes |
| r05_D-lo_K-lo_C-hi_S-lo_L-hi_M-hi_E-lo | P01 | 200 | 87 | 0.4350 | 0.1379 | 0.2139 | yes |
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
| r16_D-hi_K-lo_C-lo_S-lo_L-lo_M-hi_E-hi | P01 | 200 | 84 | 0.4200 | 0.1429 | 0.2212 | yes |
| r17_D-hi_K-lo_C-lo_S-lo_L-hi_M-hi_E-hi | P01 | 200 | 161 | 0.8050 | 0.1925 | 0.2509 | yes |
| r18_D-hi_K-lo_C-lo_S-hi_L-lo_M-hi_E-lo | P01 | 200 | 0 | 0.0000 | — | — | **NO** |
| r19_D-hi_K-lo_C-lo_S-hi_L-hi_M-hi_E-lo | P01 | 200 | 44 | 0.2200 | 0.1591 | 0.2782 | yes |
| r20_D-hi_K-lo_C-hi_S-lo_L-lo_M-lo_E-hi | P01 | 200 | 87 | 0.4350 | 0.1379 | 0.2139 | yes |
| r21_D-hi_K-lo_C-hi_S-lo_L-hi_M-lo_E-hi | P01 | 200 | 87 | 0.4350 | 0.1379 | 0.2139 | yes |
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
| c00_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| c01_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| c02_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| c03_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r00_D-lo_K-lo_C-lo_S-lo_L-lo_M-lo_E-lo | P02 | 200 | 85 | 0.4250 | 0.1529 | 0.2321 | yes |
| r01_D-lo_K-lo_C-lo_S-lo_L-hi_M-lo_E-lo | P02 | 200 | 85 | 0.4250 | 0.1529 | 0.2321 | yes |
| r02_D-lo_K-lo_C-lo_S-hi_L-lo_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r03_D-lo_K-lo_C-lo_S-hi_L-hi_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r04_D-lo_K-lo_C-hi_S-lo_L-lo_M-hi_E-lo | P02 | 200 | 88 | 0.4400 | 0.1477 | 0.2246 | yes |
| r05_D-lo_K-lo_C-hi_S-lo_L-hi_M-hi_E-lo | P02 | 200 | 88 | 0.4400 | 0.1477 | 0.2246 | yes |
| r06_D-lo_K-lo_C-hi_S-hi_L-lo_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r07_D-lo_K-lo_C-hi_S-hi_L-hi_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r08_D-lo_K-hi_C-lo_S-lo_L-lo_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r09_D-lo_K-hi_C-lo_S-lo_L-hi_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r10_D-lo_K-hi_C-lo_S-hi_L-lo_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r11_D-lo_K-hi_C-lo_S-hi_L-hi_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r12_D-lo_K-hi_C-hi_S-lo_L-lo_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r13_D-lo_K-hi_C-hi_S-lo_L-hi_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r14_D-lo_K-hi_C-hi_S-hi_L-lo_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r15_D-lo_K-hi_C-hi_S-hi_L-hi_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r16_D-hi_K-lo_C-lo_S-lo_L-lo_M-hi_E-hi | P02 | 200 | 84 | 0.4200 | 0.1429 | 0.2212 | yes |
| r17_D-hi_K-lo_C-lo_S-lo_L-hi_M-hi_E-hi | P02 | 200 | 178 | 0.8900 | 0.2247 | 0.2823 | yes |
| r18_D-hi_K-lo_C-lo_S-hi_L-lo_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r19_D-hi_K-lo_C-lo_S-hi_L-hi_M-hi_E-lo | P02 | 200 | 55 | 0.2750 | 0.2727 | 0.3885 | yes |
| r20_D-hi_K-lo_C-hi_S-lo_L-lo_M-lo_E-hi | P02 | 200 | 86 | 0.4300 | 0.1279 | 0.2028 | yes |
| r21_D-hi_K-lo_C-hi_S-lo_L-hi_M-lo_E-hi | P02 | 200 | 86 | 0.4300 | 0.1279 | 0.2028 | yes |
| r22_D-hi_K-lo_C-hi_S-hi_L-lo_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r23_D-hi_K-lo_C-hi_S-hi_L-hi_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r24_D-hi_K-hi_C-lo_S-lo_L-lo_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r25_D-hi_K-hi_C-lo_S-lo_L-hi_M-lo_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r26_D-hi_K-hi_C-lo_S-hi_L-lo_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r27_D-hi_K-hi_C-lo_S-hi_L-hi_M-lo_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r28_D-hi_K-hi_C-hi_S-lo_L-lo_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r29_D-hi_K-hi_C-hi_S-lo_L-hi_M-hi_E-lo | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r30_D-hi_K-hi_C-hi_S-hi_L-lo_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| r31_D-hi_K-hi_C-hi_S-hi_L-hi_M-hi_E-hi | P02 | 200 | 0 | 0.0000 | — | — | **NO** |
| c00_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c01_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c02_D-ce_K-ce_C-ce_S-ce_L-ce_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| c03_D-ce_K-ce_C-ce_S-ce_L-ce_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r00_D-lo_K-lo_C-lo_S-lo_L-lo_M-lo_E-lo | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
| r01_D-lo_K-lo_C-lo_S-lo_L-hi_M-lo_E-lo | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
| r02_D-lo_K-lo_C-lo_S-hi_L-lo_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r03_D-lo_K-lo_C-lo_S-hi_L-hi_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r04_D-lo_K-lo_C-hi_S-lo_L-lo_M-hi_E-lo | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
| r05_D-lo_K-lo_C-hi_S-lo_L-hi_M-hi_E-lo | P09 | 200 | 173 | 0.8650 | 0.1965 | 0.2529 | yes |
| r06_D-lo_K-lo_C-hi_S-hi_L-lo_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r07_D-lo_K-lo_C-hi_S-hi_L-hi_M-hi_E-hi | P09 | 200 | 89 | 0.4450 | 0.1685 | 0.2476 | yes |
| r08_D-lo_K-hi_C-lo_S-lo_L-lo_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r09_D-lo_K-hi_C-lo_S-lo_L-hi_M-hi_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r10_D-lo_K-hi_C-lo_S-hi_L-lo_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r11_D-lo_K-hi_C-lo_S-hi_L-hi_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r12_D-lo_K-hi_C-hi_S-lo_L-lo_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r13_D-lo_K-hi_C-hi_S-lo_L-hi_M-lo_E-hi | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r14_D-lo_K-hi_C-hi_S-hi_L-lo_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r15_D-lo_K-hi_C-hi_S-hi_L-hi_M-lo_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r16_D-hi_K-lo_C-lo_S-lo_L-lo_M-hi_E-hi | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
| r17_D-hi_K-lo_C-lo_S-lo_L-hi_M-hi_E-hi | P09 | 200 | 172 | 0.8600 | 0.1919 | 0.2480 | yes |
| r18_D-hi_K-lo_C-lo_S-hi_L-lo_M-hi_E-lo | P09 | 200 | 0 | 0.0000 | — | — | **NO** |
| r19_D-hi_K-lo_C-lo_S-hi_L-hi_M-hi_E-lo | P09 | 200 | 89 | 0.4450 | 0.1685 | 0.2476 | yes |
| r20_D-hi_K-lo_C-hi_S-lo_L-lo_M-lo_E-hi | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
| r21_D-hi_K-lo_C-hi_S-lo_L-hi_M-lo_E-hi | P09 | 200 | 83 | 0.4150 | 0.1205 | 0.1958 | yes |
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
