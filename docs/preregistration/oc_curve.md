# Operating characteristic — the confidence gate

Arm `C-full` on `DGP-3`, 200 sessions per point. Every other factor held at the configuration S1 found reachable and permissive (D=1, K=1, S=0.80, λ=0.7, all modalities, all edges).

Gates drawn: wrong inference **< 3%** (95% upper bound), false blocking **< 2%**.

## P01

| C | verified | volume/sess | wrong rate | 95% UCB | clears 3%? | direct mastered/sess | questions | accuracy |
|---|---:|---:|---:|---:|:--:|---:|---:|---:|
| 0.80 | 91 | 0.4550 | 0.1319 | 0.2049 | no | 9.24 | 24.50 | 0.6267 |
| 0.85 | 54 | 0.2700 | 0.1111 | 0.2076 | no | 8.96 | 24.50 | 0.6217 |
| 0.90 | 44 | 0.2200 | 0.0454 | 0.1363 | no | 8.90 | 24.50 | 0.6233 |
| 0.93 | 44 | 0.2200 | 0.0454 | 0.1363 | no | 8.90 | 24.50 | 0.6233 |
| 0.95 | 44 | 0.2200 | 0.0454 | 0.1363 | no | 8.90 | 24.50 | 0.6233 |
| 0.97 | 44 | 0.2200 | 0.0454 | 0.1363 | no | 8.90 | 24.50 | 0.6233 |
| 1.00 | 44 | 0.2200 | 0.0454 | 0.1363 | no | 8.90 | 24.50 | 0.6233 |

## P09

| C | verified | volume/sess | wrong rate | 95% UCB | clears 3%? | direct mastered/sess | questions | accuracy |
|---|---:|---:|---:|---:|:--:|---:|---:|---:|
| 0.80 | 134 | 0.6700 | 0.1567 | 0.2178 | no | 9.29 | 24.09 | 0.6233 |
| 0.85 | 134 | 0.6700 | 0.1567 | 0.2178 | no | 9.29 | 24.09 | 0.6233 |
| 0.90 | 129 | 0.6450 | 0.1628 | 0.2259 | no | 9.28 | 24.09 | 0.6233 |
| 0.93 | 117 | 0.5850 | 0.1538 | 0.2196 | no | 9.18 | 24.11 | 0.6233 |
| 0.95 | 107 | 0.5350 | 0.1495 | 0.2182 | no | 9.07 | 24.12 | 0.6250 |
| 0.97 | 81 | 0.4050 | 0.1482 | 0.2290 | no | 8.93 | 24.13 | 0.6250 |
| 1.00 | 49 | 0.2450 | 0.0816 | 0.1772 | no | 8.67 | 24.11 | 0.6283 |
