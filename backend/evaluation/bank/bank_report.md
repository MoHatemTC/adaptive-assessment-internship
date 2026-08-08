# AIE bank report

The active bank contains 300 unique, Pydantic-valid active items: 150 MCQ, 75 code, and 75
voice. No invalid CAT bounds, non-positive authored durations, duplicate ids, or invalid
loadings were found. Code items carry public and hidden tests plus rubric criteria; voice
items carry criterion rubrics and sample/reference material.

| Main | Usable pool | MCQ | Code | Voice | Required critical nodes | Best worst-case SE with 12 |
|---|---:|---:|---:|---:|---:|---:|
| C1 | 102 | 51 | 25 | 26 | 4 | 0.3650 |
| C3 | 104 | 51 | 26 | 27 | 4 | 0.3637 |
| C6 | 113 | 57 | 28 | 28 | 7 | 0.3601 |

At the main-competency level, the configured SE target 0.55, direct critical coverage, and
the code/voice supply floors are structurally reachable within twelve questions. The fresh
smoke run achieved direct coverage for 100% of 300 candidate-main units.

The sub-competency picture is weaker. The repository's information-floor analysis finds 16
of 33 AIE sub-competencies cannot reach SE 0.55 at theta 0 even if their entire sub-pool is
used; closing the gaps needs at least 28 optimally targeted items. This does not prevent the
current one-theta-per-main engine from running, but it blocks any claim that every node is
independently measured to the configured precision and constrains future node-posterior work.

The baseline smoke's modality compliance was 96.33% despite abundant supply. After correcting
cross-main modality credit and adding the convergence veto, the paired post-fix smoke reaches
100% (300/300). The supply diagnosis was therefore confirmed: this was enforcement, not a
bank shortage.
