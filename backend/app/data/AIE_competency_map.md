# AI Engineer — competency map

Companion to `question_bank_AIE.json` (300 items) and `competency_graph_AIE.json`
(36 nodes, 63 edges). Registered as bank id `AIE`; it is the default (`ACTIVE_BANK`).

> **The PREREQUISITE edges in this graph are unvalidated hypotheses.** They ship with
> `allow_upward_inference: false` and `allow_downward_blocking: false`. Only
> `scripts/validate_prerequisite_edges.py --apply`, run against a real session corpus,
> may enable one. See [Prerequisite edges](#prerequisite-edges).

## Bank

| | items | mcq | code | voice |
|---|---:|---:|---:|---:|
| C1 · Software and AI Application Engineering | 100 | 50 | 25 | 25 |
| C3 · Machine Learning and Statistical Engineering | 100 | 50 | 25 | 25 |
| C6 · LLM, Prompt, RAG and Knowledge Engineering | 100 | 50 | 25 | 25 |

## Shared sub-competencies

Four sub-competencies serve **two** main competencies each. One node holds one
authoritative state; every main it contributes to is recalculated from it.

| node | home | also serves | why |
|---|---|---|---|
| C1.6 · AI application integration | C1 | C6 | integrating an LLM into an application is the LLM track's delivery surface |
| C6.3 · Model sourcing & serving | C6 | C1 | serving a model is a backend-services problem |
| C3.9 · Metric selection & model comparison | C3 | C6 | choosing a retrieval metric IS metric selection |
| C3.10 · Error analysis & model calibration | C3 | C6 | evaluating a RAG system IS error analysis |

**19 items are cross-loaded**, naming a variable under a second main at partial weight —
0.5 where the question sits squarely in both, 0.35 for a narrower overlap. The secondary
weight is deliberately not normalised against the primary: `measures[].weight` means "how
much of this item is about this variable", and two such statements are independent, not a
partition. The table lives in `scripts/build_aie_bank.py` as `SHARED_LOADINGS`, so the
judgement is reviewable in a diff and the bank stays reproducible.

The two mechanisms do different work and both are needed:

- a **cross-loaded item** makes one response update two posteriors and invalidate two
  queue slots;
- a **shared node** makes coverage for either main satisfied by measuring it once.

### What it is worth, measured

Over six simulated AI Engineer sessions:

| | evidence reuse (main updates per scored response) |
|---|---:|
| shared-main gain off | **1.109** |
| shared-main gain on (`GRAPH_UTILITY_ENABLED=true`) | **1.126** |

Above 1.0, so the sharing is real — but below the review's Tier-2 bar of **1.3**, at which
the machinery would be earning its complexity. The binding constraint is the number of
cross-loaded items (19 of 300), not the utility weight: selection can only reuse evidence
the bank actually shares. Raising it means authoring more genuinely dual-topic items, not
turning the gain up.

Before this, every item measured exactly one sub-competency at weight 1.0 and every
sub-competency belonged to exactly one main, so no response could ever move more than one
estimate and the shared-evidence half of the design had nothing to act on.

`voice` is a first-class modality here, not `open`. It grades through the same evaluator
and the same four rubric criteria; the distinction is that the answer is spoken, which the
report and the Live interview path both need to know.

## Sub-competencies

Critical nodes are marked ★ — see [Coverage](#coverage) for what that decides.

| C1 | | C3 | | C6 | |
|---|---|---|---|---|---|
| ★ C1.1 | Core Python & programming fundamentals | ★ C3.1 | Probability, linear algebra & optimisation | ★ C6.1 | LLM internals, embeddings & sampling |
| ★ C1.2 | Data structures & algorithms | ★ C3.2 | Classical ML algorithms & ensembles | C6.2 | Model families & capabilities |
| C1.3 | Code quality & engineering workflow | C3.3 | Feature engineering & training data | C6.3 | Model sourcing & serving |
| ★ C1.4 | Backend services & APIs | C3.4 | Baselines & algorithm selection | C6.4 | Model selection, routing & cost |
| C1.5 | Persistence & access control | C3.5 | Training, tuning & inference efficiency | ★ C6.5 | Instruction & few-shot prompting |
| ★ C1.6 | AI application integration | C3.6 | Bias, variance & regularisation | C6.6 | Structured output, chaining & tools |
| | | ★ C3.7 | Overfitting & generalisation | C6.7 | Context assembly & token budgeting |
| | | C3.8 | Dataset splitting & cross-validation | C6.8 | Prompt versioning & evaluation |
| | | ★ C3.9 | Metric selection & model comparison | C6.9 | Trusted context & injection defence |
| | | C3.10 | Error analysis & model calibration | ★ C6.10 | RAG design & document processing |
| | | C3.11 | Robustness, fairness & interpretation | ★ C6.11 | Vector stores & hybrid search |
| | | | | C6.12 | Metadata & permission-aware filtering |
| | | | | C6.13 | Query rewriting & reranking |
| | | | | ★ C6.14 | Grounding & citations |
| | | | | C6.15 | Retrieval evaluation |
| | | | | C6.16 | Multi-hop & graph RAG |

## The C6 remap

The 25 C6 **code** items were authored against different numbering than the 100 C6 mcq and
voice items. `C6.8` meant "Prompt versioning & evaluation" to one author and "Decoding &
sampling" to the other; 24 of the 25 disagreed. Nothing crashed — the engine keys on the
variable id — so a candidate's decoding answer was recorded as evidence about prompt
versioning and the report said something false.

`scripts/build_aie_bank.py` carries `C6_CODE_REMAP`, which re-points those items by
**label**. The table is checked into the script so the judgement is reviewable in a diff,
and `--strict` fails on any C6 code label not in it.

**Confirmed by the bank owner on 2026-08-03.** 23 of the 25 C6 code items move; the two
already labelled "Vector stores & hybrid search" were correct as authored. `item_id`s were
not renamed, so `code_c6_007` still carries its authored name while measuring `C6.15`.

The full table is `C6_CODE_REMAP` in `scripts/build_aie_bank.py`, and
`--strict` fails the build on any C6 code label not in it — a new item cannot be added
under the old numbering without someone deciding where it belongs.

C1 and C3 needed no remap — their disagreements were wording variants of the same concept
("Persistence/auth" vs "Persistence & access control"), normalised through `CANONICAL_SUBS`
with the authored string preserved as `source_sub_competency` in the payload.

## Coverage

`graph_convergence_gate_enabled` is on by default: a main may not claim convergence until
every *required* sub-competency has direct, scorable evidence. Required means **critical
only** for this bank (`BankProfile.coverage_critical_only = True`).

The arithmetic, against `cat_max_questions = 12` and one node measured per question:

| main | sub-competencies | required (all) | required (critical) |
|---|---:|---:|---:|
| C1 | 7 (6 own + C6.3) | 7 | **4** |
| C3 | 11 | 11 | **4** |
| C6 | 19 (16 own + C1.6, C3.9, C3.10) | **19 — impossible** | **7** |

A shared node counts toward both mains' coverage, which is the point: measuring C1.6 once
satisfies it for C1 and for C6.

Requiring all sixteen C6 nodes would veto every convergence and end every C6 session on the
budget escape, reporting `question_budget` for a session that had the precision to certify.
Critical-only leaves 7–8 free questions per main after coverage, so the six-observation
precision floor is reachable.

`tests/test_bank_registry.py::test_the_coverage_requirement_is_reachable_within_the_question_budget`
enforces this permanently: adding a seventeenth C6 sub-competency, or promoting three more
to critical, fails the suite rather than quietly breaking convergence.

### How the critical set was chosen

An editorial judgement, stated as such: the numbering root of each main, plus the areas the
main's own title names. C6 gets five because its title names four things — LLM, prompt, RAG,
knowledge — and retrieval is split into design (C6.10) and store (C6.11), which are the two
places a retrieval system actually fails. It is not derived from data. Capped at five so the
gate stays satisfiable.

## Prerequisite edges

30 edges, one per consecutive pair within a node's HOME main: `Ck.i → Ck.i+1`. A shared
node keeps one chain — being reused by another main is not a second dependency. **The rule is the
authored numbering order and nothing else.** There is no validated dependency data for these
competencies, and inventing cross-topic edges would be fabricating domain knowledge the
source bank does not contain.

They ship inert:

```json
{
  "from": "C6.10", "to": "C6.11", "relation": "PREREQUISITE", "strength": 0.5,
  "allow_upward_inference": false,
  "allow_downward_blocking": false,
  "metadata": {"validation_status": "unvalidated", "basis": "sub_competency_numbering_order"}
}
```

`strength: 0.5` — against 0.75–0.85 in the DA graph — because these are hypotheses, and
strength multiplies through any inference that ever reads them.

Worth naming: a pure chain gives C6.16 fifteen transitive ancestors. If upward inference
were enabled on these edges, one strong answer on multi-hop RAG would infer mastery of the
entire C6 track. That is why they ship disabled, and why the three places where "disabled"
did not actually mean disabled were fixed alongside them.

To enable any of them:

```bash
PYTHONPATH=. python scripts/validate_prerequisite_edges.py --bank AIE --sessions <corpus>
PYTHONPATH=. python scripts/validate_prerequisite_edges.py --bank AIE --sessions <corpus> --apply
```

An edge is validated only when it has at least 30 sessions where the parent failed and the
child was directly tested, the Wilson 95% upper bound on P(pass child | fail parent) is at
or below 0.25, and the contrast against P(pass child | pass parent) is positive beyond its
own interval. Refuted edges are marked and kept, never deleted — a negative result is a
result.

## CONTRIBUTES_TO weights

One edge per sub-competency, uniform within each main and summing to exactly 1.0
(C1 over 7 contributors, C3 over 11, C6 over 19 — the shared nodes are counted in the
mains they serve, so adding one renormalises rather than inflating). Uniform on purpose: an item
count reflects how much of the bank an author happened to write, not how much the
sub-competency matters.

These weights are **not read by anything today** — the rollup splits by id prefix. They are
authored now so the structure exists when a rollup consumes it.
