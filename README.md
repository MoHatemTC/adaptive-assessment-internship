# Adaptive Competency Assessment — CAT engine

Computerised adaptive testing for multiple-choice competency assessment. A 3PL item
response theory engine owns the measurement; an LLM chooses each question from a shortlist
the engine has already ranked.

Every candidate answer narrows an explicit belief about their ability, and the next
question is the one that will narrow it most. A test therefore ends when the estimate is
precise enough rather than after a fixed number of questions — typically 9–11 instead of
a fixed 20–30, at a defined precision.

```
backend/
  app/
    config/settings.py            configuration, incl. every CAT policy knob
    schemas/adaptive.py           typed boundary: Item, AbilityState, CompetencyResult
    services/adaptive/
      irt.py                      3PL model, EAP estimation, Fisher & KL information
      convergence.py              stopping rules, certainty
      bank.py                     ItemRepository seam + JSON implementation
      selection.py                ranking, content balancing, the LLM chooser
      llm.py                      async LiteLLM client with reply validation
      rephrase.py                 guard for model-rewritten stems
      prompts.py                  system prompts
      session.py                  AdaptiveSession — the public entry point
    data/item_bank.json           calibrated item bank
  tests/                          IRT invariants, selection fidelity, ability recovery
```

## Who decides what

This split is the design, and it was chosen by measuring the alternatives rather than by
preference.

| Decision | Owner | Why |
|---|---|---|
| Grading | code | Exact index comparison. There is a correct answer; nothing is gained by asking a model. |
| Ability estimate | code | Bayes on a fixed grid. Deterministic and auditable. |
| Stopping | code | A psychometric threshold, not a judgement call. |
| Which items are viable, and their rank | code | The whole CAT criterion: KL early, expected Fisher later. |
| **Which viable item to ask** | **LLM** | Chosen from a code-computed shortlist, with the engine's own choice recorded beside it. |
| Stem wording | LLM, then a code guard | Opt-in; off by default (see *Rephrasing*). |

**The model cannot damage the measurement.** Every item it can pick is one the engine
already ranked as near-optimal, so the worst case is bounded by the shortlist rather than
by the model's behaviour. Any failure — an invented id, a malformed reply, an unreachable
gateway — falls back to the engine's choice, because there is always a correct next
question and an infrastructure problem must not end a candidate's assessment.

Measured against a live reasoning model over 40 simulated candidates, the model
reproduced the engine's own choice on **353 of 353 steps**, giving up no information. That
is the guarantee this structure is built to hold whether or not the model cooperates.

## Usage

```python
from app.services.adaptive import AdaptiveSession, JsonItemRepository

session = AdaptiveSession(JsonItemRepository())
state = await session.begin("Python & Software Engineering", self_rating=3)

while True:
    selected = await session.next_item(state)
    if selected is None:            # bank exhausted
        break

    # present selected.presented_stem and selected.item.options to the candidate
    state, stop = await session.record_answer(state, selected.item, chosen_index)
    if stop.should_stop:
        break

result = await session.summarise(state, stop)
```

`AbilityState` is a plain pydantic model and round-trips through JSON, so a session can be
persisted between HTTP requests and resumed on any worker. `AdaptiveSession` holds no
per-candidate state.

`CompetencyResult` separates two things a report must not conflate:

- `converged` — a measurement rule ended the test.
- `precision_target_met` — the standard error actually reached the target.

A test can end on a settled band without reaching the target. Reporting the first without
the second overstates what was measured.

## Integrating with a live question bank

`ItemRepository` is the seam. Implement two methods against Supabase, Qdrant or anything
else, and no selection code changes:

```python
class SupabaseItemRepository:
    async def items_for_competency(self, competency: str) -> list[Item]: ...
    async def competencies(self) -> list[str]: ...
```

`bank.coerce_item` builds an `Item` from a bank record, filling `a`/`b`/`c` from
difficulty and discrimination labels when the record carries no calibrated numbers.
Numeric parameters always win — overwriting a real calibration with a label midpoint would
discard the only thing that makes the scores comparable.

## Configuration

All settings live in `app/config/settings.py` with the reasoning for each default; see
`backend/.env.example`. The LLM path uses the LiteLLM proxy, matching the parent project.

Two worth knowing before deploying:

- **`LITELLM_TIMEOUT_SECONDS` defaults to 180.** A reasoning model takes ~20s for one
  selection call. A timeout tuned for a non-reasoning model reports slow calls as failures,
  and the assessment silently drops onto the deterministic path.
- **`CAT_EXPOSURE_TOP_K` defaults to 3.** Pure argmax selection is deterministic, so every
  candidate at a given ability receives an identical form and the bank leaks after one
  cohort. Set it to 1 only for reproducible runs and tests.

## Bank requirements

**A bank must be validated against the precision target before it is trusted.** Precision
comes from the *sum* of information over the items actually administered, and each item is
used once — so a pool holding one sharp item at each ability passes any peak-information
check and still cannot finish a test.

```python
from app.services.adaptive import questions_needed

questions_needed(items, theta=0.0, prior_sd=1.7)   # None => target unreachable
```

This is the check that matters, and it is a property of the bank that bounds every
assessment run against it however good the selection is. The bundled bank does not clear
`CAT_SE_TARGET = 0.65` within 12 questions at every ability — sessions there end on the
stable-band rule, which is why the distinction between `converged` and
`precision_target_met` exists.

The lever is **discrimination, not more difficulty**: information scales with `a²`, so
adding sharper items raises precision far faster than adding harder ones.

## Rephrasing

Off by default (`CAT_REPHRASING_ENABLED`). An item's difficulty is calibrated against its
exact wording, so a rewritten stem is not the item the parameters describe.

When enabled, `rephrase.py` rejects rewrites that leak an answer, flip a negation, drop a
calibrated identifier, or run long — and administers the original stem whenever anything
is off. It checks **surface fidelity only**. It cannot verify that difficulty was
preserved, which is why the feature is opt-in rather than merely guarded.

## Tests

```bash
cd backend
pip install -r requirements.txt
pytest
```

No database, no network, no credentials: every test runs the deterministic path or stubs
the model. The suite covers IRT invariants that must hold exactly (monotonicity, the
guessing floor, evidence overriding a wrong prior), selection fidelity including every way
the model can misbehave, and end-to-end ability recovery against simulated candidates of
known ability.
