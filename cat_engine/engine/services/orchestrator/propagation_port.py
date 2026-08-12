"""The one call the orchestrator makes into the competency graph, behind a seam.

WHY THIS IS THE ONLY THING THAT MOVED

The graph is used in two quite different ways, and only one of them can afford a network
hop.

    TRAVERSAL — which sub-competencies does this main require, which nodes are blocked,
    what would unblock one. Read during SELECTION, on every candidate, on every step,
    inside a 100 ms budget. Stays local against a cached graph structure.

    PROPAGATION — one response, one transaction, node state in and a delta out. Happens
    once per answered question, next to a grading call that already costs 500 ms to 30 s.
    That one can be a service.

So the port is `apply()` and `manifest()`, and nothing else. Splitting traversal out too
would have put the network inside the ranking loop for no isolation — the graph structure
is immutable for a bank version, so a remote read of it is a cache fill, not a boundary.

WHAT COMES BACK CANNOT BE MISTAKEN FOR EVIDENCE

`PropagationResult` carries a state update and a set of mains whose ELIGIBILITY changed. It
carries no score and no weight, and there is no path from it into a posterior. That is the
invariant the whole decomposition rests on: a deduction FROM a response is not a second
response, and multiplying it back in counts one answer twice — with the damage landing on
the standard error, which is what the assessment stops on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from cat_engine.engine.schemas.orchestration import AssessmentState, BankItem, GradedResponse
from cat_engine.engine.services.competency_graph import config as graph_config
from cat_engine.engine.services.competency_graph import manifest as graph_manifest
from cat_engine.engine.services.competency_graph.evidence import (
    DEFAULT_CONFIDENCE,
    EvidenceEvent,
    evidence_id_for,
)
from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService
from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
from cat_engine.engine.services.competency_graph.propagation import apply_direct_evidence
from cat_engine.engine.services.competency_graph.state import CompetencyGraphState
from cat_engine.engine.services.orchestrator.graph_delta import GraphDelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PropagationResult:
    """What one response did to the graph, in the shape the session persists it.

    `state_update` is applied verbatim to `AssessmentState`. It is a flat dict rather than
    a typed model because it IS the projection the session already stores, field for field
    — typing it here would create a second definition of a shape the engine's own schema
    owns, and the two would drift.
    """

    state_update: dict = field(default_factory=dict)
    #: Mains whose ELIGIBILITY changed. A blocked or reopened node makes a queued pick
    #: stale without any estimate having moved, so those slots must be refilled.
    selection_affected_mains: set[str] = field(default_factory=set)
    #: The propagation configuration this call ran under, and its hash. Reported by
    #: whoever OWNS propagation rather than recomputed by the caller — split across
    #: services, two answers to "what configuration is in force" is exactly the failure the
    #: manifest exists to detect.
    manifest: dict = field(default_factory=dict)
    manifest_hash: str = ""
    #: False when the graph was off, unusable, or the batch failed. The caller keeps its
    #: prior state; a graph problem must cost the candidate nothing.
    applied: bool = True


@runtime_checkable
class PropagationPort(Protocol):
    """Apply one response's outcomes to the competency graph."""

    def manifest(self) -> tuple[dict, str]:
        """The propagation configuration in force, and its hash. `({}, "")` when off."""
        ...

    def apply(
        self, state: AssessmentState, item: BankItem, graded: GradedResponse
    ) -> PropagationResult:
        """Fold one response in, all or nothing."""
        ...


def build_events(
    *,
    state: AssessmentState,
    item: BankItem,
    graded: GradedResponse,
    attempt_no: int,
) -> list[EvidenceEvent]:
    """One evidence event per graded outcome, with a deterministic id.

    Deterministic because the id is the idempotency key: a replayed response has to be
    recognised as the one already absorbed rather than counted twice.

    Two outcomes for one variable are REFUSED rather than merged. They would collide on
    that id and the second would be silently dropped as a duplicate — the grader is
    expected to aggregate per competency before this point, and a quiet drop would make a
    grader bug look like a propagation one.
    """
    seen: set[str] = set()
    events: list[EvidenceEvent] = []
    for outcome in graded.outcomes:
        target_node = str(outcome.get("variable") or "")
        if not target_node:
            continue
        if target_node in seen:
            raise ValueError(
                f"{item.item_id}: two outcomes for {target_node!r} in one response"
            )
        seen.add(target_node)
        modality = str(outcome.get("modality") or item.modality)
        events.append(
            EvidenceEvent(
                evidence_id=evidence_id_for(
                    session_id=state.session_id,
                    item_id=item.item_id,
                    attempt_no=attempt_no,
                    modality=modality,
                    target_node=target_node,
                ),
                session_id=state.session_id,
                item_id=item.item_id,
                modality=modality,
                target_node=target_node,
                score=float(outcome.get("score", 0.0)),
                weight=float(outcome.get("weight", 1.0)),
                confidence=float(outcome.get("confidence", DEFAULT_CONFIDENCE)),
                source=str(outcome.get("source_item_id") or item.item_id),
                evidence_kind="direct",
                directly_tested=True,
            )
        )
    return events


def propagate(
    *,
    graph: CompetencyGraphService,
    state: AssessmentState,
    item: BankItem,
    graded: GradedResponse,
    attempt_no: int,
    minimum_failures_to_block: int | None,
    session_variables: set[str],
    prior: GraphDelta | None = None,
) -> PropagationResult:
    """The transaction. Compute everything, then merge — or merge nothing.

    A response can produce several evidence events. Applying them one at a time straight
    into session state means a failure partway through leaves the session in a state no
    sequence of responses could have produced — and, because the evidence id was marked
    processed first, one that cannot be retried.
    """
    delta = prior if prior is not None else GraphDelta.restore(state)
    config = graph_config.propagation_config_from_settings(
        minimum_failures_to_block=minimum_failures_to_block
    )
    now = datetime.now(timezone.utc).isoformat()

    events = build_events(state=state, item=item, graded=graded, attempt_no=attempt_no)

    try:
        graph_state = CompetencyGraphState.from_dict(state.graph_node_states)
        graph_state.ensure_nodes(set(graph.graph.nodes))
        ledger = EvidenceLedger.from_ids(state.graph_processed_evidence_ids)

        results = [
            apply_direct_evidence(
                graph,
                graph_state,
                event,
                ledger=ledger,
                config=config,
                now=now,
                item_minimum_confidence=item.minimum_success_confidence,
            )
            for event in events
            if not ledger.contains(event.evidence_id)
        ]
    except Exception:
        logger.exception("graph evidence discarded for %s", item.item_id)
        return PropagationResult(
            state_update=delta.as_state_update(),
            selection_affected_mains=set(delta.selection_affected_mains),
            applied=False,
        )

    delta.absorb(
        graph=graph,
        graph_state=graph_state,
        ledger=ledger,
        results=results,
        session_variables=session_variables,
    )
    return PropagationResult(
        state_update=delta.as_state_update(),
        selection_affected_mains=set(delta.selection_affected_mains),
    )


def build_manifest(
    *, bank_id: str | None, minimum_failures_to_block: int | None, resolved
) -> tuple[dict, str]:
    """The propagation configuration in force, and its hash.

    Never raises. A session that cannot describe its configuration is worse than one that
    cannot record it, but only slightly, and refusing to start is worse than both.
    """
    if not graph_config.graph_enabled():
        return {}, ""
    try:
        manifest = graph_manifest.propagation_manifest(
            graph_config.propagation_config_from_settings(
                minimum_failures_to_block=minimum_failures_to_block
            ),
            resolved,
            bank_id=bank_id or "",
        )
    except ValueError:
        logger.exception("propagation configuration is invalid — recording no manifest")
        return {}, ""
    return manifest, graph_manifest.manifest_hash(manifest)


class InProcessPropagation:
    """The default. Applies evidence in this process, against this process's graph.

    What every current deployment, the evaluation harness and the whole engine suite use.
    The HTTP implementation in `adaptive_clients` is the same call over a wire, and the
    parity suite asserts the two produce identical sessions.
    """

    def __init__(
        self,
        graph_provider: Callable[[], CompetencyGraphService | None],
        *,
        bank_id: str | None = None,
        minimum_failures_provider: Callable[[], int | None] | None = None,
    ) -> None:
        self._graph_provider = graph_provider
        self._bank_id = bank_id
        self._minimum_failures_provider = minimum_failures_provider

    def _minimum_failures_to_block(self) -> int | None:
        if self._minimum_failures_provider is not None:
            return self._minimum_failures_provider()
        return None

    def _resolved_policy(self):
        try:
            from cat_engine.engine.services.orchestrator.registry import get_propagation_policy

            return get_propagation_policy(self._bank_id)
        except (ImportError, KeyError, OSError, ValueError):
            return None

    def manifest(self) -> tuple[dict, str]:
        return build_manifest(
            bank_id=self._bank_id,
            minimum_failures_to_block=self._minimum_failures_to_block(),
            resolved=self._resolved_policy(),
        )

    def apply(
        self, state: AssessmentState, item: BankItem, graded: GradedResponse
    ) -> PropagationResult:
        delta = GraphDelta.restore(state)
        graph = self._graph_provider() if graph_config.graph_enabled() else None
        if graph is None:
            return PropagationResult(
                state_update={},
                selection_affected_mains=set(),
                applied=False,
            )
        return propagate(
            graph=graph,
            state=state,
            item=item,
            graded=graded,
            # Distinguishes re-administrations of one item. Computed before `served` grows.
            attempt_no=state.served_item_ids.count(item.item_id) + 1,
            minimum_failures_to_block=self._minimum_failures_to_block(),
            session_variables=set(state.variables),
            prior=delta,
        )
