# Documentation

The engine's own documentation. Everything here describes code in this package.

## Start here

| Document | Read it when |
|---|---|
| [module.md](module.md) | **you are embedding this in a project.** The one to read first |
| [api.md](api.md) | you are calling it, method by method |
| [architecture.md](architecture.md) | you want to know what the engine does and why it is shaped this way |
| [operations.md](operations.md) | you are running it, or something is wrong |
| [configuration.md](configuration.md) | you are setting a flag and want to know what it does |

## Authoring the data the engine reads

| Document | Read it when |
|---|---|
| [bank-authoring.md](bank-authoring.md) | you are adding a question bank or a competency graph |
| [bank-schema.md](bank-schema.md) | you are writing the bank JSON itself, field by field |
| [grading-schema.md](grading-schema.md) | you want to know how each modality is scored |
| [competency-graph.md](competency-graph.md) | you are authoring or reading a competency graph |
| [banks/](banks/) | you want the competency map for a shipped bank |

## Why the defaults are what they are

| Document | Read it when |
|---|---|
| [evidence.md](evidence.md) | you want the measurements behind the defaults |
| [propagation-policy.md](propagation-policy.md) | you want to turn inference or blocking on, or work out why it is off |

The short version: on this bank, with prerequisite edges correct by construction, upward
inference was wrong 22.4% of the time against a 3% gate, and blocking produced 8.7% false
blocks against a 2% gate. Propagation ships inert because the evidence says inert. The
**coverage gate** is what is live.

## What is not here

The seven-service architecture this used to be, its four ADRs and its 1,851-line design
document. They described a topology that no longer exists, and every path in them was stale.
They are in git history.

The simulation harness that produced the numbers in [evidence.md](evidence.md) is also gone
— reproducing them means checking it out of history. The conclusions are kept because they
are why the shipped configuration is what it is.

## Per-directory documentation

Every code directory in the package carries its own README describing each file and what it
is responsible for, held to the filesystem by `tests/test_readmes_are_current.py`. Start at
[`cat_engine/README.md`](../README.md).
