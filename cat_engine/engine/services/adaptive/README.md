# `.../services/adaptive/`

The MCQ engine. 3PL item response theory, EAP on a fixed grid.

## The measurement, in full

A candidate's ability on one competency is a latent variable `theta` on `[-4, 4]`. Under the
three-parameter logistic model, the probability that they answer item *i* correctly is

```
P(theta) = c + (1 - c) / (1 + exp(-a (theta - b)))
```

| | |
|---|---|
| `a` | **discrimination** — how sharply the item separates ability either side of `b`. High `a` is a narrow, informative item; low `a` is one almost everyone gets the same way. |
| `b` | **difficulty** — the ability at which the curve is halfway between the guess floor and 1. |
| `c` | **guessing floor** — for four options, around 0.25. Without it the model says a candidate who knows nothing scores nothing, and four-option items say otherwise. |

The posterior over `theta` is held as **41 points on a fixed grid**, not as a distribution
with parameters. Three consequences, and all three are why:

- **Exactly reproducible.** No optimiser, no seed, no convergence criterion — the update is
  multiplication and normalisation, so the same responses give bit-identical results.
- **Cheap.** 41 multiplies per response.
- **Serialisable.** It is a `list[float]`, which is what lets a session be persisted and
  resumed. The measurement decision and the storage decision turn out to be the same one.

The point estimate is the posterior mean (EAP) and the standard error is its standard
deviation, so "how sure are we" is read off the same object as "what do we think".

## Choosing the next item

Two criteria, and the engine switches between them:

- **KL divergence** early, when the posterior is wide. Expected information is a poor guide
  when you do not yet know roughly where the candidate is.
- **Expected Fisher information** once there is evidence. It is the standard criterion and
  it is sharper, but it maximises information *at the current estimate*, which is the wrong
  place to look when that estimate is nearly uninformed.

Selection also honours exposure control, a content-balance floor, and a difficulty
corroboration window — a candidate should not be handed six items in a row from one
sub-competency at one difficulty just because that maximised a number.

## Files

| File | Responsibility |
|---|---|
| `irt.py` | The measurement core: the 3PL curve, the likelihood, Fisher information, the posterior update on the grid, and the band cut points. Everything else here is a caller. |
| `selection.py` | Choosing the next item — the two criteria, the phase switch between them, and the constraints on top. |
| `convergence.py` | When to stop, and how confident the result is: the standard-error target, the stability window, the band probability, and the question cap. |
| `session.py` | The public entry point: run one competency, question by question. |
| `bank.py` | Where items come from, behind an `ItemRepository` seam — and whether the bank can support a test at all. |
| `personfit.py` | Was this response surprising, given the posterior? A run of surprises is a candidate who is guessing, cheating, or mis-calibrated — all worth flagging, none worth acting on automatically. |
| `rephrase.py` | The guard on model-rewritten stems: no answer leak, no polarity flip, no dropped calibrated token, no essay. Every rejection returns the ORIGINAL, so a bad rewrite costs only tokens. |
| `llm.py` | Async JSON-returning calls via the LiteLLM proxy. |
| `prompts.py` | System prompts. |
| `__init__.py` | The package surface: the session, the repositories and the IRT primitives a caller needs. |

## What the model is not allowed to do

It may pick from a shortlist the engine has already ranked. It does not grade, does not
write an estimate, does not choose which competency to probe, and cannot stop an assessment.
If the model is unavailable, selection takes the engine's own top pick and the session
continues — which is why `use_llm=False` produces a complete, valid assessment.
