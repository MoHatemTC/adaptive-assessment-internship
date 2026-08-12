# `cat_engine/ingest/`

One uploaded file becomes a registered bank. **The only write path.**

## One file: the questions

An author uploads questions. They do not author a competency graph and are never asked for
one, so the graph is DERIVED from the items — nodes from `measures[].variable`, titles from
each item's own `sub_competency` label, weights from item co-measurement, and every
prerequisite edge inert.

The optional `competencies` block states the two things a file of questions cannot imply:
which sub-competencies are critical, and which serve more than one main. It has no edges, so
it is still not a graph.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | `Ingest.register`: parse, derive, validate, and — when asked — write, in that order. `write=False` is the identical path with the last step omitted rather than a second implementation that could drift. |
| `derive.py` | The derivation. Where the modelling decision lives, and the only file that has to change to replace it. `RELATION_THRESHOLD` and `EDGE_FLOOR` are read here at ingest time. |
| `uploads.py` | What an upload was, kept long enough to say why it failed. The raw bytes are retained: a rejection that cannot be reproduced from the original input is a support ticket with no evidence in it. |

## Two things this guarantees

**Uploading is idempotent.** A bank is identified by its id and its content, so the same
bytes under the same id produce the same version, whether that is the first upload or the
fifth. Sessions already in flight are unaffected — each pins the version it began under.

**Validation is reused, not reimplemented.** `BankStore.validate` is the same function the
checked-in banks are asserted against, so a bank arriving as an upload clears exactly the bar
the seeds clear. A second implementation of those rules would mean the engine's own suite was
testing the seeds rather than the system.
