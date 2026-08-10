"""Rebuilding a competency graph from what the registry serves.

WHY THIS SERVICE DOES NOT READ THE GRAPH FILES ITSELF

It could — the engine is installed here and the data directory could be mounted. But then
two services would each have their own view of which edges a bank has, and the failure when
they disagree is a permanent, silent convergence veto: the coverage gate asks a graph that
was authored for a different version which sub-competencies a main requires, marks every one
of them unmeasured, and refuses to certify anything. `bank-registry` owns graphs; this
service asks it.

THE GRAPH ARRIVES WITH ITS POLICY ALREADY APPLIED

`GET /banks/{id}/graph` serves the graph after `apply_policy`, so each edge's
`allow_upward_inference` and `allow_downward_blocking` are the EFFECTIVE values — the
three-level AND of deployment, bank and edge, already computed. Re-resolving here would be
a second place that could answer "may this edge infer?" differently.
"""

from __future__ import annotations

import logging

from adaptive_clients import BankRegistryClient
from adaptive_contracts import CompetencyGraphDTO
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.models import (
    CompetencyEdge,
    CompetencyGraph,
    CompetencyNode,
)
from app.services.competency_graph.policy import GraphPolicy

logger = logging.getLogger(__name__)


def to_graph(dto: CompetencyGraphDTO) -> CompetencyGraph:
    """The wire graph into the engine's own dataclasses, losslessly.

    `metadata` is carried on both nodes and edges because `validation_status` lives there,
    and it is what decides whether an edge may ever infer or block. A conversion that
    dropped it would hand back a graph whose edges had lost their authority to be inert.
    """
    return CompetencyGraph(
        schema_version=dto.schema_version,
        nodes={
            node.competency_id: CompetencyNode(
                competency_id=node.competency_id,
                title=node.title,
                node_type=node.node_type,
                critical=node.critical,
                context_specific=node.context_specific,
                main_competencies=tuple(node.main_competencies),
                metadata=dict(node.metadata),
            )
            for node in dto.nodes
        },
        edges=tuple(
            CompetencyEdge(
                from_id=edge.from_id,
                to_id=edge.to_id,
                relation=edge.relation,  # type: ignore[arg-type]
                strength=edge.strength,
                weight=edge.weight,
                allow_upward_inference=edge.allow_upward_inference,
                allow_downward_blocking=edge.allow_downward_blocking,
                metadata=dict(edge.metadata),
            )
            for edge in dto.edges
        ),
        policy=GraphPolicy.from_dict(dto.policy),
    )


class GraphSource:
    """Graphs by bank, cached against the bank version.

    Keyed on the version rather than time-boxed. A graph that has not changed can be held
    forever — it is immutable for a version — and one that has must not be served from
    cache at all. `PUT /banks/{id}` moves the version, so the next request misses and
    everything after it is correct, with no TTL to tune.
    """

    def __init__(self, client: BankRegistryClient) -> None:
        self._client = client
        self._graphs: dict[str, tuple[str, CompetencyGraphService | None]] = {}
        self._policies: dict[str, tuple[str, int | None]] = {}

    def _version(self, bank_id: str) -> str:
        return self._client.bank(bank_id).version

    def service(self, bank_id: str) -> CompetencyGraphService | None:
        version = self._version(bank_id)
        cached = self._graphs.get(bank_id)
        if cached is not None and cached[0] == version:
            return cached[1]
        dto = self._client.graph(bank_id)
        built = CompetencyGraphService(to_graph(dto)) if dto is not None else None
        self._graphs[bank_id] = (version, built)
        return built

    def minimum_failures_to_block(self, bank_id: str) -> int | None:
        """The bank's own blocking threshold, after the deployment floor is applied.

        Read from the resolved policy rather than the graph file, so a bank can only ever
        tighten it.
        """
        version = self._version(bank_id)
        cached = self._policies.get(bank_id)
        if cached is not None and cached[0] == version:
            return cached[1]
        try:
            resolved = self._client.policy(bank_id).get("minimum_failures_to_block")
        except Exception:  # noqa: BLE001 - a bank with no graph has no policy
            resolved = None
        value = int(resolved) if resolved is not None else None
        self._policies[bank_id] = (version, value)
        return value
