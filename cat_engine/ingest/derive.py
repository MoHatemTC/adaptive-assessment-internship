"""Deriving a competency graph from a bank of questions alone.

WHY THIS EXISTS

An author uploads QUESTIONS. They do not author a competency graph and are never asked for
one. But a bank without a graph cannot run the coverage gate, and cannot be scoped at all —
there are no sub-competency nodes to select. So the graph has to come from the items.

THIS IS NOT NEW GROUND. `backend/scripts/import_human_test_banks.py:374` already builds a
coverage-only graph from a bank, and two of the five checked-in banks ship with a graph made
that way: `AIE-JR-V3` and `JAI-600` have CONTRIBUTES_TO edges and no prerequisite edges at
all. What is added here is the WEIGHT on each edge, and the relation each weight implies.

THE ONE THING QUESTIONS CANNOT TELL YOU: WHICH COMPETENCIES ARE CRITICAL

The coverage gate requires every CRITICAL sub-competency to have direct evidence before a
competency may converge. Nothing in a file of questions says one matters less than another,
so a derivation with no other information has to mark them all critical — and then a main
with more sub-competencies than the question budget allows can never be satisfied, and the
bank is refused. `question_bank_AIE.json` is such a bank: C6 declares sixteen against a cap
of twelve.

So the bank file may declare its own competencies:

    {"schema_version": 2,
     "competency": "AI Engineer",
     "competencies": [
       {"id": "C1",   "title": "Software and AI Application Engineering", "main": true},
       {"id": "C1.1", "title": "Core Python", "critical": true},
       {"id": "C1.6", "title": "AI application integration", "critical": true,
        "mains": ["C1", "C6"]}
     ],
     "items": [...]}

STILL ONE FILE, AND STILL NOT A GRAPH. It has no edges. It is the author naming the
taxonomy they already had to know to write a `sub_competency` label on every item, and it
carries the two things an id prefix cannot express: which nodes are critical, and which
serve more than one main.

The block is OPTIONAL. Without it the derivation behaves exactly as before — every node
critical, membership by prefix — which is the right default for a bank that says nothing:
requiring everything is the strict reading, and it fails loudly at upload rather than
quietly at convergence.

WHERE THE WEIGHTS COME FROM, AND WHY IT HAD TO BE DECIDED

A bank file carries `measures[].variable` and a weight, which is an ITEM-to-NODE loading.
Nothing in it says how two nodes relate. The weights below are therefore derived from
CO-MEASUREMENT — what the bank's own items reveal about which competencies travel together:

    sub -> main   |items measuring sub| / |items measuring main|
    sub -> sub    |items measuring both| / |items measuring the source|

Both are un-normalised and land in [0, 1], which is what makes a 0.5 threshold able to
discriminate at all. The authored CONTRIBUTES_TO weights on the checked-in graphs are
normalised to sum to 1.0 per main, so no edge on any real bank exceeds 0.1429 and a
threshold against THOSE could never fire.

A SHARED NODE GETS A DIFFERENT WEIGHT INTO EACH MAIN IT SERVES, which falls out of the
denominator rather than being special-cased.

THIS IS A MODELLING DECISION, NOT A DERIVATION FROM FIRST PRINCIPLES. It is confined to this
module so that replacing it — with authored relations in the bank file, or with anything
better — touches one file. Both thresholds are settings.

WHAT THE RELATION THRESHOLD DOES, AND WHAT IT DELIBERATELY DOES NOT

Above the threshold a sub-to-sub relation is PREREQUISITE; below it, CONTRIBUTES_TO.

A sub-to-main edge is ALWAYS CONTRIBUTES_TO, whatever its weight. Two reasons, and both are
about not breaking something that works. The engine reads a main's membership from
`node.main_competencies`, but `docs/competency-graph.md` documents CONTRIBUTES_TO mass as
summing to 1.0 per main, and `competency-scope` computes `retained_weight` from it — an edge
that changed relation would silently drop out of that sum and make a scoped report understate
its own coverage. And a PREREQUISITE edge into a main is inert by construction anyway: mains
carry no direct evidence, so nothing can infer from one or be blocked by one.

EVERY DERIVED PREREQUISITE EDGE SHIPS INERT

`allow_upward_inference=False`, `allow_downward_blocking=False`,
`validation_status="unvalidated"` — exactly as every checked-in bank ships them, and for the
measured reason: upward inference was wrong 22.4% of the time against a 3% gate. An edge
derived from co-measurement has strictly less authority than one an expert authored, so
shipping it able to infer would be inverting the evidence. `scripts/validate_prerequisite_edges.py`
against a real session corpus remains the only way to promote one.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)

#: Relations at or above this are PREREQUISITE, below are CONTRIBUTES_TO.
DEFAULT_RELATION_THRESHOLD = 0.5
#: Below this, no sub-to-sub edge is emitted at all. Without a floor, a bank whose items
#: each measure one variable produces nothing, and one with heavily multi-measuring items
#: produces a near-complete graph of weak edges that says nothing.
DEFAULT_EDGE_FLOOR = 0.05

CONTRIBUTES_TO = "CONTRIBUTES_TO"
PREREQUISITE = "PREREQUISITE"


def main_of(variable: str) -> str:
    """`C1.1` -> `C1`. The engine's own rule, copied rather than imported.

    `competency.main_competency` lives in the orchestrator, which ingest deliberately does not
    import. One line, and a test asserts the two agree.
    """
    return variable.split(".")[0]


def _title_for(variable: str, items: list[dict[str, Any]], field: str) -> str:
    """The label the bank's own items give this node, by majority vote.

    A vote rather than first-wins because the label is authored per item and banks disagree
    with themselves — `JAI-600` prefixes some labels with the code and not others. The
    majority is the one a reader would recognise.
    """
    labels = Counter(
        str(item.get(field) or "").strip()
        for item in items
        if any(m.get("variable") == variable for m in item.get("measures", []))
    )
    labels.pop("", None)
    return labels.most_common(1)[0][0] if labels else variable


def parse_declaration(raw: Any) -> dict[str, dict[str, Any]]:
    """The optional `competencies` block, indexed by node id.

    Tolerant of both shapes an author might reach for — a list of objects carrying `id`, or
    a mapping of id to properties — because this is hand-written content and rejecting a
    bank over which of two obvious spellings was used would be the wrong place to be strict.
    """
    declared: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for node_id, body in raw.items():
            declared[str(node_id)] = dict(body) if isinstance(body, dict) else {}
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            node_id = str(entry.get("id") or entry.get("competency_id") or "").strip()
            if node_id:
                declared[node_id] = dict(entry)
    return declared


def derive_graph(
    bank_id: str,
    items: list[dict[str, Any]],
    *,
    declared: dict[str, dict[str, Any]] | None = None,
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD,
    edge_floor: float = DEFAULT_EDGE_FLOOR,
) -> dict[str, Any]:
    """A competency graph, from the questions plus whatever the bank declares about them.

    Only ACTIVE items count. A retired item is one the bank has decided may not be
    administered, so letting it declare a node would create a coverage requirement that no
    servable question can ever satisfy — the gate would then veto convergence forever.
    """
    declared = declared or {}
    active = [i for i in items if str(i.get("status", "active")) == "active"]

    measured_by: dict[str, set[str]] = defaultdict(set)
    for item in active:
        for measure in item.get("measures", []):
            variable = str(measure.get("variable", "")).strip()
            if variable:
                measured_by[variable].add(str(item.get("item_id", "")))

    def mains_for(node_id: str) -> list[str]:
        """Which mains a sub-competency serves. The declaration wins over the id prefix.

        This is the ONLY way a shared node can exist in a derived graph — `C1.6` serving
        both C1 and C6 is authored knowledge, and `C1.6`.split('.')[0] can only ever say C1.
        """
        entry = declared.get(node_id) or {}
        stated = entry.get("mains") or entry.get("main_competencies")
        if stated:
            return [str(m) for m in stated]
        return [main_of(node_id)]

    subs = sorted(v for v in measured_by if "." in v)
    mains = sorted(
        {m for v in measured_by for m in (mains_for(v) if "." in v else [main_of(v)])}
    )

    #: Items serving a main: the union over its sub-competencies, plus items measuring the
    #: main directly. A union rather than a sum — one item measuring two of C1's
    #: sub-competencies is one question about C1, not two.
    main_items: dict[str, set[str]] = defaultdict(set)
    for variable, item_ids in measured_by.items():
        for main in (mains_for(variable) if "." in variable else [main_of(variable)]):
            main_items[main] |= item_ids

    nodes: list[dict[str, Any]] = [
        {
            "competency_id": main,
            "title": str(
                (declared.get(main) or {}).get("title")
                or _title_for(
                    next((v for v in measured_by if main_of(v) == main), main),
                    active,
                    "competency",
                )
            ),
            "node_type": "main",
            "critical": True,
            "context_specific": False,
            "main_competencies": [],
        }
        for main in mains
    ]
    nodes += [
        {
            "competency_id": sub,
            "title": str(
                (declared.get(sub) or {}).get("title")
                or _title_for(sub, active, "sub_competency")
            ),
            "node_type": "sub_competency",
            # CRITICAL UNLESS THE BANK SAYS OTHERWISE. With no declaration every node is
            # required, which is the strict reading and the right default for a file that
            # says nothing — a main declaring more sub-competencies than the budget allows
            # is then REFUSED at upload, naming that main, where an author can still act on
            # it. A declaration is how an author narrows that, and it is the only thing a
            # file of questions cannot imply.
            "critical": bool((declared.get(sub) or {}).get("critical", True)),
            "context_specific": False,
            "main_competencies": mains_for(sub),
            "shared": len(mains_for(sub)) > 1,
        }
        for sub in subs
    ]

    edges: list[dict[str, Any]] = []

    # --- sub -> main: always CONTRIBUTES_TO. See the module docstring. -------
    #
    # NORMALISED TO SUM TO 1.0 PER MAIN, because that is what every authored graph in this
    # repository does and what `docs/competency-graph.md` documents CONTRIBUTES_TO mass to
    # mean. The raw coverage share does NOT sum to one — an item measuring two of a main's
    # sub-competencies is counted by both numerators and once by the union denominator — so
    # emitting it directly would introduce a second, quietly different convention for the
    # same field.
    #
    # Nothing is lost: the share is kept in metadata, and the relative ORDER of the weights
    # — which is the part carrying information about the bank — is unchanged by scaling.
    # Normalising is safe here precisely because a sub-to-main edge is never subject to the
    # relation threshold.
    #: (sub, main) -> raw coverage share. A SHARED node gets a different share into each
    #: main it serves, which falls out of the denominator rather than being special-cased.
    shares: dict[tuple[str, str], float] = {}
    for sub in subs:
        for main in mains_for(sub):
            denominator = len(main_items[main]) or 1
            shares[(sub, main)] = len(measured_by[sub]) / denominator

    totals: dict[str, float] = defaultdict(float)
    for (_, main), share in shares.items():
        totals[main] += share

    for (sub, main), share in sorted(shares.items()):
        total = totals[main] or 1.0
        edges.append(
            {
                "from": sub,
                "to": main,
                "relation": CONTRIBUTES_TO,
                "weight": round(share / total, 6),
                "metadata": {
                    "basis": "item_co_measurement",
                    "coverage_share": round(share, 6),
                },
            }
        )

    # --- sub -> sub: the threshold decides the relation ----------------------
    #
    # Only the STRONGER direction of each pair survives. Co-measurement shares the same
    # numerator both ways and differs only in denominator, so A->B and B->A can both clear
    # the threshold — and two PREREQUISITE edges between one pair is a cycle, which
    # `parse_and_validate_graph` refuses. Every upload of a bank with multi-measuring items
    # would fail, for a reason no author could act on.
    pairs: dict[tuple[str, str], float] = {}
    for a in subs:
        for b in subs:
            if a >= b:
                continue
            shared = measured_by[a] & measured_by[b]
            if not shared:
                continue
            forward = len(shared) / len(measured_by[a])
            backward = len(shared) / len(measured_by[b])
            if forward >= backward:
                pairs[(a, b)] = forward
            else:
                pairs[(b, a)] = backward

    for (source, target), weight in sorted(pairs.items()):
        if weight < edge_floor:
            continue
        if weight >= relation_threshold:
            edges.append(
                {
                    "from": source,
                    "to": target,
                    "relation": PREREQUISITE,
                    "strength": round(weight, 6),
                    # INERT, like every checked-in prerequisite edge. An edge derived from
                    # co-measurement has strictly less authority than one an expert wrote.
                    "allow_upward_inference": False,
                    "allow_downward_blocking": False,
                    "metadata": {
                        "validation_status": "unvalidated",
                        "basis": "item_co_measurement",
                        "n_parent_failures": 0,
                        "p_pass_child_given_fail_parent": None,
                    },
                }
            )
        else:
            edges.append(
                {
                    "from": source,
                    "to": target,
                    "relation": CONTRIBUTES_TO,
                    "weight": round(weight, 6),
                }
            )

    prerequisites = sum(1 for e in edges if e["relation"] == PREREQUISITE)
    logger.info(
        "derived a graph for %s: %d mains, %d sub-competencies, %d edges (%d prerequisite)",
        bank_id,
        len(mains),
        len(subs),
        len(edges),
        prerequisites,
    )

    return {
        "version": "1.0",
        "bank_id": bank_id,
        "notes": (
            "Derived from the uploaded bank's items. Node membership comes from the id "
            "prefix, so there are no shared nodes; edge weights come from item "
            "co-measurement, and every prerequisite edge ships inert and unvalidated. "
            "See cat_engine/ingest/derive.py."
        ),
        "policy": {
            # OFF at the bank level, not only at the deployment level, so a deployment that
            # enables propagation globally does not enable it for a graph nobody authored.
            "upward_inference": False,
            "descendant_blocking": False,
            "accepted_validation_statuses": ["validated"],
            "minimum_failures_to_block": 2,
            "notes": (
                "Propagation is off for a derived graph. Its prerequisite edges come from "
                "co-measurement rather than from expertise, which is weaker evidence than "
                "the authored edges that were themselves measured wrong 22.4% of the time "
                "against a 3% gate."
            ),
        },
        "nodes": nodes,
        "edges": edges,
    }


def summarise(graph: dict[str, Any]) -> dict[str, Any]:
    """What the upload receipt says about the graph nobody uploaded.

    An author who never wrote a graph is entitled to see what one was invented on their
    behalf, and to see it BEFORE the bank measures anybody.
    """
    relations = Counter(e["relation"] for e in graph["edges"])
    return {
        "mains": sorted(
            n["competency_id"] for n in graph["nodes"] if n["node_type"] == "main"
        ),
        "sub_competencies": sorted(
            n["competency_id"] for n in graph["nodes"] if n["node_type"] != "main"
        ),
        "edges_by_relation": dict(relations),
        "prerequisite_edges_are_inert": all(
            not e.get("allow_upward_inference", False)
            and not e.get("allow_downward_blocking", False)
            for e in graph["edges"]
            if e["relation"] == PREREQUISITE
        ),
    }
