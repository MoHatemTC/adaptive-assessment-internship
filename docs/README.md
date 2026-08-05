# Documentation

The adaptive assessment engine: a multi-modality computerised adaptive test with an
optional competency graph layer.

## Start here

| Document | Read it when |
|---|---|
| [architecture.md](architecture.md) | you want to know what the engine does and why it is shaped this way |
| [configuration.md](configuration.md) | you are setting a flag and want to know what it does |
| [propagation-policy.md](propagation-policy.md) | you want to turn inference or blocking on, or work out why it is off |
| [bank-authoring.md](bank-authoring.md) | you are adding a question bank or a competency graph |
| [operations.md](operations.md) | you are running this, or something is wrong |
| [evidence.md](evidence.md) | you want the measurements behind the defaults |
| [microservices.md](microservices.md) | you are splitting this into services |
| [preregistration/](preregistration/) | you are running, reading or extending the propagation study |

## The propagation study

| Document | What it is |
|---|---|
| [C_Shipped_Propagation_Test_Plan.md](preregistration/C_Shipped_Propagation_Test_Plan.md) | the pre-registration, signed before any data. Amendments in §13, never by editing the text above them |
| [plan_math.py](preregistration/plan_math.py) | the design arithmetic. Every sample size in the plan is derived here, not chosen |
| [S1_Screening_Report.md](preregistration/S1_Screening_Report.md) | **what the screening stage found** |

The short version of the report: corroboration — the plan's headline lever — is inoperable
on this bank. The AIE graph is a forest of chains, every parent having exactly one direct
child, so requiring two independent observations of an ancestor cannot be satisfied at
depth 1 by any candidate. `K` is either farmable under weak independence keying or
unsatisfiable under strong. That is a graph-authoring finding, not a tuning one.

## What this branch is

`C-shipped` — the graph layer's **coverage requirement** live, its **propagation inert** —
with inference and blocking now *configurable* at three levels rather than compiled off.

Configurable is not the same as recommended. [evidence.md](evidence.md) has the numbers;
the short version is that on this bank, with prerequisite edges correct by construction,
upward inference was wrong 22.4% of the time against a 3% gate and blocking produced 8.7%
false blocks against a 2% gate. The defaults are off because the evidence says off.
