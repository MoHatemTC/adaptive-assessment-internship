# `.../services/competency_graph/`

The competency graph layer.

## The rule the whole design rests on

**Only a directly observed response may move a posterior.**

The graph changes **what is asked** and **when the test may stop**. It never changes **what
is estimated**. A deduction *from* a response is not a second response; multiplying it back
in counts one answer twice, and the damage lands on the standard error — which is what the
assessment stops on.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The package surface, including `load_competency_graph` and `DEFAULT_GRAPH_PATH`. |
| `models.py` | The graph itself: nodes, edges, relations, and the policy attached to them. |
| `graph.py` | `CompetencyGraphService`: traversal, sub-node enumeration, the queries selection makes on every candidate on every step. |
| `state.py` | Per-node session state — direct, inferred, blocked, contradicted, with provenance. |
| `propagation.py` | Apply one evidence event to the graph. The transaction. |
| `inference.py` | What the graph concludes about a node it did not directly test. **Off by default.** |
| `conflicts.py` | Detect when new direct evidence contradicts what the graph already believed. |
| `ledger.py` | Which evidence has already been applied, across the whole session. Deterministic evidence ids are what make a retry safe. |
| `coverage.py` | The coverage requirement: a competency may not claim convergence until its required sub-competencies have direct evidence. |
| `policy.py` | Who is allowed to turn prerequisite propagation on, and at which level — deployment, bank, edge. All three must agree. |
| `config.py` | Propagation policy, and the one place it is constructed. |
| `prerequisite_rules.py` | Is this response strong enough to license a graph consequence? |
| `manifest.py` | What propagation configuration a session actually ran under. |
| `report.py` | Why every competency is in the state it is in. |
| `validator.py` | Whether a graph pairs with its bank — a mismatch marks every required node unmeasured and vetoes convergence for the whole session. |
| `evidence.py` | Evidence records and their provenance. |

## Propagation ships inert, and that is a measurement decision

A completed screening study measured upward inference wrong 22.4% of the time against a 3%
gate, and blocking producing 8.7% false blocks against a 2% gate — on this bank, with
prerequisite edges correct by construction. The defaults are off because the evidence says
off. See [docs/evidence.md](../../../docs/evidence.md) and
[docs/propagation-policy.md](../../../docs/propagation-policy.md).

What IS live is the **coverage gate**.
