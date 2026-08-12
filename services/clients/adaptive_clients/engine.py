"""The two adapters that hand the engine a service instead of a local object.

THE ONLY MODULE HERE THAT IMPORTS THE ENGINE

`adaptive_clients` is otherwise engine-free, so a frontend's generated client can install it
without numpy, a sandbox client and 600 KB of question banks. These two classes cannot be:
they exist to satisfy `UnifiedBankRepository` and `GraderAgent`, which are engine types.
They are therefore NOT re-exported from the package root — import this module from a process
that has `adaptive-engine` installed, which is every service that would want them.

WHY THIS IS AN ADAPTER AND NOT A REWRITE

`UnifiedBankRepository` was already a `Protocol` and `GraderAgent.grade` was already one
method. So neither of these is a new abstraction over the engine; each is a second
implementation of a boundary the engine already had, which is why the orchestrator can be
pointed at services without one line of the selection loop changing.
"""

from __future__ import annotations

import logging
from typing import Any

from adaptive_contracts import CompetencyGraphDTO
from cat_engine.engine.schemas.orchestration import BankItem, GradedResponse
from cat_engine.engine.schemas.voice import GradedVoiceResponse, VoiceResponsePackage
from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService
from cat_engine.engine.services.competency_graph.models import (
    CompetencyEdge,
    CompetencyGraph,
    CompetencyNode,
)
from cat_engine.engine.services.competency_graph.policy import GraphPolicy
from cat_engine.engine.services.orchestrator.competency import main_competency

from .bank import BankRegistryClient
from .transport import BaseClient, ServiceUnavailable

logger = logging.getLogger(__name__)


def _sorted_main_codes(codes) -> list[str]:
    """C1..C10 in numeric order, not lexicographic. Matches `JsonUnifiedBank.variables`."""

    def key(code: str) -> tuple[int, int | str]:
        if code.startswith("C") and code[1:].isdigit():
            return (0, int(code[1:]))
        return (1, code)

    return sorted(set(codes), key=key)


class HttpUnifiedBank:
    """`UnifiedBankRepository`, served by bank-registry.

    IT CANNOT PRODUCE A QUESTION, AND THAT IS THE POINT.

    Every item this returns has an EMPTY payload. Selection ranks the whole eligible pool
    on every step and needs only a, b, c, the measured variables and the expected time — so
    the repository the orchestrator holds is wired to the parameters-only endpoint, and a
    stem is not something it could leak because it is not something it has.

    Rendering the presented item is a separate, explicit fetch (`payload_for`), and grading
    does not go through here at all: the grader fetches its own copy, which is how the
    answer key stays out of the orchestrator entirely.

    CACHED AGAINST THE BANK VERSION

    A fetch per decision would put a round trip inside a loop budgeted at 100 ms. The whole
    parameter set is read once and held against the version, which is what ADR-0001 meant
    by a read-through cache keyed by bank version — the version now exists to key it on.
    """

    def __init__(self, client: BankRegistryClient, bank_id: str) -> None:
        self._client = client
        self._bank_id = bank_id
        self._version = ""
        self._items: tuple[BankItem, ...] = ()
        self._by_id: dict[str, BankItem] = {}
        self._payloads: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def bank_id(self) -> str:
        return self._bank_id

    @property
    def version(self) -> str:
        self._load()
        return self._version

    def refresh(self) -> None:
        """Re-read the parameter set. Called at session start, never per question."""
        version, refs = self._client.items(self._bank_id)
        self._items = tuple(
            BankItem.model_validate(
                {
                    **ref.model_dump(),
                    # An empty payload rather than a missing one: `BankItem` requires the
                    # key matching its modality to exist, and an empty dict is the honest
                    # statement that this view has no payload — as opposed to a `None`,
                    # which would fail validation and imply the item is malformed.
                    ref.modality: {},
                }
            )
            for ref in refs
        )
        self._by_id = {item.item_id: item for item in self._items}
        self._version = version

    def _load(self) -> tuple[BankItem, ...]:
        if not self._items:
            self.refresh()
        return self._items

    # --- UnifiedBankRepository --------------------------------------------
    def all_items(self) -> list[BankItem]:
        return list(self._load())

    def get(self, item_id: str) -> BankItem | None:
        self._load()
        return self._by_id.get(item_id)

    def shortlist(self, variable: str, exclude: set[str]) -> list[BankItem]:
        """Every unserved active item measuring `variable`, ACROSS MODALITIES.

        Not filtered by modality, deliberately: the whole payoff of one theta scale is that
        an MCQ item and a code question compete on information for the same variable.
        """
        return [
            item
            for item in self._load()
            if item.status == "active"
            and item.item_id not in exclude
            and item.loading(variable) > 0
        ]

    def variables(self) -> list[str]:
        return _sorted_main_codes(
            main_competency(m.variable) for i in self._load() for m in i.measures
        )

    # --- rendering, which is a different authorisation --------------------
    def payload_for(self, item_id: str) -> BankItem | None:
        """The full item, payload included. For PRESENTING one question.

        Separate from `get` on purpose. This is the call that can read a stem, it is made
        once per presented item rather than once per ranked candidate, and a reader can see
        every place it happens by grepping for this name.
        """
        try:
            full = self._client.item(self._bank_id, item_id)
        except ServiceUnavailable:
            logger.exception("could not read the payload for %s", item_id)
            return None
        data = full.model_dump()
        payload = data.pop("payload", {}) or {}
        data[full.modality] = payload
        return BankItem.model_validate(data)


class HttpGrader(BaseClient):
    """`GraderAgent.grade`, served by the grader.

    Sends the bank id, the item id and the response — never the item. The grading payload
    holds the answer index, the hidden tests and the reference solution, and the grader
    fetches its own copy, so none of those is ever in a message this process handled.
    """

    def __init__(self, *args, bank_id: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._bank_id = bank_id

    def grade(self, item: BankItem, response: object) -> GradedResponse:
        if item.modality == "mcq":
            return self._post("/grade/mcq", item, {"chosen_index": int(response)})  # type: ignore[arg-type]
        if item.modality == "code":
            if not isinstance(response, str):
                raise TypeError(f"{item.item_id}: code response must be source text")
            return self._post("/grade/code", item, {"source": response})
        if item.modality in ("open", "voice"):
            return self._post("/grade/open", item, {"package": self._package(response)})
        raise NotImplementedError(f"no grader for modality {item.modality!r}")

    @staticmethod
    def _package(response: object) -> dict:
        """Accepts either the raw transcript package or an already-evaluated response.

        The in-process grader takes a `GradedVoiceResponse` because evaluation happened
        earlier, in a caller that no longer exists; the service takes the package and
        evaluates it itself, because evaluation is a model call and the grader is the
        service with egress. Accepting both means the orchestrator's answer path does not
        have to know which grader it is talking to.
        """
        if isinstance(response, GradedVoiceResponse):
            return response.package.model_dump()
        if isinstance(response, VoiceResponsePackage):
            return response.model_dump()
        raise TypeError(
            "open/voice response must be a VoiceResponsePackage or a GradedVoiceResponse, "
            f"got {type(response).__name__}"
        )

    def _post(self, path: str, item: BankItem, extra: dict) -> GradedResponse:
        body = self.post(
            path,
            json={"bank_id": self._bank_id, "item_id": item.item_id, **extra},
        ).json()
        return GradedResponse.model_validate(body)

    # --- the candidate's trial run ----------------------------------------
    def public_tests(self, item_id: str) -> list[dict]:
        return self.get(f"/trial/{self._bank_id}/{item_id}/public-tests").json()["tests"]

    def trial_run(self, item_id: str, source: str) -> dict:
        return self.post(
            "/trial/code",
            json={"bank_id": self._bank_id, "item_id": item_id, "source": source},
        ).json()

    def transcribe(self, item_id: str, audio: bytes, filename: str) -> str:
        import base64

        return self.post(
            "/transcribe",
            json={
                "item_id": item_id,
                "filename": filename,
                "audio_base64": base64.b64encode(audio).decode(),
            },
        ).json()["transcript"]


# --- the competency graph, as a structure two services both need ------------------------
#
# `to_graph` and `HttpGraphSource` are used by competency-graph (to propagate) and by
# assessment-orchestrator (to traverse, during selection). They live here rather than in
# either service because a service may not import another, and a second copy of a
# graph-rebuilding function is a second chance to lose an edge flag.
#
# THE GRAPH ARRIVES WITH ITS POLICY ALREADY APPLIED. `GET /banks/{id}/graph` serves the
# graph after `apply_policy`, so each edge's `allow_upward_inference` and
# `allow_downward_blocking` are the EFFECTIVE values — the three-level AND of deployment,
# bank and edge, already computed. Re-resolving here would be a second place that could
# answer "may this edge infer?" differently.

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


class HttpGraphSource:
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


# --- scoping ---------------------------------------------------------------
#
# A scope narrows an assessment to some of a bank's competencies. It is applied HERE, by
# wrapping the two seams the orchestrator already depends on, and not in the engine — which
# is the whole reason the feature costs no engine change at all.
#
#   ScopedBank   filters `shortlist`, so every candidate pool in `fill_queue` is already
#                narrowed and ranking, exposure control, the picker and the time filter all
#                need to know nothing.
#   ScopedGraph  hands the orchestrator the INDUCED sub-graph, so `sub_nodes_for_main`
#                enumerates only the retained nodes and the coverage requirement is correct
#                by construction at all four of its call sites, with no argument threaded
#                through any of them.
#
# WHAT MUST NOT BE SCOPED, AND WHY
#
# `get()` is NOT filtered. It resolves items the session has already served — for the
# modality blueprint, for corroboration, for the report. Filtering it would make an
# administered item vanish from the record of the session that administered it.
#
# Propagation is NOT scoped either; it keeps the full graph. A response that evidences an
# excluded node still recorded that evidence, and discarding it would be throwing away a
# real observation because of a bookkeeping boundary. It simply counts toward no
# requirement.


class ScopedBank:
    """A `UnifiedBankRepository` narrowed to an allowlist of item ids.

    Delegation rather than subclassing, so this works over `JsonUnifiedBank` and
    `HttpUnifiedBank` alike — which is what lets the in-process parity harness and the
    service path exercise the same scoping code.
    """

    def __init__(self, inner, item_ids: set[str], mains: set[str] | None = None) -> None:
        self._inner = inner
        self._item_ids = set(item_ids)
        #: The mains the scope opens. `variables()` is what `Orchestrator.begin` validates
        #: target variables against, so a main outside the scope has to be absent here or
        #: a caller could open one the allowlist cannot serve.
        self._mains = set(mains) if mains is not None else None

    def __getattr__(self, name: str):
        """Anything not narrowed is the underlying bank's, unchanged.

        `payload_for`, `coverage`, `parity_report`, `refresh`, `version` — none of them is
        a selection decision, and forwarding by default means a new method on the
        repository does not silently become unavailable inside a scope.
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
    nothing, but that is why the scope service always does.
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
