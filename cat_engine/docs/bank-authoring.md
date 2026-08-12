# Authoring a bank

A bank is two files plus one row in the registry. Nothing else in the engine knows a bank id
exists.

## The two files

```
cat_engine/engine/data/question_bank_<ID>.json      items, with CAT parameters
cat_engine/engine/data/competency_graph_<ID>.json   nodes, edges, and the propagation policy
```

They are **selected together**. Pairing one bank's items with another's graph makes every
required coverage node unmeasurable and vetoes convergence for the whole session, which
presents as a measurement fault and is a configuration one. The registry enforces the
pairing; `Orchestrator._graph_covers_bank` fails **open** with a loud log if they mismatch,
because an ungated engine is a recoverable failure and a permanent convergence veto is not.

## Registering it

```python
# cat_engine/engine/services/orchestrator/registry.py
"AIE": BankProfile(
    bank_id="AIE",
    title="AI Engineer",
    bank_path=DATA / "question_bank_AIE.json",
    graph_path=DATA / "competency_graph_AIE.json",
    mains=("C1", "C3", "C6"),
    coverage_critical_only=True,
),
```

`coverage_critical_only` is per bank because the right answer depends on how many
sub-competencies a main declares against the question budget. C6 declares sixteen and every
item measures exactly one; full coverage would cost sixteen questions against a cap of
twelve, so the gate could never be satisfied and every session would end on the budget
escape.

## An item

```json
{
  "item_id": "C1-Q001",
  "modality": "mcq",
  "status": "active",
  "competency": "Software and AI Application Engineering",
  "sub_competency": "C1.1 · Core Python & programming fundamentals",
  "measures": [{"variable": "C1.1", "weight": 1.0}],
  "cat": {"a": 2.13, "b": -2.43, "c": 0.09},
  "estimated_time_seconds": 75,
  "mcq": {"stem": "...", "options": ["..."], "answer_index": 2, "rationale": "..."}
}
```

The envelope carries what the **engine** needs — which variables the item measures and its
parameters on theta. The modality payload carries what the **grader** needs. That split is
what lets a new modality be added without touching selection, and it is why selection can
run without being able to read a question.

### Item parameters

`a` discrimination (0, 3], `b` difficulty [-4, 4], `c` guessing floor [0, 1).

**These must be calibrated, not assigned.** An `a` inflated by 1.3× overstates information
by 1.69×, which shows up as a standard error understated by 30% — and SE is what the
assessment stops on. Run `scripts/calibrate_selection_constants.py` and check
`bank.parity_report()`: a modality that can never win a ranking anywhere is miscalibrated,
one that wins only at some abilities is working as intended.

### Per-modality requirements

| modality | payload must contain |
|---|---|
| `mcq` | `stem`, `options`, `answer_index` |
| `code` | `function_name`, `tests`, `rubric_criteria`, `prompt` |
| `voice` / `open` | `question`, and either `rubric_id` or `rubric_criteria` |

A malformed item is **named and skipped**, not fatal: one bad item should not deny every
candidate an assessment, but it must not pass silently either.

## A competency graph

```json
{
  "version": "1.1",
  "bank_id": "AIE",
  "notes": "what this graph encodes and on what basis",
  "policy": {
    "upward_inference": false,
    "descendant_blocking": false,
    "accepted_validation_statuses": ["validated"],
    "minimum_failures_to_block": 2,
    "notes": "why"
  },
  "nodes": [
    {"competency_id": "C1", "title": "...", "node_type": "main", "critical": true},
    {"competency_id": "C1.1", "title": "...", "node_type": "sub_competency",
     "critical": true, "main_competencies": ["C1"], "shared": false}
  ],
  "edges": [
    {"from": "C1.1", "to": "C1", "relation": "CONTRIBUTES_TO", "weight": 0.1429},
    {"from": "C1.1", "to": "C1.2", "relation": "PREREQUISITE", "strength": 0.5,
     "allow_upward_inference": false, "allow_downward_blocking": false,
     "metadata": {"validation_status": "unvalidated", "basis": "..."}}
  ]
}
```

Validated at load: unique ids, every edge endpoint present, positive prerequisite strength,
**no prerequisite cycles**, and a well-formed policy block. A graph that fails validation
raises rather than loading half.

### A shared node

Set `main_competencies` to more than one main. One node holds one authoritative state and
every main it contributes to is recalculated from it — that is the whole point, and it is
what makes evidence reuse real rather than notional.

### Authoring prerequisite edges

**Ship them inert.** `allow_upward_inference: false`, `allow_downward_blocking: false`,
`validation_status: "unvalidated"`.

An edge is a claim about the world — "you cannot do B without A" — and it is a claim that
[evidence.md](evidence.md) shows **cannot be validated from observational response data**.
Not by the raw conditional, not by regression on ability, not by ability-stratified
contrasts: real and spurious edges are statistically indistinguishable. Establishing one
needs an experiment — serve the child to candidates who failed the parent — which is what
`C-DAG-01` describes.

Until an edge has been through that, `basis` should say what it actually rests on. The
shipped AI Engineer edges say `sub_competency_numbering_order`, which is honest: they encode
the order someone wrote the sub-competencies in.

## Checklist

- [ ] every item's `measures` names a node that exists in the graph
- [ ] `bank.parity_report()` shows no miscalibrated modality
- [ ] `scripts/validate_prerequisite_edges.py` run, if a session corpus exists
- [ ] required-node count per main is reachable within `CAT_MAX_QUESTIONS`
- [ ] `python -m scripts.show_propagation_policy --bank <ID>` reads as intended
- [ ] `pytest tests/test_bank_registry.py`
