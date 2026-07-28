# Standalone Adaptive Voice Assessment — implementation plan

## Context

`voice_open_ended_cat_approach.md` specifies a standalone adaptive voice assessment that
extends the evidence-anchored pattern already shipped for MCQ and code items: an LLM
conducts the interview, a separate grader interprets the answer, and deterministic code
owns every psychometric consequence. Nothing of it exists yet. The committed
`README.md` on `cat-engine-combined-streamlit` says *"Open-ended items are designed for and
not yet built"*, `GraderAgent.grade` raises `NotImplementedError` for `open`, and
`calibration.open_cat_parameters` raises with the message *"open-ended calibration is
undefined until the open-ended grading design exists"*. This plan is that design.

It ships on a **new orphan branch containing only voice-assessment code**, so the voice
modality can be validated end-to-end without destabilising the working MCQ+code engine.
Stage B — folding it into the unified CAT engine — is planned for as a *merge*, not a
rewrite, which is why the substrate is vendored byte-identically rather than reimplemented.

Four decisions were taken with the user before planning:

| Decision | Choice |
|---|---|
| Measurement substrate | Copy the proven modules **verbatim** from `cat-engine-combined-streamlit` |
| Candidate client | FastAPI WebSocket + minimal HTML/JS `AudioWorklet` mic page |
| Live session shape | **One** Gemini Live session per assessment, compressed + resumable |
| Rubric → theta | Reuse the fractional-exponent update; leave a GPCM seam |

Verified before planning: `gemini-3.1-flash-live-preview` is real — native audio-to-audio,
131,072 input tokens, **audio-only sessions capped at 15 minutes**, sequential-only function
calling. Sources: [model card](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview),
[Live API capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities).

---

## Branch

```bash
git checkout --orphan voice-cat-standalone
git rm -rf --cached .
```

The branch carries `voice_assessment/`, `Docs/voice_open_ended_cat_approach.md` (the source
spec, committed so the branch is self-describing), `README.md`, `.gitignore`,
`.env.example`. Nothing from `backend/`, `streamlit/`, `project/`, or the two prototype dirs.

---

## The two architectural decisions everything else follows from

**1. Evaluation is async and happens *before* grading, which stays sync.**
`Orchestrator.record_response` is sync; `chat_json` is async. Calling the grader LLM inside
`grade()` would force a change to `orchestrator.py` and kill the merge-not-rewrite premise.

```
transport            → VoiceResponsePackage (frozen)
await VoiceResponseEvaluator.evaluate(item, pkg) → VoiceEvaluation   [async: chat_json, validate, one retry]
                       GradedVoiceResponse(package, evaluation, rubric)
orchestrator.record_response(state, item, graded)                    [sync, VENDORED, untouched]
    └─ GraderAgent.grade → _grade_open → grade_voice(...)            [sync, pure, no I/O]
```

`record_response(self, state, item, response: object)` already takes `object`. So
`orchestrator.py` stays byte-identical and `grade_voice` is a pure function, unit-testable
with zero monkeypatching.

**2. Manual activity control, not server VAD — because gating and server VAD are mutually
exclusive.** `silence_duration_ms` is measured over audio the *server receives*. If we drop
silence to save tokens, the server never observes the silence it needs to end a turn. The
user's "cut off on no-audio, decrease token usage" requirement therefore forces
`automatic_activity_detection.disabled = True`, with our own gate driving `activity_start` /
`activity_end`. A `gating.mode` config flag flips the whole system back to `server_vad` in
one line for A/B comparison.

Corollary: **the interviewer gets zero tools.** Function calling on this model is
sequential-only and would block the audio pipeline for a full round trip. All control flows
out-of-band as nonce-sealed text "director" turns.

---

## Layout

Single package root `voice_assessment/app/`. That is deliberate: vendored files keep their
own `from app.services...` import lines and are byte-identical *including imports*, which is
what `scripts/check_vendor_drift.py` asserts.

`BANK_PATH` in the vendored `bank.py` resolves to `voice_assessment/app/data/question_bank.json`
with no edit.

### Vendored verbatim — 14 files, zero changes

`app/config/settings.py` · `app/schemas/orchestration.py` · `app/services/observability.py` ·
`app/services/adaptive/{irt,convergence,llm}.py` ·
`app/services/orchestrator/{outcome,queue,variables,picker,competency,bank,orchestrator}.py`

`picker.py` needs **no** change — verified: `information_for` reads `item.cat.{a,b,c}` and
`item.loading(variable)`, both populated for a voice item; `QueuedCandidate.modality` accepts
`"open"` because the `Literal` already contains it.

### Vendored + one named edit each — 3 files

| File | Edit | Conflict surface |
|---|---|---|
| `orchestrator/calibration.py` | `OPEN_RUBRIC_FLOOR` + `open_cat_parameters` body replacing the `raise` | one function |
| `orchestrator/prompts.py` | one paragraph appended to `PICKING_SYSTEM` describing an `open` candidate | one string literal |
| `orchestrator/grader.py` | `CodeAdaptiveSession` import under `if TYPE_CHECKING:`; `open` dispatch + `_grade_open` | two hunks |

### New — assessment half (`app/services/voice/`)

`adapter.py` (`VoiceQuestionAdapter`, `measures_from_rubric`) · `rubrics.py` ·
`prompts.py` · `evaluator.py` · `validation.py` · `quotes.py` · `evidence.py` ·
`measurement.py` · `grader.py` · `shortlist.py` · `budget.py` · `presentation.py` ·
`session.py` · `report.py`
Plus `app/schemas/voice.py`, `app/config/voice_settings.py`, `app/data/voice_rubrics/*.json`.

### New — transport half (`app/voice_live/`)

`audio/{constants,frames,gate,recorder}.py` · `live/{transport,gemini_transport,scripted_transport,session_config,resumption}.py` ·
`interview/{runner,turn_machine,silence,director,prompts,guards,transcript,quality,clock}.py` ·
`protocol/messages.py` · `telemetry/{usage,sink}.py`
Plus `app/api/{voice_ws,health}.py`, `app/main.py`, `static/{index.html,app.js,mic-worklet.js,player-worklet.js}`.

Two files carry the whole transport's testability: `live/transport.py` (normalises away
`google.genai.types`) and `interview/clock.py` (makes every timer virtual).

### Scripts and config

`scripts/check_vendor_drift.py` — CI-fails if any vendored path differs byte-for-byte from
`cat-engine-combined-streamlit`. A single accidental import reorder in `picker.py` converts
the Stage-B merge from trivial to manual, and nothing else catches it.
`scripts/lint_voice_bank.py` — bank/rubric invariants.
`requirements.txt` — `backend/requirements.txt` minus `e2b-code-interpreter`, plus
`fastapi`, `uvicorn[standard]`, `websockets>=13`, `google-genai` (**pinned exact**).
The quote matcher uses stdlib `difflib`, so no fuzzy-match dependency.

---

## Resolving `open_cat_parameters`

```python
# The rubric floor is not a guessing floor, and it is not zero.
# Under the fractional likelihood, `c` is the lowest normalised rubric score a candidate of
# arbitrarily low ability still obtains. That is measurably non-zero for two reasons that
# have nothing to do with guessing: level-1 descriptors reward attempting (1 of 4 = 0.25),
# and an LLM rubric grader has central-tendency bias. With c = 0, a 0.15 score at theta = -4
# looks like a surprise and pushes the posterior UP, away from an ability the candidate may
# genuinely have.
OPEN_RUBRIC_FLOOR = 0.15

def open_cat_parameters(difficulty, discrimination) -> dict[str, float]:
    return {"a": round(max(float(discrimination), 0.05), 4),
            "b": mastery_difficulty_to_theta(difficulty),
            "c": OPEN_RUBRIC_FLOOR}
```

`b` reuses the code path's mastery logit — same authoring provenance, same [0,1] scale, same
words in the author's head, and Stage B mixes them. One honest wrinkle to record in the
docstring: with a floor, `P(b) = (1+c)/2 = 0.575`, so `b` is where the expected normalised
score is 0.575, not 0.5 — a 0.21-theta shift at `a = 1.7`. Not corrected: it sits well under
the 0.65–0.80 SE any short test reaches, and shifting an uncalibrated authored number by a
derived amount adds arithmetic without adding truth.

### Information parity is the sharp edge — enforce `a ∈ [1.8, 2.3]`

Fisher information scales with `a²` and is *reduced* by `c`. Against the real MCQ bank
(median `a = 1.90, c = 0.12`, peak `I = 0.716`):

| voice `a` (c=0.15) | peak I | rel. MCQ | min loading to clear picker's 0.75 utility floor |
|---|---|---|---|
| 1.4 | 0.367 | 0.51 | 1.465 — **impossible** (`weight` is `le=1.0`) |
| 1.7 | 0.541 | 0.76 | 0.992 — only its primary competency, only just |
| **2.0** | **0.750** | **1.05** | 0.716 |

Below `a = 1.7` a voice item **can never win a mixed shortlist** — the LLM picker can choose
it and be silently overridden by the relative-utility floor with only an `INFO` log line. The
assessment would look mixed and be MCQ-only. Mitigations: `lint_voice_bank.py` enforces
`a ∈ [1.8, 2.3]`; `measures[].weight` is **max-normalised** so the primary competency reads
1.0; `test_voice_calibration.py` asserts `parity_report()` returns no `miscalibrated` entry.

That `a ≈ 2.0` claim is a modelling judgement, stated as one: a 4-minute answer scored across
4–6 criteria aggregates far more observation than one MCQ, so a dichotomous-equivalent `a`
equal to one MCQ's would *understate* it. GPCM is what would derive it properly.

---

## The evidence chain

```
VoiceResponsePackage (frozen)
  → build_payload            transcript only; no audio, no prosody, no candidate identity
  → chat_json(...)           json.dumps(payload), per the gateway-400 lesson in picker.py
  → validate()               every §12 check, per-code disposition
  → normalize_criterion_score(raw, maximum)            raw/maximum, no thresholds
  → criterion_to_competency_weights(rubric)            projection, rows sum to 1.0
  → prompt_dependency_multiplier(kind)                 1.0 / 0.9 / 0.75
  → evidence_strength(package, evaluation)
  → VoiceCompetencyEvidence[]
  → FractionalExponentAdapter.outcomes()               weight = strength × dependency × confidence
  → GradedOutcome(modality="open")
  → competency.rollup_outcomes(...)                    VENDORED
  → variables.apply_outcome(...)                       VENDORED
```

Per sub-competency *k* over validated criteria *j*, with `w_jk = criterion_weight_j × projection_jk`:

```
score_k      = Σ(s_j·w_jk) / Σw_jk
confidence_k = Σ(conf_j·w_jk) / Σw_jk
coverage_k   = Σw_jk / Σ(all rubric criteria of k)
strength_k   = evidence_strength(package, evaluation) × coverage_k
```

**Loading is not multiplied in again** — it is folded into the projection, exactly as
`grader.py::_grade_code` already notes for the code path.

**§16.2's ordinal thresholds are a *reporting* transform, not a measurement one.**
`outcome.py`'s docstring is explicit that the fractional exponent exists so a 0.62 is
"0.62 of an observation rather than inventing thresholds nobody measured". Bucketing before
the posterior update would reintroduce them. `ordinal_level(score)` lives in
`measurement.py`, is used by `report.py`, and is documented as the input a future GPCM
adapter will consume.

### `evidence_strength` — the voice analogues, spelled out

| Code path | Voice analogue | Return |
|---|---|---|
| sandbox unavailable | Live session dropped, STT never returned | `0.0` |
| — | unscorable: silence, wrong language, refusal | `0.0` |
| — | `total_speech_seconds < 3.0` | `0.0` |
| did not compile | transcript unusable (confidence proxy < 0.55) | `0.25` |
| execution did not complete | cut off mid-sentence by `maximum_answer_seconds` | `0.4` |

Then multiplicative: `× 0.5` under 20 s of speech (below ~50 words a low score measures the
answer's length, not the competency); `× 0.5` on `explicit_decline`; `× 0.8` when
`evaluation_confidence < 0.5` (same rung, same number as the code path); `× 0.6` when
validation had to salvage the reply.

Worked: an ordinary good answer is `1.0 × 1.0 × 0.9 × 0.85 = 0.765`. A probe-dependent answer
with a dropped criterion is `1.0 × 0.6 × 0.8 × 0.75 × 0.7 = 0.252`.

Probe dependency is deliberately **not** in this function — §12 carries `prompt_dependency`
per criterion, so it applies during projection, not once per item.

---

## Grader validation and its degradation policy

`validate(reply, item, rubric, package) -> VoiceEvaluation`. Every §12 check with an explicit
disposition:

- **fatal** → `QUESTION_ID_MISMATCH`, `RUBRIC_ID_MISMATCH`, `RUBRIC_VERSION_MISMATCH`
- **drop criterion** → `UNKNOWN_CRITERION`, `UNKNOWN_COMPETENCY`, `CRITERION_COMPETENCY_MISMATCH`, `UNPARSEABLE_SCORE`, `MAXIMUM_SCORE_MISMATCH`, `SCORE_OUT_OF_RANGE`, `CONFIDENCE_OUT_OF_RANGE`, `NO_EVIDENCE_CITED`, `PROTECTED_ATTRIBUTE_LANGUAGE`
- **drop that evidence entry** → `UNKNOWN_TURN`, `INTERVIEWER_TURN_AS_EVIDENCE`, `QUOTE_NOT_IN_TURN`, `QUOTE_TOO_SHORT`
- **coerce in place, flag** → `UNKNOWN_PROMPT_DEPENDENCY` → `probe_supported`; `PROMPT_DEPENDENCY_CONTRADICTS_TRANSCRIPT` → `probe_supported`
- **record only** → `MISSING_REQUIRED_CRITERION` (reduces `coverage_k`); `DUPLICATE_CRITERION` (keep first)

`PROMPT_DEPENDENCY_CONTRADICTS_TRANSCRIPT` is the voice analogue of the code path's
`LLM_OBJECTIVE_CONFLICT`. Turn ordering is the only objective fact the transport hands us that
the model can contradict, so it gets the same treatment functional correctness gets:
**corrected in place and flagged, not dropped.**

**Three-tier degradation.** Code grading degrades to objective evidence; voice has none.

1. **Drop the failing criteria — and price the loss.** Code can drop naively because
   execution backstops the gap. Voice charges instead: a dropped criterion is rubric weight
   not observed, `coverage_k` falls, `evidence_strength` falls with it, the posterior moves
   less. Drop-and-price, not drop-and-ignore. That is what makes local dropping self-limiting.
2. **One semantic retry**, on any fatal code or `coverage < 0.5` or empty
   `criterion_evidence`. Errors fed back as `previous_attempt_errors`; same system prompt,
   same temperature. Implemented in `evaluator.py`, **not** `llm.py` — `chat_json`'s three
   attempts are for unusable *replies* and must stay byte-identical. Bounded at one.
3. **Unscorable.** Zero-weight `GradedOutcome` per measured variable,
   `competency_update_applied: false`, `retry_allowed: true`. Nothing special-cases theta:
   `apply_outcome` already skips zero-weight outcomes and does not increment `observations`.

Why identity mismatches are fatal and criterion errors are not: a wrong `rubric_version` means
the entire sheet was produced against a different instrument, and no descriptor that produced
any level is the right one. *Was the instrument right* vs *was one reading wrong* is the line.

---

## The evidence-quote matcher

The anchor rule requires the quote to appear in the cited turn, but the grader reads an STT
transcript and models paraphrase. Four tiers, two hard gates, one truthful escape hatch:

| Tier | Test | Accepted |
|---|---|---|
| `exact` | `quote in turn_text` | always |
| `normalized` | after NFKC, casefold, punctuation strip, disfluency drop (um/uh/[inaudible]) | always |
| `subsequence` | content tokens present **in order**, gaps ≤ 3 | always |
| `fuzzy` | `difflib.SequenceMatcher` matching-block coverage ≥ **0.82** | conditionally |

**Gate 1 — minimum 12 normalised characters, before any tier.** This, not the ratio, is what
stops the too-loose failure: `"the model"` matches almost any turn and anchors nothing, and no
ratio can detect that because a 9-character quote genuinely *is* present.
**Gate 2 — fuzzy needs ratio ≥ 0.82 *and* ≥ 24 characters.**

Realistic STT-paraphrase edits (dropping "um", `do not`→`don't`, fixing a plural) cost 2–6
characters and land at 0.94–0.98. A fabrication reusing the turn's topic vocabulary produces
scattered short blocks and lands under 0.6. The 0.60–0.82 band is genuinely ambiguous; the
line goes at its top because **a rejected entry costs coverage, which is priced; an accepted
fabrication costs a wrong number on a real candidate's record, which is not.**

**The escape hatch is load-bearing.** The prompt tells the model: if you cannot quote
verbatim, set `"quote": null` and describe it — accepted, tier `described`, still requires a
valid candidate `turn_id`, multiplies that criterion's projection weight by **0.7**. Without
it a strict matcher gives a paraphrasing model no truthful option and it will invent quotes.

Safeguards: a criterion is dropped only for **zero** surviving evidence entries; every accepted
quote records tier and ratio in `quote_report`; the floor is env-tunable. First-cohort target
**< 10%** rejection — **> 30%** means the number is wrong, not the models.

---

## Transport: gating, turns, and the one long session

### The gate — the whole token saving

Client `getUserMedia({channelCount:1, echoCancellation:true, noiseSuppression:true,
autoGainControl:false})`. **AGC off deliberately** — it normalises room tone up to speech
level and destroys the SNR proxy. 20 ms frames = 320 samples @ 16 kHz = 640 bytes;
48k→16k via a decimating FIR (naive decimation aliases and inflates the band the gate reads).

Calibrate over ~1.8 s of silence at session start: `noise_floor_db = median(dBFS)` (median so
one cough does not poison it), `T_open = max(p95 + 8.0, -50.0)`, `T_close = T_open - 4.0`
(Schmitt trigger, prevents flapping inside one utterance). The `-50 dBFS` clamp exists because
a dead mic calibrates to `-90` and would make every frame speech.

```
CLOSED --[3 consecutive frames > T_open]--> OPEN   (flush 300 ms pre-roll ring first)
OPEN   --[< T_close]--> TAIL (still forwarding 200 ms) --> CLOSED
CLOSED --[speech again before TURN_END_SILENCE_MS]--> OPEN   (same turn, no activity_end)
```

3-frame consistency rejects door slams and key clacks. The **300 ms pre-roll** matters: the
docs' example `prefix_padding_ms: 20` clips the leading plosive off "Because…". The **200 ms
tail** costs ~5 tokens per turn and measurably improves transcription.

**Sub-threshold frames are dropped entirely — no silence, no zeros, no keepalive.** During
`CLOSED` the WebSocket carries no binary frames at all. Anything else defeats the purpose.
During interviewer playback raise the bar to `T_open + 6 dB` and 5 frames — barge-in must be
deliberate.

**Client gate is an optimisation; the server gate is authoritative.** Same algorithm, real
thresholds, in Python — so every tested decision is reachable by CI with no browser and no
microphone. `test_gate_parity.py` asserts a one-sided property (JS may forward frames the
server drops, never the reverse), which is far more maintainable than bit-exact parity.

### The cut-off ladder

| Timer | Default | Behaviour |
|---|---|---|
| `TAIL_PAD_MS` | 200 | stop forwarding; turn stays open |
| `TURN_END_SILENCE_MS` | **1800** | `activity_end` → candidate turn ends |
| `NUDGE_SILENCE_MS` | 8000 | director `nudge`: "Take your time — let me know when you're ready." |
| `REPEAT_SILENCE_MS` | 20000 | interviewer re-asks once; does **not** consume a probe |
| `ABANDON_SILENCE_MS` | 45000 | close item, `unscorable / no_response` |

`TURN_END_SILENCE_MS = 1800` is the load-bearing number and is deliberately long. Getting it
wrong short is the worst failure mode: the interviewer interrupts a thinking candidate, which
is both unnatural and expensive (the model then generates a turn). Per-item overridable.

`maximum_answer_seconds` counts **cumulative gated speech across all turns of the item**, not
wall clock — wall clock punishes candidates who pause to think, and speech seconds are also
what the token bill is proportional to.

### Live session config

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    system_instruction=INTERVIEWER_SYSTEM_INSTRUCTION.format(nonce=session.nonce),
    speech_config=...,                                     # prebuilt voice
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=True,                 # PRIMARY MODE; fields below apply only to server_vad
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=300,         # NOT 20 — 20 ms clips the word onset
            silence_duration_ms=800,       # NOT 100 — 100 ms chops a thinking candidate
        ),
        turn_coverage=types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,   # override 3.1's default
    ),
    context_window_compression=types.ContextWindowCompressionConfig(
        trigger_tokens=96_000, sliding_window=types.SlidingWindow(target_tokens=48_000)),
    session_resumption=types.SessionResumptionConfig(handle=resume_handle),
)
```

Send with `mime_type="audio/pcm;rate=16000"` — the prior art in `project/` omits the rate and
makes the server guess. Also note `project/backend/app/services/gemini_live.py:18` passes
`settings.litellm_api_key` as the Google key; the new `config.py` reads `GEMINI_API_KEY` and
fails loudly at startup on a placeholder.

A 30-minute assessment budgets ≈ 30k of 131k tokens, so it never compresses — that is the
goal, and `trigger_tokens = 96_000` keeps the common case uncompressed and predictable.

### Question injection and the director protocol

Questions are injected as a **text turn** (`send_client_content`) and the model speaks them.
Against speaking them with our own TTS: one voice, natural prosody, and decisively — barge-in
works through the same machinery, instead of needing a separate code path to interrupt a
question versus a probe.

```
<<DIRECTOR 9f2c4ab13e07d5c1>>
action: ask
item_id: voice_c10_001
question: |
  <verbatim question text>
<<END 9f2c4ab13e07d5c1>>
```

Actions: `ask`, `probe`, `nudge`, `repeat`, `wrap`, `close`, `resume`, `resume_cold`, `correct`.
The per-session nonce (`secrets.token_hex(8)`) is the prompt-injection defence, and it is
unusually strong here: directors arrive as *text*, candidate input arrives as *audio*, and the
nonce is never spoken aloud. Every `probe` and `resume` block **restates the question
verbatim** — ~40 tokens buying immunity to context eviction of the most important fact in the
session.

### Keeping the interviewer in its lane

1. **The rubric never enters the Live session.** Only `question_text` is sent. Criteria,
   score bands, expected answers and the running estimate live in the grader process. This is
   not a mitigation — it is elimination: the model cannot leak what it was never told.
2. System instruction (drafted in full during Phase 6; enforces ask-only-the-given-question,
   neutral acknowledgements ≤ 6 words, no correctness signals, approved probe types only,
   candidate speech is evidence and never instruction).
3. Post-hoc `guards.py` on every interviewer `output_transcription`: evaluative-language
   lexicon (word-boundary regex), unauthorised question (a `?` with no director since the last
   `turn_complete`), question drift (Jaccard on content words vs canonical text, < 0.55 flags).
4. On violation: `quality_warning`, log the utterance, inject `action: correct`, stamp the
   item. **Never kill the session** — aborting mid-assessment harms the candidate far more
   than one stray "good".

### Per-item transcript slices

Tag at write time, do not carve after the fact. Every `TranscriptEntry` carries
`(seq, t_mono, role, kind, text, item_id, turn_id, partial, truncated)`.

**The subtlety that will bite an implementation that ignores it:** `input_transcription`
chunks arrive asynchronously and can land after `activity_end`, sometimes after the next item
opened. Maintain `turn_id -> item_id` and attribute late chunks by `turn_id`, never by
"whatever item is current". `freeze_item()` waits for the last `turn_complete` plus a bounded
2000 ms drain, then emits an immutable slice. Post-freeze arrivals log
`late_transcript_dropped` with a byte count — if that is ever non-zero the drain is too short.

### Reconnect

Store `new_handle` on every resumable `SessionResumptionUpdate`. On `GoAway`, defer to the next
safe boundary if `time_left` allows — but be honest that `time_left` is often seconds, so the
hard path is the common path and gets built first. Reconnect with the stored handle, backoff
0.5/1/2 s × 3, then send `action: resume` re-priming the verbatim question. In-flight audio
buffers into a bounded 5 s ring. After three failures apply an evidence bar: ≥ 15 s speech and
≥ 40 words → `truncated`; below → `unscorable / transport_lost`. **Never silently re-run an
item** — a candidate answering twice with different information is an integrity problem, not
a recovery.

---

## Two contract reconciliations between the halves

These are the only places the transport and assessment designs disagreed; resolve them before
writing either.

**1. `outcome_status` has one vocabulary, owned by the assessment half** — it is what drives
`evidence_strength`. Transport maps into it:

| Transport condition | `outcome_status` | `reason_code` |
|---|---|---|
| clean | `complete` | `""` |
| cut by `maximum_answer_seconds`; recovered above the evidence bar; SNR 10–15 dB; drift < 0.55 | `truncated` | `max_time` / `transport_lost` / `low_snr` / `question_drift` |
| silence, wrong language, refusal, < 5 s speech, < 12 words, SNR < 10 dB, dropout > 10% | `unscorable` | `no_response` / `wrong_language` / … |
| our outage | `infrastructure_error` | `live_session_dropped` / `stt_unavailable` |

**2. `transcript_confidence` is a computed proxy and must be named as one in the code.** The
Live API's `input_transcription` exposes text only — no per-token confidence, no alternatives,
no word timings. `VoiceTurn.transcript_confidence` and
`VoiceResponsePackage.mean_transcript_confidence` are therefore derived by
`interview/quality.py` from four proxies, cheapest first:

1. **Words per second** — genuine speech runs 2.0–3.5 wps; `< 0.8` sustained over ≥ 10 s means
   the recogniser produced very little from a lot of audio. The single most useful proxy.
2. **Degenerate-token ratio** — single chars, immediate repeats, bracketed artefacts; > 25% suspect.
3. **Language match** — > 40% of tokens off the expected language → `wrong_language`.
4. **Round-trip agreement** (paid, conditional) — re-transcribe the retained item WAV with a
   batch model, content-word overlap < 0.6 → unscorable. **Run only on items already flagged
   by 1–3**, bounding the cost to the bad tail.

`ItemTranscript` therefore carries **both** `live_text` (drives real-time probe decisions) and
`final_text` (initially `None`, filled by batch re-transcription). The grader quotes
`final_text or live_text`, so upgrading transcription quality later is a config change.

---

## Question bank

**Reuse `modality: "open"`; do not add a `"voice"` literal.** Adding one edits
`schemas/orchestration.py` — the first file the vendoring decision names as byte-identical —
and `Modality` is referenced by `BankItem`, `QueuedCandidate` and `GradedResponse`, so **every
`AssessmentState` this branch persists would fail `model_validate` on the main branch** until
the Literal merges. A cross-branch serialisation incompatibility costs far more than a report
label. The discriminator `open.question_type == "voice_open_ended"` carries the distinction
where it belongs. Cost accepted: `modalities_used` reads `"open"`, and typed-open would pool
with voice in `information_parity()` once it exists.

`app/data/question_bank.json` in the shape `JsonUnifiedBank._load()` already handles;
`app/data/voice_rubrics/{rubric_id}.json` one file per rubric — separate, not inlined, because
one rubric serves an item family and inlining makes a rubric edit an N-item edit with no way
to check the copies agree.

Item payload: `question_type, title, prompt, difficulty, discrimination,
estimated_time_seconds, maximum_answer_seconds, maximum_probes, allowed_probe_types,
rubric_id, prerequisites, requires_supports, topic_tags`. `measures` are **derived** from the
rubric by `measures_from_rubric` and max-normalised; `cat.b` is recomputed by the lint rather
than trusted. Rubric criteria carry `criterion_id, competency_id, weight, required,
maximum_score, score_levels {0..4}`, and communication criteria carry a `fairness_note`
rendered into the prompt.

### Size — derived from the convergence rule, not guessed

Simulating `graded_posterior_update` at `a=2.0, c=0.15` against a tentative prior:

| SE target | w = 0.60 (realistic voice weight) |
|---|---|
| 0.65 (shipped default) | 9 items ≈ 40 min per main competency |
| **0.80** | **7 items ≈ 32 min** |

**Minimum 8 items per main competency**, comfortable 12 (3 difficulty bands × 4, so `b` can
track `theta` instead of the picker always reaching for the same tier). **First bank: 2 main
competencies × 12 items = 24 items, 6–10 rubrics.** Ten main competencies at 4.5 min/item is
a three-hour session and is not a thing that exists.

---

## Loop and policy changes

**Time budget belongs in the session runner, not the bank.** The obvious fix — a bank decorator
filtering `shortlist()` by remaining time — is wrong: `record_response` calls
`len(self._bank.shortlist(...))` and feeds it to `evaluate_finalisation`, which finalises with
`stop_reason="bank_exhausted"` at zero. A time filter visible there makes a variable report as
bank-exhausted when it merely has no *currently affordable* item — a false claim, produced by a
time policy, very hard to trace.

So `budget.py` provides `estimated_cost_seconds(item)` (`estimated_time_seconds + probes×30 +
20 s overhead`) and `affordable(item, remaining)` at 0.9 headroom, and `VoiceAssessmentRunner`
checks affordability **after `ensure_presenting`, before administering**, stopping with
`stop_reason="time_limit"`. This also fixes a misreport the vendored loop would otherwise
produce: budget exhaustion currently surfaces as `"no_candidates_available"`, claiming the bank
ran out when the clock did.

**§17.1's four permanent exclusions go in `VoiceShortlistPolicy`** (satisfies
`UnifiedBankRepository`, so the vendored orchestrator takes it directly): prerequisites,
bank-level exposure cap, similarity-to-recent (shared `sub_competency` + `topic_tag` with the
last 2 items — deterministic tags, no embeddings, because a tag comparison is auditable and a
similarity model is not), and language/accessibility supports. These *should* be visible to
`items_remaining` — a variable whose remaining items all fail prerequisites genuinely is
exhausted. Each filter has an "unless the pool would otherwise be empty" release, applied
similarity → exposure → supports; prerequisites never release.

**`get()` must be an unfiltered passthrough.** `next_item()` calls it on an already-queued id;
if the policy hid it the orchestrator would log "queued item vanished from the bank" and return
`None`, killing the step.

---

## Verification

Run from `voice_assessment/`: `pip install -r requirements.txt && pytest`.
`pytest.ini` mirrors `backend/pytest.ini` (`asyncio_mode = auto`, placeholder env). House
style: `monkeypatch` only at real boundaries, no network, no credentials.

**Vendored, preserved:** `tests/test_orchestration.py` — `TestFractionalUpdate` byte-for-byte
including the `np.array_equal` float-equality reduction to `irt.posterior_update`.
Non-negotiable. `TestCalibration::test_open_ended_calibration_is_refused_not_guessed`
**inverts** to `test_open_ended_items_carry_a_rubric_floor`.

**Assessment half:**
- `test_voice_calibration.py` — `open_cat_parameters(0.55, 2.0) == {"a":2.0,"b":0.2007,"c":0.15}` exactly; `b` agrees with `code_cat_parameters`; `parity_report()` has no `miscalibrated` entry; `min(item.cat.a) >= 1.8`.
- `test_voice_validation.py` — table-driven, one parametrized case per §12 code, asserting the flag *and* its documented disposition.
- `test_quote_matcher.py` — one per tier; `"the model"` rejected though literally present; an STT paraphrase accepted > 0.9; a topic-word fabrication rejected < 0.6; `null` quote accepted as `described`; an interviewer turn quoted verbatim rejected as `INTERVIEWER_TURN_AS_EVIDENCE`, not accepted as a match.
- `test_voice_evidence.py` — every rung's exact number (0.0/0.25/0.4/0.5/0.8/0.6); `1.0 × 1.0 × 0.9 × 0.85 == approx(0.765)`; loading not applied twice.
- `test_voice_grader.py` — tier 1 leaves three criteria and sets `degraded`; tier 2 makes **exactly two** calls with `previous_attempt_errors` in the second; tier 3 yields all-zero weights; `LLMUnavailable` never raises; an unscorable package short-circuits with `calls == []`.
- `test_unscorable.py` — the property at its enforcement point: `theta_hat`, `standard_error`, `observations` and `served_item_ids` all unchanged.
- `test_shortlist_policy.py` — each filter; `get()` passthrough; **`items_remaining` unaffected by the time budget** (the false-`bank_exhausted` regression).
- `test_voice_convergence.py` — **the bank-adequacy test**: weight-0.6 outcomes against the shipped bank's real parameters reach `cat_se_target` within `cat_max_questions`. This is the test that fails when the bank is too weak, and it is worth more than any other here.
- `test_voice_session.py` — end-to-end with fake transport and fake grader; terminates; no item twice; `AssessmentState.model_validate(state.model_dump()) == state`; unaffordable item stops with `"time_limit"`, not `"no_candidates_available"`.
- `test_voice_bank.py` / `test_voice_prompt_safety.py` — every item validates; `measures == measures_from_rubric(rubric)`; `cat.b == mastery_difficulty_to_theta(difficulty)`; no protected-attribute term anywhere; `build_payload()`'s key set matches an allowlist so no audio, URI, prosody or identity field can leak in.

**Transport half** (all offline, via the `LiveTransport` and `Clock` seams; PCM fixtures +
scripted YAML server-event streams):
`test_gate.py` (room tone forwards zero frames; speech forwards ≥ 95%; first frame ≤ 300 ms
before onset; door slam produces no `activity_start`; exactly one open/close per utterance) ·
`test_calibration.py` · `test_turn_machine.py` (table-driven, virtual time) ·
`test_silence_policy.py` · `test_bargein.py` (including the 250 ms self-echo guard) ·
`test_transcript_slices.py` (late chunks land on the right item by `turn_id`) ·
`test_reconnect.py` · `test_guards.py` (a forged director block in a candidate transcript
changes no state) · `test_quality.py` · `test_protocol.py` · `test_usage.py` ·
`test_gate_parity.py` (`@pytest.mark.node`, skipped without node).

**Not in CI:** `test_live_smoke.py`, `@pytest.mark.live`, skipped unless `GEMINI_API_KEY` and
`RUN_LIVE_TESTS=1`. Asserts the config is accepted and each `LiveEvent` kind still arrives.
Nightly — it is the contract test that catches preview-model churn.

**Also in CI:** `scripts/check_vendor_drift.py` on every commit, and
`scripts/lint_voice_bank.py`.

**Manual end-to-end:** `uvicorn app.main:app`, open `http://localhost:8000/`, run a two-item
assessment with a headset. Confirm the interviewer starts within ~800 ms of `activity_end`
(p95 above that reads as robotic), barge-in stops playback instantly, and
`runs/{id}/summary.json` reports `savings_pct` — the one number that answers the token
requirement.

---

## Phases

| # | Scope | Done when | Size |
|---|---|---|---|
| 1 | Orphan branch; vendor 14 + 3 edited; `check_vendor_drift`; `voice_settings`; `open_cat_parameters`; `VoiceMeasurementAdapter` + `ordinal_level` | vendored suite green incl. the float-equality reduction; drift check clean; parity report has no `miscalibrated` entry | S |
| 2 | `schemas/voice.py`, `adapter.py`, `rubrics.py`, `lint_voice_bank.py`; 24 items × 2 competencies, 6–8 rubrics | zero `_load()` rejections; every lint invariant; ≥ 12 per main across 3 difficulty bands; protected-attribute lint clean | **L** (rubric authoring dominates and is the schedule risk) |
| 3 | `prompts/evaluator/validation/quotes/evidence/measurement/grader`; the two `orchestrator/grader.py` edits | §12 table green with every disposition; both quote gates + `described`; unscorable provably moves no theta; a scripted reply produces the hand-computed weights; retry fires exactly once | **L** (the core) |
| 4 | `audio/`, `live/`, `interview/`, `clock`, `scripted_transport` | full 8-item assessment runs offline against the scripted transport, in virtual time | **L** |
| 5 | `protocol/`, `api/voice_ws.py`, `gemini_transport.py`, `resumption.py`, `static/` | real Live session completes 2 items with a headset; reconnect survives an induced `GoAway` | M |
| 6 | `shortlist.py`, `budget.py`, `session.py`, `report.py`, `guards.py`, `quality.py`, `telemetry/` | end-to-end session reports every variable; `time_limit` reported correctly; `savings_pct` emitted | M |
| 7 | Langfuse spans, quote-tier telemetry, calibration script re-estimating `OPEN_RUBRIC_FLOOR` and the quote floor from the first cohort; walk §28's 20 DoD criteria | all 20 check off against a named test or script output | M |

Build order within the transport phases matters: `gate.py` first (riskiest algorithm, pure, no
SDK, cheapest to validate), then the two seams, then the state machine, then the wire, then
the browser last — by which point the server gate, protocol and event stream are all fixed.

---

## Risks

**1. Weight compression is the one I would raise first in review.** `weight = strength ×
dependency × confidence` multiplies three sub-unit factors; a clean probe-supported answer at
grader confidence 0.85 yields 0.64. At w ≈ 0.6, the shipped `cat_se_target = 0.65` needs 9
items ≈ 40 min per competency against `cat_max_questions = 12`, so most variables finalise on
`question_budget` and report *not converged* — correct and useless.
*Mitigation:* set `CAT_SE_TARGET=0.80`, `CAT_STABILITY_SE_CEILING=0.95` in the branch env —
**values, not code**, and `convergence.py`'s docstring sanctions per-deployment retuning. That
gives 7 items ≈ 32 min. Scope the first bank to 2 competencies. `test_voice_convergence.py`'s
bank-adequacy test fails in CI rather than in a candidate's session. State in the report that
`certainty_pct` is calibrated to a different SE target than the MCQ engine's, or the two
branches' "90% confident" will silently mean different things.

**2. Information parity — voice items losing every ranking, invisibly.** Quantified above.
*Mitigation:* lint-enforced `a ∈ [1.8, 2.3]`, max-normalised loadings, a parity assertion in
CI, and the `a ≈ 2.0` claim stated as a modelling judgement with its reason in the docstring.

**3. Live input transcription may not be good enough to quote as evidence.** The least
boundable risk: transcription is latency-optimised, there is no confidence signal, and a
grader quoting a misheard phrase back in a report is a credibility disaster.
*Mitigation, built in from day one rather than retrofitted:* retain per-item audio;
`live_text` / `final_text` separated; live text drives only flow control and probe decisions;
any item whose text will be *quoted* gets batch re-transcription first. Budget for that cost
from the start.

**4. Manual activity control is unverified in three ways** — whether `activity_start`
mid-generation interrupts as cleanly as server VAD; whether jump-cuts between gated segments
degrade recognition; whether the model's turn-taking instincts fight our explicit boundaries.
*Mitigation:* pre-roll and tail pad preserve onsets and decays; `gating.mode` flips to
`server_vad` in one config line; measure the WER cost against batch re-transcription on a
20-session pilot **before** real candidates. If the delta exceeds a few points, lengthen the
tail or fall back — do not defend the design against the measurement.

**5. `c = 0.15`, `b = logit(difficulty)`, `a ∈ [1.8, 2.3]`, and the 0.82 quote floor are
modelling decisions, not derivations.** The house rule is that `calibration.py` is *the only
provisional place*; keep that literally true by refusing to let any voice number leak into
`evidence.py` or `measurement.py`. `OPEN_RUBRIC_FLOOR` is one named constant, not authored per
item, so exactly one number needs re-estimating. Phase 7's calibration script re-estimates it
from the bottom ability decile and the quote floor from the tier histogram.

**Also tracked:** acoustic self-barge-in (AEC + 6 dB playback threshold + 250 ms suppression
window + a `self_bargein_suspected` metric, because this failure is invisible in aggregates);
interviewer verbosity inflating output-audio cost (guard on non-ask turns > 20 s); spoken
prompt injection (mitigated by the nonce, the untrusted-evidence framing, `guards.py`, and
above all by the interviewer having no tools and no authority over item flow — the worst a
successful injection achieves is misbehaviour in words).

---

## Stage B — the integration diff this plan is shaped to produce

**Files that gain a real branch (6):** `orchestrator/calibration.py` (one function),
`orchestrator/grader.py` (two hunks — the file the merge exists to change; **no signature
change and no new constructor parameter**, because the rubric rides on `GradedVoiceResponse`),
`orchestrator/prompts.py` (one string literal), `orchestrator/engine.py` (`Modality.OPEN`, one
cosmetic line), `tests/test_orchestration.py` and `tests/test_orchestration_flow.py` (the two
`..._is_refused_not_guessed` tests invert).

⚠️ `TestUnifiedBank::test_both_modalities_are_present` asserts `== {"mcq","code"}` and **will
fail the moment voice items enter the shared bank**. It must become `>= {"mcq","code"}`. That
is the one that will be missed.

**Files that gain nothing (14):** all vendored paths plus `backend/requirements.txt`. Three of
those are deliberate: `requirements.txt` because the quote matcher uses stdlib `difflib`;
`settings.py` because voice knobs live in `VoiceSettings`; `orchestration.py` because voice
reuses `"open"`.

**Pure additions:** `git mv voice_assessment/app/services/voice/ backend/app/services/voice/`,
plus the schemas, settings, rubrics, scripts and test files.

**The bank file — avoid the one avoidable conflict.** Appending voice items to the 550-item
`question_bank.json` conflicts whenever both branches append at the tail, and a JSON merge
conflict there is genuinely miserable. Ship `backend/app/data/voice_bank.json` and add a
`CompositeBank` over two `JsonUnifiedBank`s. Zero edit to `bank.py`, zero conflict, and
`coverage()` / `parity_report()` compose over the union.

Because every vendored file is byte-identical, a three-way merge sees no change on the voice
side and takes main's version with no conflict at all. That is the entire payoff — and it is
only true if `check_vendor_drift.py` runs on every commit.
