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

## What this branch is

`C-shipped` — the graph layer's **coverage requirement** live, its **propagation inert** —
with inference and blocking now *configurable* at three levels rather than compiled off.

Configurable is not the same as recommended. [evidence.md](evidence.md) has the numbers;
the short version is that on this bank, with prerequisite edges correct by construction,
upward inference was wrong 22.4% of the time against a 3% gate and blocking produced 8.7%
false blocks against a 2% gate. The defaults are off because the evidence says off.
