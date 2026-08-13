# The API

Written for someone building on the module — most likely putting HTTP routes over it for a
frontend. It is the part a signature cannot tell you: which calls to make in which order,
and why some things are deliberately not in the return value.

For the signatures themselves — every method, every parameter, every returned field, every
exception — see [`METHODS.md`](../../METHODS.md) at the repository root. This page is the
narrative; that one is the reference, and a test keeps it complete.

This was seven HTTP services and this page was the contract a frontend built against. It is
the same contract; the transport is the host's now. Every error `code` is unchanged, so a
frontend written against the old API branches correctly against a host that maps
`CatError` back onto responses.

```python
from cat_engine import AssessmentModule, CatConfig

cat = AssessmentModule(CatConfig(active_bank="AIE"))
```

---

## The candidate flow

```
await cat.begin(...)                     -> session_id, presenting
   |
   +--> state.presenting.item           render by modality
   |
await cat.answer(session_id, ...)        -> presenting (the next one), or report
   |
   '--> repeat until state.stop
```

Every lifecycle call returns the **same shape**, so there is one thing to render and one
place to look for "what now": `presenting` when there is a question, `report` when there is
not.

### Begin

```python
state = await cat.begin(
    bank_id="AIE",             # omit for the configured active bank
    target_variables=None,     # omit to assess every competency the bank measures
    intake={"C1": 3},          # self-rating 1-5, seeds a narrower prior
    confidence={"C1": True},   # is that rating trusted? untrusted seeds a wide prior
    use_llm=True,
)
```

The `bank_id` and the bank **version** are pinned to the session from here on — you never
pass them again, and a bank replaced mid-session cannot change the pool underneath the
candidate.

### Begin, narrowed to some competencies

An assessment can cover part of a bank instead of all of it, at main **or** sub-competency
granularity. Pass `scope` instead of `target_variables` — the two are mutually exclusive, and
passing both raises `ScopeInvalid` with code `scope_and_targets`.

```python
state = await cat.begin(scope=["C1.1", "C1.4", "C6"])
```

**The selection travels, not a scope id.** The module builds the scope itself and pins what
it built, so it never applies an item allowlist nobody in that process derived. If you called
`cat.scope(...)` first — to preview the selection or draw it — you get the identical
`scope_id`, because the manifest is a pure function of the bank version and the selection.

Three things to know before offering this in a UI:

**A selection widens.** Sub-competencies can be shared: `C1.6` serves both `C1` and `C6`, so
selecting `C6` whole necessarily measures something that evidences `C1`, and `C1` is
therefore opened too. It has to be — an outcome whose main is not in the session is discarded
— but the report marks such a main `implied`, so you can tell it from one the user chose.

**A partially covered main is estimated from part of itself.** Ability is estimated per main;
sub-competencies have no estimate of their own. The report says which mains were partial and
how much of each was retained — do not present a partial main's level beside a whole-bank one
without saying so.

**Preview it first if the selection is user-built.** `cat.scope(...)` returns
`coverage.reachable == False` with the offending competencies named when a selection cannot
be assessed within the question budget. `begin` raises `ScopeUnassessable` on the same
selection, but by then you have a user staring at an error instead of a picker telling them
what to add.

```python
manifest = cat.scope(["C1.1", "C1.4", "C6"])
drawing  = cat.scope_graph(["C1.1", "C1.4", "C6"])   # mermaid
```

Do not pass `seed` in production. It exists for deterministic replay; omitting it draws OS
entropy, and exposure control must not be predictable.

### Render

`state.presenting.item.modality` decides what to draw:

| modality | fields |
|---|---|
| `mcq` | `stem`, `options` |
| `code` | `prompt`, `language`, `function_name`, `starter_code` |
| `open` / `voice` | `question`, `answer_format`, `spoken` |

`spoken` is how you tell a voice item from a typed one without inspecting anything.
`starter_code` may be a deliberately incomplete scaffold — that is authored, not a bug.

### Answer

```python
await cat.answer(session_id, mcq=2)
await cat.answer(session_id, code="def solve(x): ...")
await cat.answer(session_id, transcript="...")
await cat.answer(session_id, audio=raw_bytes, audio_filename="answer.wav")
```

The answer must match the modality currently presented — a mismatch raises
`AnswerTypeMismatch` rather than being silently mis-graded.

The returned `last_graded` is an **acknowledgement, not a result**:

```python
GradeReceiptDTO(item_id=..., modality="mcq", accepted=True, flags=[])
```

No score, no correctness, no detail. That is not squeamishness — feedback after each answer
changes what the assessment measures. A candidate told which hidden test failed learns
something between question four and question five, and the estimate that comes out is of a
person who was being taught mid-measurement.

`flags` carries exactly one class of thing: infrastructure. `SANDBOX_UNAVAILABLE` or
`PACKAGE_INFRASTRUCTURE_ERROR` mean their answer moved no estimate and they may be asked
again. Show that. It is the one thing they are entitled to know while answering.

### Finish

When `state.stop` is true, `state.report` is populated. `cat.report(session_id)` returns it
and raises `assessment_running` before then.

**Report the band RANGE, not the point band.** At the standard-error target, within-one-level
accuracy runs about 99% while exact-band accuracy runs about 65%. A UI that renders
`level: 3` alone is asserting a confidence the measurement does not support; render
`credible_interval_95` or the neighbouring levels with it.

`decision_status` is `provisional` for every measured competency and will stay that way until
external exact-band calibration passes. It is not a placeholder — do not render it as a
certified result.

### The scope, on the report

A scoped assessment carries a `scope` block on its report. A whole-bank one carries `None`.

```json
"scope": {
  "scope_id": "scp_7f3a…",
  "selected": ["C1.1", "C1.4", "C6"],
  "partial_mains": ["C1", "C3"],
  "retained_weight_by_main": {"C1": 0.5713, "C3": 0.1818, "C6": 1.0}
}
```

`retained_weight_by_main` is the share of a main's declared competency mass the scope kept. It
is a **coverage statement, not a confidence** — it does not enter any estimate. It is there so
a report can say what a level covers, and it is the number to show beside any main listed in
`partial_mains`.

---

## Errors

One hierarchy. `detail` is for a person; `code` is for your `if`; `status_code` is the answer
the module already knows, so a host does not have to guess it:

```python
from cat_engine.errors import CatError

try:
    state = await cat.answer(session_id, mcq=choice)
except CatError as exc:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, exc.status_code)
```

The ones worth handling by name:

| exception | code | status | what to do |
|---|---|---|---|
| `StaleAnswer` | `stale_answer` | 409 | a double-submit; re-read the session and render what it shows |
| `AnswerTypeMismatch` | `answer_type_mismatch` | 422 | a client bug — the wrong modality was submitted |
| `AnswerMissing` / `AnswerInvalid` | `answer_missing` / `answer_invalid` | 422 | show the message, let them answer again |
| `GraderUnavailable` | `grader_unavailable` | 503 | nothing was recorded; the same item can be retried |
| `CapacityReached` | `capacity_reached` | 503 | no new session right now; existing ones are unaffected |
| `AssessmentUnknown` | `assessment_unknown` | 404 | the session expired or was never created |
| `ScopeUnassessable` | `scope_unassessable` | 422 | the selection cannot be assessed; the detail names why |
| `DiagnosticsDisabled` | `diagnostics_disabled` | 404 | the author surface is off on this deployment |
| `ConfigConflict` | `config_conflict` | 500 | a second module disagreed about measurement policy |

Capacity fails **closed**: at the limit a new assessment is refused rather than an in-progress
one being evicted.

---

## Spoken answers

Two ways, and the second is better where you can use it.

**Typed transcript.** Pass `transcript=` and you are done. Pass `audio=` and it is transcribed
first.

**A real interview.** `cat.live` runs the realtime bridge as a Python API; the socket between
it and a browser is yours:

```python
room = cat.live.create(item_id, question, assessment_session_id=session_id)
await room.connect()
async for event in room.events():        # {"type": "audio"|"status"|"done"|"error", ...}
    ...
await room.push_pcm(chunk)
result = await room.finish()
```

`cat_engine.live.STATIC_DIR` holds a reference interview page and its audio worklet. The
worklet must be served from the same origin as the page, so serve these files rather than
reimplementing them.

Pass `assessment_session_id` so the interview trace groups with the rest of the session. The
room produces a transcript; you then submit it as a normal answer. **The room does not
grade** — that separation is why an audio failure can be reported as an audio failure rather
than as a candidate who said nothing.

---

## Registering a bank

An author uploads **questions**. The competency graph is derived from them — an author does
not write one and is never asked for one.

```python
receipt = cat.upload_bank("MY-BANK", path.read_bytes())
if receipt.status == "rejected":
    show(receipt.error, receipt.validation.findings)
```

A rejected bank comes back as a receipt rather than an exception, because the caller is
usually an authoring tool and the useful answer is which item is at fault. Findings name the
item, node or competency. `cat.validate_bank(...)` runs every check and writes nothing.

Uploading is **idempotent**: the same bytes under the same id produce the same version,
whether that is the first upload or the fifth. Sessions already in flight are unaffected —
each pins the bank version it began under. `cat.delete_bank(id)` removes a stored bank, and a
checked-in one of the same id reappears underneath it.

### What gets refused

Errors, all of which would otherwise surface as an unexplainable assessment:

- item parameters out of range — `a` above 3.0 does not rank oddly, it ranks meaninglessly
- duplicate item ids, or no active item at all
- a measured variable with no node — invisible to coverage and to blocking
- a required node no active item measures — an unsatisfiable gate
- more required nodes than the question budget can cover — the gate could never be satisfied
  and every session would end on the budget escape, reporting a stop it never chose

Warnings never block. The optional `competencies` block in the file states the two things a
file of questions cannot imply: which sub-competencies are critical, and which serve more
than one main.

**There is no authentication on this path.** See `docs/operations.md`. Set
`ADMIN_API_ENABLED=false` and `INGEST_API_ENABLED=false` wherever writing is not needed.

---

## The author view

`cat.diagnostics(session_id)` and `cat.raw_state(session_id)`, both raising
`DiagnosticsDisabled` unless `author_diagnostics_enabled`.

They carry the posterior, the shortlist, the criterion phase and which item the engine would
have picked — enough to reverse-engineer difficulty, and enough for a candidate to tell how
they are doing while still being measured. **Never expose them on a candidate-facing
deployment.** They exist so an assessment author can see why an item was chosen; without
them, an assessment nobody can audit mid-flight.
