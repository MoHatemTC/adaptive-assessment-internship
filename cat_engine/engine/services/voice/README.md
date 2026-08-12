# `.../services/voice/`

Open and spoken answers: from a transcript to `GradedOutcome[]`.

`open` and `voice` grade identically — the modality records how the answer was collected,
not how it was judged.

## Files

| File | Responsibility |
|---|---|
| `evaluator.py` | Async rubric grading. **This runs before `grade_voice`, and the ordering is load-bearing**: evaluation is a model call and the grader is sync, so the await happens at the call site. |
| `grader.py` | The sync pure grader: package + evaluation → `GradedOutcome` list. No I/O, so it is exactly reproducible. |
| `evidence.py` | Evidence strength and competency projection — how much a spoken answer is allowed to move an estimate. |
| `rubrics.py` | Projecting a rubric onto competencies. A rubric scores CRITERIA; a posterior is over COMPETENCIES, and these two functions are the map. |
| `quotes.py` | Evidence-quote matcher — four tiers, two hard gates, one escape hatch. A claim the model cannot ground in the transcript does not count. |
| `validation.py` | Validate a grader reply against the allowed dispositions. A malformed reply contributes nothing rather than contributing noise. |
| `language.py` | Lightweight language checks for spoken answers (English-only policy). |
| `prompts.py` | Prompts for the rubric grader — LiteLLM, never Gemini Live. |

## The rubric travels with the item

Every bank carries `rubric_criteria` inside the payload of each open or voice item, so a
rubric travels with the question it grades and a bank is one self-contained file.

There used to be a second route — an item could name a `rubric_id` and a loader read
`data/voice_rubrics/<id>.json`. It is gone. No registered bank ever used it, and an uploaded
bank could never have used it either, because the write path stores a bank file and has
nowhere to put a side-car rubric.
