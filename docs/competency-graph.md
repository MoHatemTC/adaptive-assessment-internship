# The competency graph layer

What the graph is allowed to do, what it is not, and the decisions that are easy to
re-break. Written because three of them had already been broken once, silently.

Implements `CAT_DAG_FULL_IMPLEMENTATION_ARCHITECTURE.md` as amended by
`CAT_DAG_Architecture_Review.md`. Where they conflict, the review wins.

---

## The boundary: the graph never touches the posterior

> **Only a directly observed response multiplies a likelihood.** Inference is recorded,
> reported, and used to prioritise or deprioritise items. It never enters the CAT update.

This is the review's primary ruling (A1), and it is a property of the code rather than a
convention:

- The graph layer **cannot import** `app.services.orchestrator`, so it cannot construct a
  `GradedOutcome`. Enforced by `tests/test_graph_posterior_isolation.py`, which walks every
  module's imports with `ast`.
- Inference returns `InferredNodeSignal`, which has **no `score` and no `weight`**. It
  cannot be splatted into a graded outcome; the keywords do not exist.
- A golden test runs the same scripted responses with every graph flag off and every graph
  flag on, and asserts **bit-identical** theta, SE and observation counts.

### Why, concretely

Propagated evidence is a deduction *from* a response already in the likelihood.
Multiplying it in again counts one response twice — 1.80× weight inflation on the
specification's own worked example, saturating the `min(1, ·)` cap on denser graphs. The
damage is not mainly to the ability estimate. It is to the **standard error**, which is
what the assessment stops on: a session would converge on evidence it never collected.

A feature flag was not enough. It left `upward_decay`, `minimum_inferred_weight`,
`maximum_inferred_weight` and `maximum_propagation_depth` — four of the constants the
specification itself admits are uncalibrated — wired into a code path terminating at SE.
The path is gone.

**The cap is gone too.** `w_M = min(1, Σ w_i)` saturated for a single item measuring
several sub-competencies: a code question at 0.70 and 0.65 counted for exactly as much as a
flawless full-credit multiple-choice answer, and past the cap the fractional-weight
apparatus stopped distinguishing anything at all. Every outcome in a rollup comes from ONE
administered item, so summing their weights adds one response to itself.

`competency.combined_weight` now takes a probabilistic union, `1 - Π(1 - w_i)`:

| weights | old `min(1, Σ)` | now |
|---|---:|---:|
| `[1.0]` | 1.000 | 1.000 |
| `[0.70, 0.65]` | 1.000 | 0.895 |
| `[0.5, 0.5]` | 1.000 | 0.750 |
| `[0.4, 0.4, 0.4]` | 1.000 | 0.784 |

Bounded by one response, monotone in how much the item measures, and exact for a single
outcome — so every multiple-choice item updates precisely as before.

---

## Observation counting

> `VariableState.observations` counts exactly one thing: **main-level rolled outcomes with
> `weight > 0` that were folded into the posterior.**

All three CAT guards read it and nothing else — the six-observation floor, the KL→Fisher
switch at n = 3, and provisional-certainty damping. It is never incremented by a node, by
an inference, or by an unscorable response.

Node-level `direct_observations` / `inferred_observations` are graph-layer reporting only.
The report calls them `nodes_directly_measured` / `nodes_inferred` so the two can never be
read as the same quantity.

An unscorable response (`weight == 0`) — audio failure, sandbox failure — is **not a wrong
answer**. It produces no node change, no affected main, and no observation. It does consume
the item, which is intended: the question was administered, it simply carries no evidence.

---

## The two `loading`s

The specification uses one word for two quantities (review C9). They are different and
normalise differently:

| Quantity | Where | Means | Normalisation |
|---|---|---|---|
| **item→node loading** | `MeasuredVariable.weight`, via `BankItem.loading()` | how much of this item is about this node | **Not** normalised across an item. Each in (0, 1]; no sum constraint. An item may load 0.70 on one node and 0.65 on another — independent statements, not a partition. |
| **node→main contribution** | `CompetencyEdge.weight` on `CONTRIBUTES_TO` | how much of this main this node accounts for | **Sums to 1.0** over all contributions into a main. Enforced for the AI Engineer graph by `test_bank_registry.py`. |

`combined_weight` is neither: it is how much evidence ONE response carries about a main,
combined across the item→node weights in that response. The graph's `CONTRIBUTES_TO`
contribution is read by nothing today — the rollup splits by id prefix — and is authored so
the structure exists when a rollup wants it.

---

## Threshold precedence

> **The most restrictive floor wins.**

Three confidence floors exist. `prerequisite_rules.py` is the single place that resolves
them:

```
success floor = max(minimum_propagation_confidence, item.minimum_success_confidence)
failure floor = max(downward_block_confidence,       item.minimum_success_confidence)
```

A per-item floor exists to **tighten** a claim about that item, never to loosen global
policy — an item cannot license an inference the engine's own configuration would refuse.

Blocking uses the higher floor by default (0.85 vs 0.80) because the two errors are not
symmetric: inferring mastery a candidate lacks costs one unnecessary question, while
blocking denies them the chance to demonstrate a skill they have.

Those two predicates used to be inlined in three places and had **drifted** — the rollup
applied no modality gate, so one multiple-choice hit inferred prerequisite mastery that
shadow mode explicitly refused. Divergent copies of a threshold are two parts of one system
disagreeing about what a candidate demonstrated.

---

## Shadow versus enforced

> "Shadow" means the **result** is recorded but not enforced. It never means a different
> algorithm.

There is one `apply_direct_evidence`. Two projections read it: `graph_*` state feeds
eligibility, coverage and queueing; `graph_shadow_*` is the audit mirror. What differs is
which consumers are allowed to act, never what was computed.

When they were separate implementations they disagreed in four ways, and each disagreement
made the shadow record a description of something that was not happening:

1. the enforced path inferred from a multiple-choice hit; shadow refused;
2. the enforced path blocked through edges that forbid blocking, and ignored depth;
3. a missing grader confidence defaulted to 1.0 in one and 0.0 in the other;
4. the enforced path marked evidence processed *before* applying it.

---

## Blocking is soft, and probed

Blocked and mastered-non-critical items are **penalised** in ranking (−0.90 and −0.60),
not excluded. The multiplier is clamped at 0.01: at −1.0 the ranking key is zero and the
tie-break decides selection; below −1.0 the ranking **inverts**.

An excluded item can never disprove the belief that excluded it — which is why
contradiction recovery as specified could never fire, and why the false-blocking rate was
unmeasurable in production. Every `graph_unblocking_probe_period` observations, a blocked
item is served deliberately. The schedule is deterministic, not random: this design commits
to replayable sessions.

---

## Contradictions

`conflicts.py` compares **new direct evidence against existing state**. Three cases:

| Kind | Trigger | Node put in doubt |
|---|---|---|
| `blocked_but_passed` | a BLOCKED node passes outright | the **prerequisite** whose failure caused the block |
| `direct_reversal` | the same node graded both ways | the node, set to `NEEDS_VERIFICATION` |
| `wrong_inference` | an INFERRED_MASTERED node fails directly | the node, naming the evidence that inferred it |

The third is the only routine way a wrong inference becomes observable in production.

Contradictions are appended to `graph_contradictions` as **events**. The previous
set-intersection derivation had no memory: a resolved contradiction simply stopped
appearing, so nothing recorded that it had happened.

---

## Stop reasons

| Reason | Converged | Means |
|---|---|---|
| `precision` | yes | SE at target, past the observation floor, difficulty corroborated |
| `stable_band` | yes | the reported band settled at reasonable precision |
| `band_probability` | yes | the reported level is probably right. **Off by default** — it changes test length near a band cut point, which wants measuring first |
| `question_budget` | no | ran out of questions without converging |
| `bank_exhausted` | no | no items left |
| `time_budget` | no | no remaining item fits the clock |
| `graph_gates_waived` | no | **measurement converged**, but required sub-competencies were never measured |

The last one is review C5. The old code reported that case as `question_budget`, which
describes a stop the session did not make — and made the deadlock rate, a stated rollout
target, unmeasurable, because the two were indistinguishable in the record.
`graph_waived_nodes` says exactly what was skipped.

---

## Master switch

`competency_graph_enabled` defaults **True** and is read by
`competency_graph.config.graph_enabled()`, which every sub-flag ANDs with. Off means the
engine behaves as though no graph existed, whatever the sub-flags say.

It defaults True because the coverage gate is already live by default, so the graph layer
is already load-bearing. It was previously declared and read by **nothing** — worse than
having no switch, because an operator will reach for it during an incident and nothing
will happen.

---

## Unvalidated edges

The AI Engineer graph's 30 PREREQUISITE edges encode the authored sub-competency numbering
order and nothing else. They ship with `allow_upward_inference: false`,
`allow_downward_blocking: false` and `metadata.validation_status: "unvalidated"`.

Only `scripts/validate_prerequisite_edges.py --apply`, run against a real session corpus,
may enable one. See [`banks/AIE.md`](banks/AIE.md).

Three places where "disabled" did not actually mean disabled were fixed alongside them:
the rollup ignored `allow_upward_inference`; descendant blocking used the unrestricted
children index and ignored depth; and `prerequisite_edges` was declared on the graph model
and populated by nothing, so blocking was a silent no-op.

### The forecast, and how an edge earns its way in

Inert edges have a cost that only shows up in a real session: **inferred nodes = 0 and
blocked nodes = 0, structurally, for every session this bank will ever run.** Zero is also
what a broken inference engine reports, and there is no way out of it from inside — judging
an edge needs evidence about the edge, and the only route to that evidence was enabling it
on live candidates.

`graph_edge_preview_enabled` (default **True**, reporting only) runs the same inference and
the same blocking walk with the per-edge validation gate waived, and writes the result
nowhere: not the posterior, not selection, not coverage, not node state. It lives on
`AssessmentState.graph_preview_*` and it is what the DAG view draws dashed.

Waiving the validation gate waives **nothing else** — the modality rule, the decay, the
weight bounds and the depth limit all still apply. That is the property that makes it worth
reading: enable an edge and enforcement reproduces the forecast exactly, which
`test_inference_preview.py` asserts by turning the edges on and comparing.

What it buys is falsifiability, in both directions:

| kind | the graph would have | the session measured | means |
|---|---|---|---|
| `wrong_inference` | called a node mastered and stopped asking | a direct failure | the prerequisite claim does not hold |
| `false_block` | declared a node unreachable | a direct pass | a false block — the review's otherwise unmeasurable rate |

The second is measurable **only** because the block was never enforced. An excluded item
can never disprove the belief that excluded it; that is the same argument that made blocking
soft, applied one level up.

Measured on the AI Engineer graph, worth knowing before anyone reads a forecast: edge
strength 0.5, decay 0.7 and a 0.15 floor put the second hop at 0.5·0.5·0.7² = 0.1225, under
the floor. **These edges reach exactly one parent per strong success** — the depth limit of
4 never gets to bind.

`scripts/validate_prerequisite_edges.py` remains the only thing permitted to enable an
edge, and it is the stricter instrument: it wants both endpoints directly measured across a
corpus, with a Wilson interval. The forecast is the cheaper signal that says which edges are
worth running it on.

---

## Ability bands

Five levels over interior bands **1.6 logits wide**, cut at −2.4, −0.8, +0.8, +2.4.
`irt.BAND_CUTS` is the single source of truth; anything needing the inverse reads
`band_lower_bound` rather than re-deriving it.

They were 1.0 wide, which is as narrow as the eight-level scale the review criticised — so
five labels bought none of the honesty five bands were meant to. Measured at the SE target:

| band width | P(reported band is the true band), at a band centre |
|---:|---:|
| 1.0 | 0.639 |
| 1.4 | 0.799 |
| **1.6** | **0.850** |

The instrument cannot resolve a tenth of a logit at six to twelve observations; claiming a
level that fine was a claim about precision nobody had. `p_reported_band` sits beside the
level in every report, so a candidate near a cut point is visibly less certain rather than
silently reported at 90.

## Where things live

| Module | Owns |
|---|---|
| `config.py` | `PropagationConfig`, the only construction site, and every enable/disable predicate |
| `prerequisite_rules.py` | is-this-strong-enough, and threshold precedence |
| `inference.py` | `InferredNodeSignal` and the upward walk |
| `propagation.py` | applying one evidence event |
| `conflicts.py` | contradiction detection |
| `ledger.py` | idempotency, seeded from and drained into session state |
| `state.py` | node state, and its round trip through persistence |
| `coverage.py` | which sub-competencies a main requires |
| `report.py` | provenance for the assessment report |
| `graph.py` | the graph and every precomputed index |
| `validator.py` | loading, validating, cycle detection |
