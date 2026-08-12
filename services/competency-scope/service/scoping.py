"""Turning a selection of competencies into a sub-graph and an item allowlist.

PURE, AND DELIBERATELY SO

Nothing here reads a setting, opens a connection or looks at a clock. It takes a graph, a
list of item references and a selection, and returns a manifest. That is what makes the
scope id a hash of its inputs rather than a row in a table, which in turn is what lets this
service hold no state and lose nothing on restart — the same argument `competency-graph`
makes for itself.

THE SUB-GRAPH IS STRICTLY INDUCED

Only the retained nodes, and only the edges with BOTH endpoints retained. A prerequisite
edge with one endpoint outside the scope is reported in `dangling_prerequisites` rather than
dropped in silence.

Strict is the right default HERE for a reason specific to this repository, not a general
preference: prerequisite edges ship inert because a screening study measured upward
inference wrong 22.4% of the time against a 3% gate. Following them to enlarge a scope would
be spending their authority on the one thing they were measured unfit for. A caller who
wants the ancestor closure asks for it, and then owns that choice.

WHY MAIN NODES ARE ALWAYS RETAINED

`Orchestrator._graph_is_compatible` compares the bank's mains against the graph's before it
will use a graph at all, and a mismatch makes it run the whole session with graph gating
off. A sub-graph that dropped its main nodes would silently disable the coverage gate — the
one part of the graph layer that is actually live.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from cat_engine.contracts import (
    BankItemRef,
    CompetencyGraphDTO,
    DanglingEdgeDTO,
    ScopeCoverageDTO,
    ScopeEdgeDTO,
    ScopeMainDTO,
    ScopeManifest,
    ScopeNodeDTO,
    ScopeRequest,
)

CONTRIBUTES_TO = "CONTRIBUTES_TO"
PREREQUISITE = "PREREQUISITE"

#: `bank_store.validate` refuses a bank whose main requires more sub-competencies than this
#: leaves room for, and a scope has to answer the same question the same way. Two questions
#: of headroom: a session that spends every single slot closing coverage has no slot left to
#: actually localise the estimate, so it converges on nothing.
BUDGET_HEADROOM = 2


def mains_of(node_id: str, declared: list[str]) -> list[str]:
    """The mains a sub-competency serves.

    Mirrors `coverage.sub_nodes_for_main`, including its fallback to the id prefix. Two
    implementations of "which main does this node belong to" would eventually disagree, and
    the symptom would be a scope requiring a node the coverage gate does not, so the
    fallback is copied rather than improved on.
    """
    if declared:
        return list(declared)
    return [node_id.split(".", 1)[0]] if "." in node_id else []


class _Graph:
    """Indexed once, so the passes below read rather than search."""

    def __init__(self, dto: CompetencyGraphDTO) -> None:
        self.nodes = {n.competency_id: n for n in dto.nodes}
        self.edges = list(dto.edges)
        self.subs_by_main: dict[str, list[str]] = defaultdict(list)
        self.mains: set[str] = set()

        for node_id, node in self.nodes.items():
            if node.node_type == "main":
                self.mains.add(node_id)
                continue
            for main in mains_of(node_id, node.main_competencies):
                self.subs_by_main[main].append(node_id)

        # A main a node claims but the graph never declares as a node. Real: the fallback
        # above invents `C1` from `C1.4` whether or not anybody wrote a `C1` node down.
        self.mains |= set(self.subs_by_main)

        #: main -> total authored CONTRIBUTES_TO mass, for `retained_weight`.
        self.contribution: dict[str, dict[str, float]] = defaultdict(dict)
        for edge in self.edges:
            if edge.relation == CONTRIBUTES_TO:
                self.contribution[edge.to_id][edge.from_id] = float(edge.weight)

    def is_main(self, node_id: str) -> bool:
        node = self.nodes.get(node_id)
        if node is not None:
            return node.node_type == "main"
        return node_id in self.mains

    def prerequisite_parents(self, node_id: str) -> list[str]:
        """Nodes that are prerequisites OF `node_id` — `from` is the prerequisite."""
        return [e.from_id for e in self.edges if e.relation == PREREQUISITE and e.to_id == node_id]


def scope_id_for(request: ScopeRequest, bank_version: str) -> tuple[str, str]:
    """A deterministic identity for (this bank version, this selection).

    Over the NORMALISED request, so `["C6", "C1.1"]` and `["C1.1", "C6"]` are one scope
    rather than two. Includes the bank version because a scope names item ids, and the same
    selection over a replaced bank is a different set of items — pinning the version is what
    lets a session refuse a manifest that has gone stale instead of applying an allowlist
    full of ids that no longer exist.
    """
    digest = hashlib.sha256(
        json.dumps(
            {
                "bank_id": request.bank_id,
                "bank_version": bank_version,
                "selected": sorted(set(request.selected)),
                "critical_only": request.critical_only,
                "include_prerequisites": request.include_prerequisites,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"scp_{digest[:16]}", digest[:16]


def build_scope(
    *,
    request: ScopeRequest,
    bank_version: str,
    graph: CompetencyGraphDTO,
    items: list[BankItemRef],
    critical_only: bool,
    question_budget: int,
) -> ScopeManifest:
    """One selection becomes one manifest. See the module docstring for the invariants."""
    index = _Graph(graph)
    scope_id, scope_hash = scope_id_for(request, bank_version)

    # --- 1. resolve the selection ------------------------------------------
    rejected: list[str] = []
    retained_subs: set[str] = set()
    selected_mains: set[str] = set()
    #: Sub-competencies the caller LITERALLY named. Not the ones a selected main expanded
    #: into, and not the ones a shared node dragged in — those are exactly what has to be
    #: excluded for `implied` to mean anything.
    named_subs: set[str] = set()

    for choice in sorted(set(request.selected)):
        if index.is_main(choice):
            children = index.subs_by_main.get(choice, [])
            if not children:
                # A main with no sub-competencies measures nothing the gate can require.
                # Not an error — some banks are one flat competency — but it cannot
                # contribute nodes, so it is only usable if items measure the main itself.
                selected_mains.add(choice)
                continue
            selected_mains.add(choice)
            retained_subs.update(children)
        elif choice in index.nodes:
            retained_subs.add(choice)
            named_subs.add(choice)
        else:
            rejected.append(choice)

    # --- 2. optional prerequisite closure ----------------------------------
    if request.include_prerequisites:
        frontier = list(retained_subs)
        while frontier:
            node_id = frontier.pop()
            for parent in index.prerequisite_parents(node_id):
                if parent not in retained_subs and not index.is_main(parent):
                    retained_subs.add(parent)
                    frontier.append(parent)

    # Every main any retained sub serves is opened, even if it was not named: an item
    # measuring C1.6 evidences both C1 and C6, and a session that opened only one of them
    # would discard half of what it just measured.
    mains: set[str] = set(selected_mains)
    for node_id in retained_subs:
        node = index.nodes.get(node_id)
        declared = node.main_competencies if node else []
        mains.update(mains_of(node_id, declared))

    retained = retained_subs | mains

    # --- 3. induce the edges ------------------------------------------------
    kept_edges: list[ScopeEdgeDTO] = []
    dangling: list[DanglingEdgeDTO] = []
    for edge in index.edges:
        tail, head = edge.from_id in retained, edge.to_id in retained
        if tail and head:
            kept_edges.append(
                ScopeEdgeDTO(
                    from_id=edge.from_id,
                    to_id=edge.to_id,
                    relation=edge.relation,
                    strength=float(edge.strength),
                    weight=float(edge.weight),
                    weight_original=float(edge.weight),
                )
            )
        elif edge.relation == PREREQUISITE and (tail or head):
            dangling.append(
                DanglingEdgeDTO(
                    from_id=edge.from_id,
                    to_id=edge.to_id,
                    relation=edge.relation,
                    direction="leaves" if tail else "enters",
                )
            )

    # --- 4. renormalise CONTRIBUTES_TO over what survived -------------------
    # The retained children of a main no longer sum to 1.0, so the sub-graph is internally
    # inconsistent as authored. Renormalising fixes that; `weight_original` is kept beside
    # it because the change alters what a main's declared mass MEANS, and a change nobody
    # can see is a change nobody can review. Nothing in the engine reads either number
    # today — see `graph.py`, which indexes only PREREQUISITE edges.
    retained_mass: dict[str, float] = defaultdict(float)
    for edge in kept_edges:
        if edge.relation == CONTRIBUTES_TO:
            retained_mass[edge.to_id] += edge.weight_original
    for edge in kept_edges:
        if edge.relation == CONTRIBUTES_TO and retained_mass[edge.to_id] > 0:
            edge.weight = round(edge.weight_original / retained_mass[edge.to_id], 6)

    # --- 5. the item allowlist ----------------------------------------------
    item_ids: list[str] = []
    by_modality: dict[str, int] = defaultdict(int)
    per_node: dict[str, int] = defaultdict(int)
    mains_with_items: set[str] = set()
    for item in items:
        if item.status != "active":
            continue
        touched = {m.variable for m in item.measures} & retained
        if not touched:
            continue
        item_ids.append(item.item_id)
        by_modality[item.modality] += 1
        for node_id in touched:
            per_node[node_id] += 1
            if index.is_main(node_id):
                mains_with_items.add(node_id)
                continue
            node = index.nodes.get(node_id)
            mains_with_items.update(
                mains_of(node_id, node.main_competencies if node else [])
            )

    # A main no allowlisted item can measure cannot be assessed. `Orchestrator.begin` would
    # refuse it as "no bank coverage"; doing it here instead tells the caller WHICH of the
    # competencies they chose is the problem, before a candidate is involved. Dropped
    # before the rows below are built, so nothing downstream reports on a main that is not
    # in the scope.
    unmeasurable = sorted(main for main in mains if main not in mains_with_items)
    if unmeasurable:
        rejected.extend(unmeasurable)
        mains -= set(unmeasurable)
        retained -= set(unmeasurable)

    # --- 6. the mains, and how much of each survived ------------------------
    main_rows: list[ScopeMainDTO] = []
    required_by_main: dict[str, int] = {}
    unserved: list[str] = []
    over_budget: list[str] = []

    for main in sorted(mains):
        all_subs = set(index.subs_by_main.get(main, []))
        kept = sorted(all_subs & retained_subs)
        excluded = sorted(all_subs - retained_subs)

        # The coverage gate's own question, asked the way the engine asks it.
        required = [
            node_id
            for node_id in kept
            if not critical_only or (index.nodes[node_id].critical if node_id in index.nodes else False)
        ]
        required_by_main[main] = len(required)
        unserved.extend(node_id for node_id in required if per_node.get(node_id, 0) == 0)
        if len(required) > question_budget - BUDGET_HEADROOM:
            over_budget.append(main)

        authored = index.contribution.get(main, {})
        total = sum(authored.values())
        if total > 0:
            retained_weight = sum(authored.get(n, 0.0) for n in kept) / total
        elif all_subs:
            # No CONTRIBUTES_TO edges authored at all — a coverage-only graph. Fall back to
            # the share of nodes, which is the same statement with less precision behind it.
            retained_weight = len(kept) / len(all_subs)
        else:
            retained_weight = 1.0

        node = index.nodes.get(main)
        main_rows.append(
            ScopeMainDTO(
                main=main,
                title=node.title if node else main,
                implied=main not in selected_mains and not (all_subs & named_subs),
                partial=bool(excluded),
                retained_weight=round(retained_weight, 6),
                required=required,
                excluded=excluded,
            )
        )

    nodes = [
        ScopeNodeDTO(
            node_id=node_id,
            title=index.nodes[node_id].title if node_id in index.nodes else node_id,
            node_type="main" if index.is_main(node_id) else "sub_competency",
            critical=index.nodes[node_id].critical if node_id in index.nodes else False,
            main_competencies=mains_of(
                node_id,
                index.nodes[node_id].main_competencies if node_id in index.nodes else [],
            ),
            item_count=per_node.get(node_id, 0),
        )
        for node_id in sorted(retained)
    ]

    coverage = ScopeCoverageDTO(
        reachable=not unserved and not over_budget and bool(item_ids),
        question_budget=question_budget,
        critical_only_applied=critical_only,
        required_by_main=required_by_main,
        unserved=sorted(set(unserved)),
        over_budget=sorted(set(over_budget)),
    )

    return ScopeManifest(
        scope_id=scope_id,
        scope_hash=scope_hash,
        bank_id=request.bank_id,
        bank_version=bank_version,
        selected=sorted(set(request.selected)),
        include_prerequisites=request.include_prerequisites,
        nodes=nodes,
        edges=kept_edges,
        mains=main_rows,
        item_ids=sorted(item_ids),
        item_count_by_modality=dict(by_modality),
        coverage=coverage,
        rejected=sorted(set(rejected)),
        dangling_prerequisites=dangling,
    )
