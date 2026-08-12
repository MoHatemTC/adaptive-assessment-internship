# Documentation

The adaptive assessment engine: a multi-modality computerised adaptive test with an
optional competency graph layer.

## Start here

| Document | Read it when |
|---|---|
| [module.md](module.md) | **you are embedding this in a project.** The one to read first |
| [api.md](api.md) | you are calling it, method by method |
| [architecture.md](architecture.md) | you want to know what the engine does and why it is shaped this way |
| [adr/0003-uploaded-banks-and-scoped-assessments.md](adr/0003-uploaded-banks-and-scoped-assessments.md) | you want to know why uploaded banks derive their graph, and why a scope decorates rather than changes the engine |
| [architecture-proposal.md](architecture-proposal.md) | HISTORY. The seven-service design, drawn. Kept for the reasoning, not the paths |
| [operations.md](operations.md) | you are running this, or something is wrong |
| [configuration.md](configuration.md) | you are setting a flag and want to know what it does |
| [bank-authoring.md](bank-authoring.md) | you are adding a question bank or a competency graph |
| [bank-schema.md](bank-schema.md) | you are writing the bank JSON itself, field by field |
| [grading-schema.md](grading-schema.md) | you want to know how each modality is scored |
| [competency-graph.md](competency-graph.md) | you are authoring or reading a competency graph |
| [banks/](banks/) | you want the competency map for a shipped bank |
| [propagation-policy.md](propagation-policy.md) | you want to turn inference or blocking on, or work out why it is off |
| [evidence.md](evidence.md) | you want the measurements behind the defaults |
| [adr/](adr/) | you want to know why the seams are where they are |
| [preregistration/](preregistration/) | you are running, reading or extending the propagation study |

There are no machine-readable specs any more: the surface is Python, and its signatures and
docstrings are the specification. `docs/api/openapi-*.json` went with the services.

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

**One module** a host project imports — see
[adr/0004](adr/0004-from-services-to-a-module.md). It was seven services over one engine
library, and the engine is still a library; what went is the transport between its parts.

On measurement it is `C-shipped`: the graph layer's **coverage requirement** live, its
**propagation inert**, with inference and blocking *configurable* at three levels rather
than compiled off.

Configurable is not the same as recommended. [evidence.md](evidence.md) has the numbers;
the short version is that on this bank, with prerequisite edges correct by construction,
upward inference was wrong 22.4% of the time against a 3% gate and blocking produced 8.7%
false blocks against a 2% gate. The defaults are off because the evidence says off.
