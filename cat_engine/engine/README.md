# `cat_engine/engine/`

The psychometrics. Everything outside this directory is an adapter over it.

This is the part that decides what a candidate is measured as. It was a monolith, then a
library installed into seven service images, and is now a subpackage — and in all three
arrangements **not one line of the loop changed**, because the seams it depends on were
always Protocols. See [ADR-0002](../../docs/adr/0002-engine-as-a-library.md) and
[ADR-0004](../../docs/adr/0004-from-services-to-a-module.md).

It imported as top-level `app` until the module was extracted. A package a host embeds
cannot claim that name.

## Why `services/` is still called that

There are no services. The name predates them: it means the engine's service layer in the
ordinary sense — the code that does something, as opposed to `schemas/` which only describes.
Renaming it would rewrite every import in the engine to change a word, and the engine's
internals were deliberately left alone across both migrations.

## Directories

| Directory | Responsibility |
|---|---|
| [`config/`](config/README.md) | All engine policy in one place, the data directory, and the fingerprint over the settings that change what is MEASURED. |
| [`schemas/`](schemas/README.md) | The typed boundaries. `BankItem`, `AssessmentState`, `GradedResponse`, and the per-engine equivalents. |
| [`services/`](services/README.md) | The engines themselves: MCQ, code, voice, the competency graph, and the orchestrator that runs all of them in one session. |
| `data/` | The checked-in banks, their competency graphs, and the code rubrics. See [data/README.md](data/README.md). |

## The measurement, in one paragraph

One latent ability (`theta`) per main competency, estimated by EAP on a fixed 41-point grid
over [-4, 4] under a 3PL model. Every modality lands on the same scale, which is what makes
"the least-measured competency" a well-defined question. Partial credit uses a fractional
likelihood that reduces exactly to the Bernoulli term at `s ∈ {0,1}, w = 1`, so the MCQ path
is unchanged by construction rather than by inspection — and at `w = 0` it is identically 1,
so an infrastructure failure moves nothing.
