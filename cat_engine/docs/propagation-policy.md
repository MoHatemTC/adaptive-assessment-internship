# Propagation policy

How to turn upward inference and descendant blocking on — and why they are off.

## Three levels, most restrictive wins

| Level | Where | Owned by |
|---|---|---|
| **1. Deployment** | `.env` / environment | whoever runs the service |
| **2. Bank** | `policy` block in the competency graph JSON | whoever authors the bank |
| **3. Edge** | per-edge flags and `validation_status` | whoever validated that edge |

An edge acts only if **all three** permit it. A narrower scope may tighten, never loosen —
the same convention the confidence floors already use. There is deliberately no value
meaning "this bank forces it on".

```
                    inference actually fires
                              =
   GRAPH_UPWARD_INFERENCE_ENABLED     (deployment: does this deployment act on it)
     AND  policy.upward_inference     (bank: is this a valid dependency here)
     AND  edge.allow_upward_inference (edge: has anyone validated this one)
     AND  edge.validation_status accepted by the bank
```

### Why the deployment level is separate from the other two

The bank and edge levels answer *"is this a valid dependency?"* — a property of the world.
The deployment level answers *"does this deployment act on it?"* — a property of the rollout.

Only the first two are baked into the edge set at load. The deployment switch is applied at
the enforcement point, so the **audit mirror keeps working**: a deployment with propagation
off still records what the graph *would* have concluded, which is the evidence you need to
decide whether to turn it on. Silencing the computation as well as the action would blind
exactly the deployment that most needs to see it.

## Deployment level

```bash
GRAPH_UPWARD_INFERENCE_ENABLED=false     # default
GRAPH_DESCENDANT_BLOCKING_ENABLED=false  # default
GRAPH_MINIMUM_FAILURES_TO_BLOCK=2        # default
```

> `GRAPH_UPWARD_INFERENCE_ENABLED` was previously declared in settings and **read by
> nothing** — inference was gated only by the per-edge flag, so an operator turning the
> feature off saw no change. It is now enforced in `orchestrator/graph_delta.py`,
> symmetrically with the blocking switch.

## Bank level

An optional `policy` block at the top of a competency graph file:

```json
{
  "version": "1.1",
  "bank_id": "AIE",
  "policy": {
    "upward_inference": false,
    "descendant_blocking": false,
    "accepted_validation_statuses": ["validated"],
    "minimum_failures_to_block": 2,
    "notes": "why this bank is configured this way"
  },
  "nodes": [...],
  "edges": [...]
}
```

| field | values | meaning |
|---|---|---|
| `upward_inference` | `true` / `false` / omitted | omitted = no opinion, defer to the deployment |
| `descendant_blocking` | `true` / `false` / omitted | as above |
| `accepted_validation_statuses` | list | which edge statuses license action. Default `["validated"]`. `"refuted"` is refused at load |
| `minimum_failures_to_block` | int | may only *raise* the deployment's value |
| `notes` | string | shown by the policy diagnostic |

The block travels with the bank, which matters because a bank and its graph are selected
together and an edge set validated for one bank says nothing about another.

## Edge level

```json
{
  "from": "C1.1", "to": "C1.2", "relation": "PREREQUISITE", "strength": 0.5,
  "allow_upward_inference": false,
  "allow_downward_blocking": false,
  "metadata": {
    "validation_status": "unvalidated",
    "basis": "sub_competency_numbering_order",
    "n_parent_failures": 0,
    "p_pass_child_given_fail_parent": null
  }
}
```

`validation_status` is one of `validated`, `provisional`, `unvalidated`, `refuted`.

**An edge that states no status at all defers to its own `allow_*` flags.** That keeps this
change additive: a graph authored before the policy block existed behaves exactly as it did.
A *declared* `unvalidated` is a stronger claim — "nobody has checked this" — and is held
inert whatever the flags say.

## Working out why an edge is inert

```bash
python -m scripts.show_propagation_policy --bank AIE
python -m scripts.show_propagation_policy --bank AIE --what-if inference=on,blocking=on
python -m scripts.show_propagation_policy --bank AIE --json
```

`--what-if` resolves against a hypothetical deployment without needing that deployment to
exist — the safe way to answer "what would turning this on actually enable?".

Each edge reports the **widest scope still refusing**, because that is the one you can act
on. Being told an edge is disabled when the whole feature is off sends you to the wrong file.

## The evidence for the defaults

Measured on the AI Engineer bank, 1,600 candidates balanced across eight ability strata,
with prerequisite edges **correct by construction**:

| gate | bar | measured |
|---|---|---|
| C-DAG-03 wrong inference | < 3% | **22.4%** (95% UCB 24.4%), precision 0.776 vs 0.97 |
| C-DAG-04 false blocking | < 2% | **8.7%** (95% UCB 9.6%) |
| C-DAG-05 missed blocking | — | 94.9% |

And a result that is structural rather than fixable:

> **A block fired on *perfect* knowledge of the parent is wrong 10.8–14.6% of the time**,
> because a prerequisite relation is probabilistic — candidates learn out of order. To pass
> a 2% gate an edge would have to bind ≥98% of the time. That is a claim about the world,
> not about the engine.

Requiring two consistent parent failures cut blocks from 23.5 to 5.3 per session, which is
worth having, but it is a mitigation and not a route to the gate.

See [evidence.md](evidence.md) for the full study and [operations.md](operations.md) for
the checklist before enabling anything.
