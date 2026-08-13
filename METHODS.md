# `cat_engine` — every method, its inputs and its outputs

The complete callable surface of the module, one entry per method: what it takes, what it
returns, and what it raises. `cat_engine/docs/api.md` says which calls to make in which
order and why; this says exactly what each one is.

Everything here is reached from three objects and one exception hierarchy:

```python
from cat_engine import AssessmentModule, SyncAssessmentModule, CatConfig
from cat_engine.errors import CatError

cat = AssessmentModule(CatConfig(active_bank="AIE"))
```

Conventions used throughout:

- **`bank_id=None`** always means *the configured active bank*, never *all banks*.
- **`use_llm=True`** uses the model; `False` runs the deterministic heuristic. `False` is for
  tests and offline replay, not a degraded mode a deployment should sit in.
- Methods marked **`async`** must be awaited on `AssessmentModule`. On
  `SyncAssessmentModule` the identical name is a blocking call.
- Every failure this module models is a `CatError` subclass carrying `.detail` (for a
  person), `.code` (for your `if`) and `.status_code`. Anything else escaping is a bug.

---

## 1. Construction

### `AssessmentModule(config=None, *, sessions=None)`

| input | type | default | meaning |
|---|---|---|---|
| `config` | `CatConfig \| None` | `None` | Explicit settings. `None` reads everything from the environment and the defaults. |
| `sessions` | `SessionStore \| None` | `None` | A host's own session storage. `None` selects one from `session_database_url`: a DSN gives `SqlSessionStore` behind a write-through cache, no DSN keeps sessions in this process only. |

**Returns** a configured module. **Raises** `ConfigConflict` (500, `config_conflict`) if a
module already exists in this process with different **measurement** policy — engine settings
are a process-wide singleton, so two modules disagreeing about what a candidate is scored by
would silently rescore the first. Topology (store locations, open surfaces, upload ceilings)
is per-instance and may differ freely.

**Attributes it exposes**

| attribute | type | what it is |
|---|---|---|
| `.config` | `CatConfig` | What was passed in. |
| `.settings` | `ModuleSettings` | Resolved module topology and surfaces (§10). |
| `.sessions` | `SessionStore` | The live session store (§9). |
| `.wiring` | `Wiring` | Builds and caches an `Orchestrator` per `(bank_id, version, scope_hash)`. Internal; documented in §11. |
| `.ingest` | `Ingest` | The bank write path. Internal; reached through `upload_bank`. |
| `.live` | `Live` | Interview rooms (§8). |

### `AssessmentModule.live` *(property)*

**Input** none. **Returns** `Live`, built on first access.

Lazy on purpose: `cat_engine.live` reaches a realtime SDK that is an **optional** dependency
(`pip install cat-engine[live]`). Building it eagerly made `from cat_engine import
AssessmentModule` fail with `No module named 'google'` for every host that never runs an
interview. **Raises** `LiveUnavailable` (503, `live_unavailable`) on first *use* if the extra
is not installed.

---

## 2. Catalogue — reading what a bank holds

Read-only, no session involved. Safe to call before `begin`.

### `banks() -> list[BankSummary]`

**Input** none. **Returns** one row per registered bank. A bank that could not be read is
listed *with its error* rather than omitted — a broken bank must not hide the working ones
from a picker.

`BankSummary`: `bank_id`, `title`, `version` (content hash over items, graph and profile),
`mains: list[str]`, `items: int`, `modalities: list[str]`, `has_graph: bool`,
`coverage_critical_only: bool`, `source: "seed" | "stored"`, `error: str`.

### `bank(bank_id=None) -> BankSummary`

| input | type | default |
|---|---|---|
| `bank_id` | `str \| None` | `None` → the active bank |

**Returns** the single `BankSummary`. **Raises** `BankUnknown` (400, `bank_unknown`).

### `items(bank_id=None) -> list[BankItemFull]`

**Returns** every item **with its payload** — stems, options, answer keys, hidden tests.
For rendering and authoring, **never for selection**.

`BankItemFull` = `BankItemRef` + `status`, `competency`, `sub_competency`, `payload: dict`.
`BankItemRef` is `item_id`, `modality` (`"mcq" | "code" | "open" | "voice"`),
`measures: list[{variable, weight}]`, `cat: {a, b, c}`, `estimated_time_seconds`,
`minimum_success_confidence`, `status`.

The split is the security boundary of the bank surface: selection ranks the whole eligible
pool on every step and holds refs only, so it *cannot* read a question.

### `item(item_id, bank_id=None) -> BankItemFull`

| input | type | default | note |
|---|---|---|---|
| `item_id` | `str` | — | **first** positional argument here, unlike `catalogue.item(bank_id, item_id)` |
| `bank_id` | `str \| None` | `None` | |

**Raises** `BankUnknown` (400, `bank_unknown`) — for an unregistered bank *and* for an item
the bank does not hold; the `detail` says which.

### `graph(bank_id=None) -> CompetencyGraphDTO | None`

**Returns** the bank's competency graph, or `None` when it has none.

`CompetencyGraphDTO`: `schema_version`, `nodes: list[GraphNodeDTO]`,
`edges: list[GraphEdgeDTO]`, `policy: dict`.
`GraphNodeDTO`: `competency_id`, `title`, `node_type` (`"main" | "sub_competency"`),
`critical`, `context_specific`, `main_competencies`, `metadata`.
`GraphEdgeDTO`: `from`/`to` (attributes `from_id`/`to_id`), `relation`, `strength`, `weight`,
`allow_upward_inference`, `allow_downward_blocking`, `metadata`.

### `policy(bank_id=None) -> PolicyDTO | None`

**Returns** the **resolved** propagation policy, so an operator can ask "why is this edge
inert?" without reading three files and doing the AND by hand.

`PolicyDTO`: `prerequisite_edges`, `inference_enabled`, `blocking_enabled`,
`minimum_failures_to_block`, `deployment: dict`, `bank_policy: dict`,
`inert_because: dict[str, int]`, `edges: list[EdgeDecisionDTO]`.
`EdgeDecisionDTO` names, per edge: `parent`, `child`, `validation_status`,
`inference_allowed`, `blocking_allowed`, `inference_authorised_by_bank`,
`blocking_authorised_by_bank`, `inference_blocked_by`, `blocking_blocked_by` — deployment
governs *enforcement*, bank and edge govern *validity*, and the two are reported separately.

---

## 3. Scoping — narrowing an assessment before it begins

### `scope(selected, *, bank_id=None, critical_only=None, include_prerequisites=False) -> ScopeManifest`

| input | type | default | meaning |
|---|---|---|---|
| `selected` | `list[str]` | — | Node ids at **main or sub-competency** granularity, mixed in one list: `["C1.1", "C1.4", "C6"]`. A selected main expands to its sub-competencies; a selected sub-competency stands alone. An id the bank does not declare lands in `rejected` rather than raising. |
| `bank_id` | `str \| None` | `None` | |
| `critical_only` | `bool \| None` | `None` | `None` defers to the bank's own coverage policy. `True` narrows the coverage requirement to the critical set. |
| `include_prerequisites` | `bool` | `False` | Pull in prerequisite ancestors. Off by default: those edges ship inert because their inference rate was measured at 22.4% wrong against a <3% gate, so enlarging an assessment on their authority is the one use they were found unfit for. |

**Returns** `ScopeManifest`:

| field | type | meaning |
|---|---|---|
| `scope_id`, `scope_hash` | `str` | Hash over bank version + normalised selection. Deterministic — a preview and the session's own scope agree by construction. |
| `bank_id`, `bank_version` | `str` | Lets a session refuse a scope built against a bank since replaced. |
| `selected`, `include_prerequisites` | | Echoed back. |
| `nodes` | `list[ScopeNodeDTO]` | `node_id`, `title`, `node_type`, `critical`, `main_competencies`, `item_count` (active items **within the scope**; zero here is what makes a scope unreachable). |
| `edges` | `list[ScopeEdgeDTO]` | Retained edges, with `weight` and `weight_original` — renormalising `CONTRIBUTES_TO` over a subset changes what a main's mass means, and a change nobody can inspect is a change nobody can review. |
| `mains` | `list[ScopeMainDTO]` | `main`, `title`, `implied`, `partial`, `retained_weight`, `required`, `excluded`. |
| `item_ids` | `list[str]` | The allowlist. **Ids only** — never refs, never payloads. |
| `item_count_by_modality` | `dict[Modality, int]` | |
| `coverage` | `ScopeCoverageDTO` | `reachable`, `question_budget`, `critical_only_applied`, `required_by_main`, `unserved` (required nodes no active item measures), `over_budget` (mains requiring more than the budget could cover). |
| `rejected` | `list[str]` | Selected ids the bank does not declare, or declares with no active item. |
| `dangling_prerequisites` | `list[DanglingEdgeDTO]` | Edges with exactly one endpoint in scope: `from`, `to`, `relation`, `direction` (`"leaves"`/`"enters"`). Reported, not dropped. |
| `.assessable` | `bool` *(property)* | `coverage.reachable and item_ids and mains`. |

**Raises** `BankUnknown` (400, `bank_unknown`). Never raises for an unassessable selection —
that is the point:
a picker needs `coverage.reachable == False` with the offending competencies named, not an
exception. `begin` refuses the same selection, but by then a user is staring at an error.

**A selection widens.** Sub-competencies can be shared — `C1.6` serves both `C1` and `C6`, so
selecting `C6` whole necessarily measures something that evidences `C1`, and `C1` is opened
too and marked `implied`. It has to be: an outcome whose main is not in the session is
discarded.

### `scope_graph(selected, **kwargs) -> str`

Same inputs as `scope` (kwargs forwarded verbatim). **Returns** the induced sub-graph as
**mermaid** source, rendered from the manifest that produced the allowlist so the drawing and
the allowlist cannot disagree. Excluded siblings are drawn greyed rather than omitted — a
picture of the retained nodes alone makes two-of-seven look like a whole competency.

---

## 4. Lifecycle

All three of `begin`, `answer` and `state` return the **same shape**, so a host has one thing
to render and one place to look for "what now".

`AssessmentStateResponse`:

| field | type | meaning |
|---|---|---|
| `session_id` | `str` | |
| `bank_id`, `bank_version` | `str` | Pinned at `begin`, never sent again. |
| `stop` | `bool` | |
| `stop_reason` | `str` | Populated only when `stop`. |
| `items_administered` | `int` | |
| `open_variables` | `list[str]` | Competencies not yet finalised. |
| `presenting` | `PresentingDTO \| None` | The question, when there is one. |
| `last_graded` | `GradeReceiptDTO \| None` | Set by `answer` only. |
| `report` | `AssessmentReportDTO \| None` | Populated when `stop`. |

`PresentingDTO`: `variable`, `criterion` (`"KL"` for the first three observations,
`"E[Fisher]"` thereafter), `item: PresentedItemDTO`.

`PresentedItemDTO` — the only item shape a candidate-facing response carries. **No
`answer_index`, no test cases, no `reference_solution`.** One flat model across modalities;
render by `modality` and the inapplicable fields are absent:

| always | `item_id`, `modality`, `competency`, `sub_competency`, `estimated_time_seconds` |
|---|---|
| `mcq` | `stem`, `options` |
| `code` | `prompt`, `language`, `function_name`, `starter_code` (may be a deliberately incomplete scaffold) |
| `open` / `voice` | `question`, `answer_format`, `spoken` |

### `async begin(*, bank_id=None, target_variables=None, scope=None, intake=None, confidence=None, use_llm=True, seed=None) -> AssessmentStateResponse`

| input | type | default | meaning |
|---|---|---|---|
| `bank_id` | `str \| None` | `None` | The bank **and its version** are pinned to the session here and never accepted again. A caller that began against one bank must not be able to answer it against another, and a bank replaced mid-session must not change the pool underneath a live candidate. |
| `target_variables` | `list[str] \| None` | `None` | Main competencies to assess. `None` assesses every main the bank measures. Mutually exclusive with `scope`. |
| `scope` | `list[str] \| ScopeSelection \| None` | `None` | The same idea at main **or** sub-competency granularity. The **selection** travels, not a `scope_id`: a hash cannot be inverted, so accepting one would mean applying an item allowlist nobody in this process derived. |
| `intake` | `dict[str, int] \| None` | `None` | Self-rating per main, 1–5. Seeds a narrower prior than flat. |
| `confidence` | `dict[str, bool] \| None` | `None` | Whether each self-rating is trusted. An untrusted rating seeds a wide prior — the difference between *using* intake and merely *recording* it. |
| `use_llm` | `bool` | `True` | Whether the picking agent may run. |
| `seed` | `int \| None` | `None` | **Do not pass in production.** Deterministic replay only; omitting draws OS entropy, and exposure control must not be predictable. |

**Returns** the state with `presenting` set to the first question.

**Raises**

| | |
|---|---|
| `ScopeInvalid` (400, `bank_unknown`) | no such bank |
| `ScopeInvalid` (400, `scope_and_targets`) | both `scope` and `target_variables` were passed |
| `ScopeInvalid` (400, `targets_invalid`) | a named target is not a main this bank measures |
| `ScopeUnassessable` (422, `scope_unassessable`) | the selection cannot be assessed; `detail` names which competencies are unmeasurable, unserved or over budget |
| `CapacityReached` (503, `capacity_reached`) | at the session limit. Fails **closed**: a new assessment is refused rather than an in-progress one evicted |

Note that when `scope` is given, the targets are **derived from the manifest's mains** and are
always mains. A sub-competency must never become a target: outcomes are folded to
`variable.split(".")[0]` and dropped when that main is not in the session, so a session begun
on `["C1.1"]` would run to the question cap with its standard error exactly where it started,
raising nothing.

### `state(session_id) -> AssessmentStateResponse`

| input | type |
|---|---|
| `session_id` | `str` |

**Returns** where the assessment is. Safe to poll — the finished-session record is written
once. **Raises** `AssessmentUnknown` (404).

### `next_item(session_id) -> PresentingDTO`

For a caller that wants only the question. **Raises** `AssessmentUnknown`, or `CatError`
(409, `assessment_stopped`) once the assessment has ended — returning nothing would read as
"not yet" rather than "this is over".

### `report(session_id) -> AssessmentReportDTO`

**Raises** `AssessmentUnknown`, or `CatError` (409, `assessment_running`) before the
assessment stops, because a report before then would state a measurement that has not been
made.

`AssessmentReportDTO`: `session_id`, `items_administered`, `variables:
list[VariableReportDTO]`, `all_finalised`, `stop_reason`, `graph: dict` (node-level
provenance, populated whatever the enforcement flags say — a report that only appears once a
feature is on cannot inform the decision to turn it on), `aberrant_responses: list[dict]`,
`seconds_by_item: dict[str, float]`, `scope: ScopeSummaryDTO | None`.

`VariableReportDTO` — one competency's result:

| field | meaning |
|---|---|
| `variable`, `theta_hat` | The ability estimate (posterior mean, EAP). |
| `standard_error` | The **reported** value, widened when a calibration correction applies. |
| `standard_error_raw` | The posterior's own — what the stopping rule actually read. Both are present because a single widened number cannot be checked against the rule that produced the session, and a single raw number is the overconfident one. |
| `interval_widening_factor` | |
| `precision_index_pct` | A monotone remap of the posterior SD. **Not** the probability the reported level is correct — at SE 0.55 this reads 90 while P(correct band) is about 0.64 at a band centre and 0.47 near a boundary. |
| `certainty_pct` | |
| `level`, `band` | The reported level. **Report the band range, not the point band** — see the note below. |
| `p_reported_band` | P(the reported band is the true one). This is the number a reader assumes `precision_index_pct` is giving them. |
| `band_probability`, `most_probable_band` | |
| `credible_interval_95` | `(low, high)` on the θ scale. |
| `aberrant_response_count` | Responses the posterior could not explain. |
| `graph_coverage_satisfied`, `graph_unmeasured_nodes` | |
| `observations`, `finalised`, `converged` | |
| `decision_status` | `"not_assessed" \| "provisional" \| "certified"`. **Permanently `provisional`** until external exact-band calibration passes. Not a placeholder — do not render it as a certified result. |
| `stop_reason`, `modalities_used` | |

At the standard-error target, within-one-level accuracy runs about 99% while **exact-band
accuracy runs about 65%**. A UI that renders `level: 3` alone asserts a confidence the
measurement does not support; render `credible_interval_95` or the neighbouring levels with
it.

`ScopeSummaryDTO` (present only on a scoped assessment): `scope_id`, `scope_hash`,
`selected`, `partial_mains`, `retained_weight_by_main`. The last is the share of a main's
declared competency mass the scope kept — a **coverage statement, not a confidence**. It
enters no estimate. It exists so a report can say what a level covers.

### `discard(session_id) -> bool`

**Returns** `True`. **Raises** `AssessmentUnknown` (404) if there was nothing to discard.

---

## 5. Answering

### `async answer(session_id, *, mcq=None, code=None, transcript=None, audio=None, audio_filename="answer.wav", use_llm=True) -> AssessmentStateResponse`

| input | type | default | for |
|---|---|---|---|
| `session_id` | `str` | — | |
| `mcq` | `int \| None` | `None` | `mcq` items — the chosen option index |
| `code` | `str \| None` | `None` | `code` items — the submitted source |
| `transcript` | `str \| None` | `None` | `open` / `voice` items — the text |
| `audio` | `bytes \| None` | `None` | `open` / `voice` items — transcribed first, then treated as `transcript` |
| `audio_filename` | `str` | `"answer.wav"` | Only read when `audio` is given |
| `use_llm` | `bool` | `True` | Whether rubric grading and the picking agent may call the model |

Exactly one form is expected and it must match the modality currently presented.

**Returns** the next state, with `last_graded` set.

`GradeReceiptDTO`: `item_id`, `modality`, `accepted: bool`, `flags: list[str]`.

The receipt is an **acknowledgement, not a result** — no score, no correctness, no detail.
That is not squeamishness: feedback after each answer changes what the assessment measures. A
candidate told which hidden test failed learns something between question four and question
five, and the estimate that comes out is of a person who was being taught mid-measurement.

`flags` carries exactly one class of thing: infrastructure. `SANDBOX_UNAVAILABLE` and
`PACKAGE_INFRASTRUCTURE_ERROR` mean the answer moved no estimate and the candidate may be
asked again. Show that — it is the one thing they are entitled to know while answering.

**Raises**

| | |
|---|---|
| `AssessmentUnknown` (404) | no such session |
| `AnswerTypeMismatch` (422, `answer_type_mismatch`) | a `code` payload against an `mcq` item and so on. A client bug, refused rather than silently mis-graded — grading an `mcq` payload against a `code` item produces a *number*, not an error |
| `AnswerMissing` (422, `answer_missing`) | empty source, or neither transcript nor audio |
| `AnswerInvalid` (422, `answer_invalid`) | the answer cannot be graded as submitted |
| `StaleAnswer` (409, `stale_answer`) | the presented item changed while this answer waited — a double-submit, or two clients on one assessment, or another replica recording first. Nothing was recorded |
| `GraderUnavailable` (503, `grader_unavailable`) | the sandbox or model gateway is unreachable. **Nothing was recorded**, so the same item can be retried — which is why this is not a graded response of weight 0 |

Concurrency: two answers to one session are serialised on a per-session lock, and the item
this answer is *for* is captured **before** waiting. Serialising alone is not enough — the
second call would otherwise wake up and apply its stale answer to the next item whenever the
modality happened to match.

---

## 6. Bank writes

### `upload_bank(bank_id, content, *, filename="bank.json", dry_run=False) -> UploadReceipt`

| input | type | default | meaning |
|---|---|---|---|
| `bank_id` | `str` | — | |
| `content` | `bytes \| str` | — | The bank file. `str` is encoded UTF-8. |
| `filename` | `str` | `"bank.json"` | Recorded on the receipt. |
| `dry_run` | `bool` | `False` | `True` runs the identical path and writes nothing. |

The only way a bank enters the system. An author uploads **questions**; the competency graph
is **derived** from them and never authored. Idempotent by nature — the same bytes under the
same id produce the same version, first upload or fifth. Sessions in flight are unaffected;
each pins the version it began under.

**Returns** `UploadReceipt` — a receipt rather than an exception on a bad bank, because the
caller is usually an authoring tool and the useful answer is *which item is at fault*:

`upload_id`, `status` (`"received" | "parsing" | "deriving" | "validating" | "rejected" |
"registered"`), `filename`, `bank_id`, `title`, `version`, `items`,
`validation: BankValidationReport | None`, `derived_graph: DerivedGraphDTO | None`,
`error: str` (set when the file could not be read as a bank at all — a different failure from
one that parsed and then failed a rule, and an author fixes them differently).

`BankValidationReport`: `bank_id`, `accepted`, `version`, `items`, `mains`, `modalities`,
`findings: list[ValidationFinding]`, `.errors` *(property)*. Each `ValidationFinding` is
`severity` (`"error"`/`"warning"`), `code`, `message`, `subject` — the item, node or
competency at fault. Warnings never block.

`DerivedGraphDTO`: `mains`, `sub_competencies`, `edges_by_relation`,
`prerequisite_edges_are_inert` (always `True`, asserted rather than assumed — an edge derived
from co-measurement has strictly less authority than one an expert authored, and the authored
ones were themselves measured wrong 22.4% of the time).

**Raises**

| | |
|---|---|
| `WritesDisabled` (403, `writes_disabled`) | `ingest_api_enabled` is off |
| `BankInvalid` (422, `upload_empty`) | the file is empty |
| `BankInvalid` (413, `upload_too_large`) | over `max_upload_bytes` |

A bank that parses and then fails a validation rule does **not** raise — it comes back as a
receipt with `status="rejected"`. The two are different failures and an author fixes them
differently.

### `validate_bank(bank_id, content) -> UploadReceipt`

Every check the write path runs, writing nothing. Exactly `upload_bank(..., dry_run=True)`.

### `delete_bank(bank_id) -> bool`

Removes a **stored** bank. A checked-in one of the same id reappears underneath it.
**Raises** `WritesDisabled` (403, `writes_disabled`) unless `admin_api_enabled`.

> There is no authentication on this path — the host owns that. Set `ADMIN_API_ENABLED=false`
> and `INGEST_API_ENABLED=false` wherever writing is not needed.

---

## 7. Grading primitives

Reachable without a session. For a candidate's own editor, and for transcription.

### `public_tests(item_id, bank_id=None) -> list[dict]`

**Returns** the example cases a candidate may run against — **public only**. If a trial run
could reach the hidden cases, a candidate could converge on a lookup table by trial and error
and the score would mean nothing. A case nobody explicitly marked public is treated as
hidden: an authoring slip must cost a candidate one example rather than void the question.

**Raises** `BankUnknown` (400) for an unknown bank or item, `AnswerTypeMismatch` (422) if the
item is not a `code` item.

### `trial_run(item_id, source, bank_id=None) -> TrialRunResponse`

| input | type | default |
|---|---|---|
| `item_id` | `str` | — |
| `source` | `str` | — |
| `bank_id` | `str \| None` | `None` |

Runs the candidate's code against the public cases. **Grades nothing** — there is no code
path from here into a learner model, not "a path not currently taken", none at all.

Without this, the first time a candidate's code ever executes is the moment it is graded,
which measures something other than competency: a misread signature they would have caught in
five seconds becomes a permanent observation about what they know.

`TrialRunResponse`: `available: bool` (`False` means the **sandbox** failed — not a statement
about the candidate's code and must not be shown as one), `compiled: bool | None`,
`cases: list[TrialCaseDTO]`, `error_message`, `passed: int`, `total: int`.
`TrialCaseDTO`: `test_id`, `arguments`, `expected`, `passed`, `detail`.

**Raises** `BankUnknown` (400), `AnswerTypeMismatch` (422) — same as `public_tests`.

### `transcribe(audio, *, filename="answer.wav") -> str`

| input | type | default |
|---|---|---|
| `audio` | `bytes` | — |
| `filename` | `str` | `"answer.wav"` |

**Returns** the transcript text, stripped. **Raises** `AnswerInvalid` (422, `audio_empty`) on
empty bytes.

Separate from grading because the two fail differently: a transcription failure is a retry, a
grading failure is an unscorable response that must move no estimate. Folding them together
would make the second look like the first.

---

## 8. Live interviews — `cat.live`

The room was never coupled to a transport, so it is a Python API and the socket is the
host's. **The room does not grade**: it produces a transcript, which is then submitted as a
normal answer. Keeping the two apart is why an audio failure can be reported as an audio
failure rather than as a candidate who said nothing.

### `live.create(item_id, question, *, assessment_session_id="", mode="interview", save_recording=False) -> RealtimeLiveRoom`

| input | type | default | meaning |
|---|---|---|---|
| `item_id` | `str` | — | |
| `question` | `str` | — | The opening prompt. |
| `assessment_session_id` | `str` | `""` | Pass it so the interview trace groups with the rest of the session. |
| `mode` | `str` | `"interview"` | `"interview"` or `"chat"`; anything else falls back to `"interview"`. |
| `save_recording` | `bool` | `False` | |

**Raises** `LiveUnavailable` (503, `live_unavailable`) when realtime audio is not configured
or the model session could not be opened.

### The room object

| method | input | output |
|---|---|---|
| `async connect()` | — | `None`. Opens the model session. |
| `async push_pcm(pcm)` | `bytes` — candidate audio, **PCM16 @ 16 kHz** | `None` |
| `async events()` | — | async iterator of `dict`: `{"type": "audio" \| "status" \| "done" \| "error", ...}`. `audio` carries interviewer PCM16 **@ 24 kHz** under `"data"`. |
| `async finish()` | — | `LiveInterviewResult` |
| `async set_turn_state(turn_state)` | `str` | `None` |
| `async activity_start()` / `activity_end()` | — | `None`. Manual turn markers. |
| `async mark_pause()` / `mark_resume()` | — | `None` |
| `turn_config()` | — | `dict` |
| `.room_id`, `.state` | — | `RealtimeRoomState`: `status`, `turn_state`, `item_id`, `mode`, `error`, `package`, `turns`, `result` |

`LiveInterviewResult`: `item_id`, `transcript`, `turns: list[dict]`, `total_speech_seconds`,
`outcome_status`, `reason_code`, `available`, `error_message`, `interviewer_wavs:
list[bytes]`, and `.as_package() -> VoiceResponsePackage`.

### The rest of `Live`

| method | input | output |
|---|---|---|
| `get(room_id)` | `str` | the room. **Raises** `RoomUnknown` (404, `room_unknown`) |
| `status(room_id)` | `str` | `dict`: `room_id`, `status`, `turn_state`, `item_id`, `mode`, `error`, `package`, `turns`, `transcript` — without holding the room object |
| `drop(room_id)` | `str` | `None` |
| `prune(*, now=None)` | `float \| None` | `int` — rooms pruned on the idle timeout |
| `config()` | — | `dict`: `gating_mode`, `stream_mode`, `thinking_pause_ms`, `turn_end_silence_ms`, `nudge_silence_ms`, `gate_open_db`, `gate_close_db`, `gate_open_frames`, `gate_close_frames`, `gate_barge_in_extra_db`, `model`, `via`. Chat always uses manual markers — server VAD is broken on the Live preview model |
| `debug_feed(limit=50)` | `int`, clamped to 1–200 | `list` of recent room events. **Raises** `RoomUnknown` unless `live_debug_api_enabled` — it carries candidate transcripts |
| `clear_debug_feed()` | — | `None`, same gate |

### `cat_engine.live.STATIC_DIR`

A `Path` to the reference interview page, its audio worklet and a chat page. The worklet must
be served from the **same origin** as the page, so serve these files rather than
reimplementing them.

---

## 9. Session storage — `SessionStore`

A `Protocol`. Implement it and pass it as `AssessmentModule(sessions=...)` to bring your own
retention policy or encryption at rest.

| method | input | output |
|---|---|---|
| `get(session_id)` | `str` | `Session \| None` |
| `add(session)` | `Session` | `None` |
| `save(session, *, finished=False)` | `Session`, `bool` | `None`. **Raises** `SessionConflict` when another worker moved the session first (compare-and-swap on a revision) |
| `drop(session_id)` | `str` | `bool` |
| `mark_finished(session_id)` | `str` | `None` |
| `lock_for(session_id)` | `str` | `asyncio.Lock \| None` |
| `at_capacity()` | — | `bool` |
| `__len__()` | — | `int` |

`Session` is a dataclass: `bank_id`, `bank_version` (pinned, never refreshed), `state`,
`rng` (the one exposure-control stream this assessment owns — persisted rather than
recreated, because a fresh generator per request would make selection repeat its first draw
on every question), `scope`, `lock`, `last_access`, `finished_at`, `recorded`.

### `open_session_store(dsn="") -> SessionStore`

`""` returns `InMemorySessionStore` — a supported single-replica arrangement, not a fallback:
a restart loses every assessment mid-answer and a second worker cannot serve one this one
started. A DSN returns `InMemorySessionStore(SqlSessionStore(dsn))`, schema ensured.

---

## 10. Configuration — `CatConfig`

Three layers, in this order: **`CatConfig(...)` wins**, then the **environment**, then the
**defaults**. Every field is optional, and unset means *do not override* — which is what lets
a host set three things and leave the rest alone.

`model_config = extra="forbid"`: a misspelled field raises at construction rather than being
silently ignored.

### Fields

**Model access and sandbox**

| field | type | meaning |
|---|---|---|
| `litellm_base_url` | `str` | The gateway every model call goes through. |
| `litellm_api_key` | `str` | |
| `litellm_model` | `str` | Rubric grading, code interpretation and the picking agent. |
| `litellm_live_preview_model` | `str` | The realtime interview model. |
| `litellm_transcribe_model` | `str` | ASR for `transcribe` and for `audio=` answers. |
| `e2b_api_key` | `str` | The sandbox. The only component that executes candidate code. |

**Which bank**

| field | type | meaning |
|---|---|---|
| `active_bank` | `str` | What `bank_id=None` resolves to everywhere. |

**Measurement policy — in the fingerprint, one per process**

| field | type | meaning |
|---|---|---|
| `cat_se_target` | `float` | The standard-error target. Conjunctive with the band-probability target. |
| `cat_max_questions`, `cat_min_questions` | `int` | Per-variable question bounds. |
| `cat_aberrance_drives_verification` | `bool` | Make a response the posterior could not explain cost one more observation before the competency may claim it converged. Off by default. |
| `code_approach`, `code_rubric` | `str` | |
| `competency_graph_enabled` | `bool` | |
| `graph_upward_inference_enabled` | `bool` | Ships **off** — measured 22.4% wrong against a <3% gate. |
| `graph_descendant_blocking_enabled` | `bool` | Ships **off**. |
| `graph_convergence_gate_enabled` | `bool` | The coverage gate. Ships **on**. |
| `graph_coverage_critical_only` | `bool` | |
| `orchestrator_max_items` | `int` | Whole-assessment question budget. |
| `orchestrator_time_limit_minutes` | `int` | |

**Voice**

| field | type | meaning |
|---|---|---|
| `allow_text_fallback` | `bool` | Typed answers to spoken items. Keeps remote and accessibility testing possible when realtime audio is unavailable. It is not the measurement the item was authored for. |

**Where data lives**

| field | type | default | meaning |
|---|---|---|---|
| `engine_data_dir` | `str` | packaged data | Banks, graphs and rubrics. Set into the **environment**, not onto a settings object, because `engine/config/paths.py` reads it directly and must resolve before anything opens a file. |
| `bank_store_dir` | `str` | `""` | Where banks are read from on disk. Empty keeps the packaged file store. |
| `bank_database_url` | `str` | `""` | Where banks **live** when they live in Postgres. May be set **together with** `bank_store_dir`, and both then mean something: the DSN decides where banks live, the directory is where the SQL store materialises them to read. |
| `session_database_url` | `str` | `""` | Empty keeps sessions in this process — a supported single-replica arrangement, and it means a restart loses every assessment mid-answer. Deliberately a separate url from `bank_database_url` even when both point at one server: a bank is content that is published and kept, a session is a record of what a person answered and has a deletion deadline. One url for both makes the deadline somebody's afterthought. |
| `session_keep_responses` | `bool` | `False` | `False` blanks the per-response detail as soon as an assessment ends and keeps the report. The report is what anybody reads afterwards; the responses are what make the row personal data. |

**Surfaces**

| field | type | default | meaning |
|---|---|---|---|
| `author_diagnostics_enabled` | `bool` | `False` | `diagnostics()` and `raw_state()`. Not candidate-safe. |
| `admin_api_enabled` | `bool` | `True` | `delete_bank`. |
| `ingest_api_enabled` | `bool` | `True` | `upload_bank`. |
| `live_debug_api_enabled` | `bool` | `False` | The live debug feed. It carries candidate transcripts. |

**Ingest tuning**

| field | type | default | meaning |
|---|---|---|---|
| `max_upload_bytes` | `int` | 32 MiB | |
| `max_retained_uploads` | `int` | `200` | |
| `relation_threshold` | `float` | see `ingest/derive.py` | Modelling decision, read at **ingest** time. |
| `edge_floor` | `float` | see `ingest/derive.py` | Likewise. Changing either does not re-derive an existing bank — it changes what the next upload produces. |

### Methods

| method | input | output |
|---|---|---|
| `overrides()` | — | `dict[str, Any]` — only the fields actually set |
| `apply()` | — | `ModuleSettings`. Routes each field to whichever object owns it (engine `Settings`, `voice_settings`, or `ModuleSettings`) and records the measurement fingerprint. **Raises** `ConfigConflict` (500) if a module with different measurement policy already exists — checked **before** anything is mutated, so a rejected second module leaves the first one's configuration exactly as it was |

### `cat_engine.config.applied_fingerprint() -> str | None`

The 12-hex-character measurement fingerprint in force, or `None` before any module is built.
It covers 41 measurement settings and deliberately excludes topology — folding "the bank
directory moved" and "the stopping rule changed" into one hash produces a hash that gets
ignored within a week.

---

## 11. Author surfaces — gated, never candidate-safe

Both raise `DiagnosticsDisabled` (404, `diagnostics_disabled`) unless
`author_diagnostics_enabled`. **Refused, not thinned** — returning a reduced version would
make "is this safe to return" a judgement call at every call site.

### `diagnostics(session_id) -> DiagnosticsResponse`

`session_id`, `bank_id`, `variables: list[VariableDiagnosticsDTO]`, `queue: dict` (the full
queued candidate per open variable — criterion, information, utility, what the engine would
have picked, whether the model overrode it and why), `presenting: dict | None`, `graph: dict`,
`elapsed_minutes`, `seconds_by_item`, `aberrant_responses`.

`VariableDiagnosticsDTO`: `variable`, `theta_hat`, `standard_error`, `precision_index_pct`,
`level`, `band`, `credible_interval_95`, `observations`, `finalised`, `converged`,
`stop_reason`, `criterion`, `served_item_ids`, `score_history`, `band_history`,
`presenting_information` (what the current item carries for this variable — what makes a
selection auditable rather than merely logged), `graph_unmeasured_nodes`.

### `raw_state(session_id) -> AssessmentState`

The whole session object, unprojected — the engine's own type, deliberately, because a
projected copy here would be a second definition that drifts from the one sessions are stored
in. For replay, for an appeal, and for debugging a selection nobody can explain.

Together these carry the posterior, the shortlist and which item the engine would have picked
— enough to reverse-engineer difficulty, and enough for a candidate to tell how they are doing
while still being measured. **Never expose them on a candidate-facing deployment.**

---

## 12. `SyncAssessmentModule`

`SyncAssessmentModule(config=None, **kwargs)` — same inputs as `AssessmentModule`.

Every method above is available under the same name with the same inputs and outputs. The
three coroutines (`begin`, `answer`, and any awaited grading) are wrapped in `asyncio.run`;
everything else passes straight through.

Called from inside a running event loop it **raises `RuntimeError`** with a message saying to
use `AssessmentModule` instead, rather than deadlocking.

`.live` is **not** wrapped. A room is a duplex audio stream and there is no synchronous form
of one; a host that wants interviews needs a loop, and `.live` is exposed unchanged so it can
use one where it has it.

---

## 13. Errors

One hierarchy, one `except` clause. `detail` is the sentence a person reads, `code` is the
stable greppable identifier a caller branches on (unchanged from when this was seven HTTP
services), `status_code` is the answer the module already knows so a host does not have to
guess it.

```python
except CatError as exc:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, exc.status_code)
```

| exception | code | status | what it means |
|---|---|---|---|
| `CatError` | `internal_error` | 500 | the base; anything else escaping is a bug |
| `AssessmentUnknown` | `assessment_unknown` | 404 | expired, discarded, or never existed |
| `CapacityReached` | `capacity_reached` | 503 | fails closed — in-progress assessments are unaffected |
| `StaleAnswer` | `stale_answer` | 409 | double-submit or two clients; nothing was recorded |
| `AnswerTypeMismatch` | `answer_type_mismatch` | 422 | wrong modality submitted — a client bug |
| `AnswerInvalid` | `answer_invalid` | 422 | ungradeable as submitted |
| `AnswerMissing` | `answer_missing` | 422 | subclass of `AnswerInvalid` |
| `ScopeUnassessable` | `scope_unassessable` | 422 | the detail names why |
| `ScopeInvalid` | `scope_invalid` | 400 | also raised with `bank_unknown`, `scope_and_targets`, `targets_invalid` |
| `ScopeUnavailable` | `scope_unavailable` | 503 | scoped assessments disabled — refused rather than silently widened to the whole bank |
| `BankUnknown` | `bank_unknown` | 400 | |
| `BankInvalid` | `bank_invalid` | 422 | |
| `WritesDisabled` | `writes_disabled` | 403 | |
| `GraderUnavailable` | `grader_unavailable` | 503 | nothing recorded; retry the same item |
| `DiagnosticsDisabled` | `diagnostics_disabled` | 404 | |
| `ConfigConflict` | `config_conflict` | 500 | a second module disagreed about measurement policy |
| `RoomUnknown` | `room_unknown` | 404 | `cat_engine.live` |
| `LiveUnavailable` | `live_unavailable` | 503 | `cat_engine.live` |

**No error means "the candidate answered incorrectly."** Being wrong is a measurement, not a
failure, and the only thing a candidate learns from a response is whether it was *recorded*.

---

## 14. What this reference does not make true

Every signature above is implemented, tested and stable. That is a statement about the
software, and it is not a statement about the instrument.

- `decision_status` is `provisional` for every measured competency and stays that way until
  external exact-band calibration passes.
- Exact-band accuracy is about 65%; within-one-level about 99%. Report the range.
- Fairness has never been measured for any configuration — no DIF study, no subgroup
  analysis. `cat_engine/docs/evidence.md` calls this the largest untouched exposure.
- Every accuracy figure in this repo comes from simulation against a known true θ. No real
  candidate has been scored and checked against anything.

See `cat_engine/docs/evidence.md` for what has been measured and how.
