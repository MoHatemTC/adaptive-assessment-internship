"""Narrowing a bank and a graph to a scope.

WHY THIS IS NOT IN `engine/`

`Orchestrator` takes a `UnifiedBankRepository` and a graph service, and both were already
interfaces — that is what let the monolith become services and what lets the services
become this module. A scope is therefore two decorators over those seams rather than a
concept the engine has to learn:

    ScopedBank           filters `shortlist`, which is where every candidate pool in
                         `fill_queue` comes from. Ranking, exposure control, the picker and
                         the time filter are all downstream of it and unchanged.
    scoped_graph_service hands over the INDUCED sub-graph, so `sub_nodes_for_main`
                         enumerates only retained nodes and the coverage requirement is
                         correct by construction at all four of its call sites, with no
                         argument threaded through any of them.

WHAT MUST NOT BE SCOPED, AND WHY

`get()` is NOT filtered. It resolves items the session has already served — for the
modality blueprint, for corroboration, for the report. Filtering it would make an
administered item vanish from the record of the session that administered it.

Propagation is NOT scoped either; it keeps the full graph. A response that evidences an
excluded node still recorded that evidence, and discarding it would be throwing away a real
observation because of a bookkeeping boundary. It simply counts toward no requirement.
"""

from __future__ import annotations

from cat_engine.engine.schemas.orchestration import BankItem
from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService
from cat_engine.engine.services.competency_graph.models import CompetencyGraph

__all__ = ["ScopedBank", "induced_graph", "scoped_graph_service"]


class ScopedBank:
    """A `UnifiedBankRepository` narrowed to an allowlist of item ids.

    Delegation rather than subclassing, so it works over any repository satisfying the
    protocol — which is what let the same scoping code run in-process and behind HTTP while
    both existed, and what keeps it independent of how banks are stored now.
    """

    def __init__(self, inner, item_ids: set[str], mains: set[str] | None = None) -> None:
        self._inner = inner
        self._item_ids = set(item_ids)
        #: The mains the scope opens. `variables()` is what `Orchestrator.begin` validates
        #: target variables against, so a main outside the scope has to be absent here or a
        #: caller could open one the allowlist cannot serve.
        self._mains = set(mains) if mains is not None else None

    def __getattr__(self, name: str):
        """Anything not narrowed is the underlying bank's, unchanged.

        `payload_for`, `coverage`, `parity_report`, `refresh`, `version` — none of them is a
        selection decision, and forwarding by default means a new method on the repository
        does not silently become unavailable inside a scope.
        """
        return getattr(self._inner, name)

    def all_items(self) -> list[BankItem]:
        return [i for i in self._inner.all_items() if i.item_id in self._item_ids]

    def get(self, item_id: str) -> BankItem | None:
        # Unfiltered, deliberately — see the note above.
        return self._inner.get(item_id)

    def shortlist(self, variable: str, exclude: set[str]) -> list[BankItem]:
        return [
            item
            for item in self._inner.shortlist(variable, exclude)
            if item.item_id in self._item_ids
        ]

    def variables(self) -> list[str]:
        available = self._inner.variables()
        if self._mains is None:
            return available
        return [v for v in available if v in self._mains]


def induced_graph(graph: CompetencyGraph, retained: set[str]) -> CompetencyGraph:
    """The strictly induced sub-graph: retained nodes, and edges between two of them.

    MAIN NODES MUST SURVIVE. `Orchestrator._graph_is_compatible` compares the bank's mains
    against the graph's and, on a mismatch, runs the whole session with graph gating OFF
    rather than vetoing every convergence. A sub-graph that dropped its main nodes would
    therefore silently disable the coverage gate — the one part of the graph layer that is
    live today. The caller is responsible for including them in `retained`; this asserts
    nothing, but that is why the scope builder always does.
    """
    nodes = {nid: node for nid, node in graph.nodes.items() if nid in retained}
    edges = tuple(
        edge
        for edge in graph.edges
        if edge.from_id in retained and edge.to_id in retained
    )
    return CompetencyGraph(
        schema_version=graph.schema_version,
        nodes=nodes,
        edges=edges,
        policy=graph.policy,
    )


def scoped_graph_service(
    inner: CompetencyGraphService | None, retained: set[str]
) -> CompetencyGraphService | None:
    """A graph service over the induced sub-graph, or None when there was no graph."""
    if inner is None:
        return None
    return CompetencyGraphService(induced_graph(inner.graph, retained))
