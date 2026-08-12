# `.../services/voice/`

Open and spoken answers: from a transcript to `GradedOutcome[]`.

`open` and `voice` grade identically — the modality records how the answer was collected,
not how it was judged. A typed answer and a spoken one reach the same rubric.

## The chain, and why it is split where it is

```
transcript ─► evaluator ─► validation ─► quotes ─► evidence ─► grader ─► GradedOutcome[]
              (async,       (what the     (is that   (how much   (sync,
               a model      model may     claim in   is it       pure)
               call)        send back)    the text?) worth?)
```

**The async/sync seam is at `evaluator` → `grader`, and it is load-bearing.** Rubric
evaluation is a model call; `Orchestrator.record_response` is synchronous. So the evaluation
happens first, at the call site, and the grader receives an already-evaluated
`GradedVoiceResponse`. Handing it a raw package raises `TypeError` rather than silently
grading nothing — which is the one ordering the service-to-module collapse had to get right.

**The grader is pure.** No I/O, so the same package and evaluation always produce the same
outcomes, and a session can be replayed from a dump exactly.

## Files

| File | Responsibility |
|---|---|
| `evaluator.py` | Async rubric grading. Builds the rubric from the item payload, calls the model, and hands the reply to validation. |
| `validation.py` | What the model may send back. Fatal rejections (wrong item, rubric or version) discard everything; degraded ones drop a criterion and keep the rest. Every rejection raises a flag, because a silent drop and a recorded drop look identical in the score. |
| `quotes.py` | Four tiers, two hard gates. Is the quote the model cited actually in the transcript? Too strict throws away correct gradings over a comma; too loose lets a model invent evidence. |
| `evidence.py` | Evidence strength and the projection onto competencies — how much a spoken answer is allowed to move an estimate, given its quote tier, prompt dependency and transcript confidence. |
| `rubrics.py` | A rubric scores CRITERIA; a posterior is over COMPETENCIES. These two functions are the map, and `normalize_criterion_score` clamps — a model asked for a score out of 8 will occasionally return 9. |
| `grader.py` | The sync pure grader: package + evaluation → `GradedOutcome` list. |
| `language.py` | Lightweight English-only checks for spoken answers. |
| `prompts.py` | Prompts for the rubric grader — LiteLLM, never the realtime model. |
| `__init__.py` | Package marker. |

## The rubric travels with the item

Every bank carries `rubric_criteria` inside the payload of each open or voice item, so a
rubric travels with the question it grades and a bank is one self-contained file.

There was a second route once — an item could name a `rubric_id` and a loader read
`data/voice_rubrics/<id>.json`. It is gone. No registered bank used it, and an uploaded bank
could never have used it either, because the write path stores a bank file and has nowhere
to put a side-car rubric.

## An unscorable answer moves nothing

`outcome_status` of `unscorable` or `infrastructure_error` produces outcomes of **weight 0**,
which make the likelihood identically 1. Lost audio is not evidence about a candidate, and
the candidate is told so — `PACKAGE_INFRASTRUCTURE_ERROR` is one of the two flags that
crosses the candidate boundary mid-session.
