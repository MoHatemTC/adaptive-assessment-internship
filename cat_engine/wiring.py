"""Building an `Orchestrator` out of in-process parts.

WHAT THIS FILE IS EVIDENCE OF

Nothing in the engine's loop changed to make the services possible, and nothing changed to
undo them. `UnifiedBankRepository` was already a Protocol, `GraderAgent.grade` was already
one method, and propagation was already one call — so pointing the orchestrator at local
objects instead of HTTP clients is a wiring change, visible in one function. The four
`Http*` adapters this replaces did exactly the same job through a socket.

ONE ORCHESTRATOR PER (BANK, VERSION, SCOPE)

Not per bank. Replacing a bank produces a new version, and a session already running must
keep the pool it began with — items disappearing from a queue that already ranked them is
not a change any candidate consented to. Keying on the version means a live session keeps
its orchestrator, a new one gets the new bank, and neither needs to know the other exists.

The scope hash is part of the key because two scopes over one bank version are two
different item pools and two different coverage requirements; sharing an orchestrator
between them would give one candidate the other's allowlist.

THE GRAPH IS READ LOCALLY, WHICH IT ALWAYS WAS

Selection reads the graph on every candidate on every step, which is why even the split
kept traversal local and sent only propagation over a wire. Now both are local, and
`InProcessPropagation` is the same class the evaluation harness has always used.
"""

from __future__ import annotations

import logging

from cat_engine.contracts import ScopeManifest
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
from cat_engine.engine.services.orchestrator.propagation_port import (
    InProcessPropagation,
)
from cat_engine.grading import Grader
from cat_engine.scope.bank import ScopedBank, scoped_graph_service

logger = logging.getLogger(__name__)

__all__ = ["Wiring"]


class Wiring:
    """The orchestrators for one module instance, and the grader they share.

    An instance rather than module globals: the state here is a cache keyed on bank
    versions, and a host that builds a second module — for a different bank store, say —
    should not silently inherit the first one's parsed banks.
    """

    def __init__(self, grader: Grader | None = None) -> None:
        self._grader = grader if grader is not None else Grader()
        #: (bank_id, version, scope_hash) -> Orchestrator. Bounded by how many bank
        #: versions are in flight times how many distinct scopes run against them.
        self._orchestrators: dict[tuple[str, str, str], Orchestrator] = {}

    @property
    def grader(self) -> Grader:
        return self._grader

    def current_version(self, bank_id: str) -> str:
        return registry.version(bank_id)

    def orchestrator_for(
        self, bank_id: str, version: str, scope: ScopeManifest | None = None
    ) -> Orchestrator:
        """The orchestrator for one bank AT ONE VERSION, optionally narrowed to a scope.

        THE SCOPE IS APPLIED HERE, AND NOWHERE IN THE ENGINE.

        Two decorators over seams the orchestrator already depended on — see
        `scope/bank.py` for which, and for what deliberately is NOT narrowed.

        PROPAGATION KEEPS THE FULL GRAPH. A response that evidences a node outside the
        scope still made that observation; discarding it would be throwing away real
        evidence over a bookkeeping boundary. It simply counts toward no coverage
        requirement.
        """
        key = (bank_id, version, scope.scope_hash if scope else "")
        cached = self._orchestrators.get(key)
        if cached is not None:
            return cached

        bank: object = registry.get_bank(bank_id)
        if scope is not None:
            bank = ScopedBank(
                bank,
                item_ids=set(scope.item_ids),
                mains={row.main for row in scope.mains},
            )

        propagation = InProcessPropagation(
            lambda: registry.get_graph_service(bank_id),
            bank_id=bank_id,
            minimum_failures_provider=lambda: self._minimum_failures_to_block(bank_id),
        )

        graph = registry.get_graph_service(bank_id)
        if scope is not None:
            graph = scoped_graph_service(graph, {node.node_id for node in scope.nodes})

        coverage_critical_only = registry.coverage_policy(bank_id)
        if scope is not None:
            # The scope already resolved this — against the same bank policy, and possibly
            # overridden by the caller. Re-reading the bank here would let a scope built
            # under `critical_only=false` be gated under the bank's `true`, so the session
            # would require fewer nodes than the manifest promised and the two would
            # disagree about what was measured.
            coverage_critical_only = scope.coverage.critical_only_applied

        built = Orchestrator(
            bank,
            self._grader.agent,
            graph=graph,
            coverage_critical_only=coverage_critical_only,
            bank_id=bank_id,
            propagation=propagation,
        )
        self._orchestrators[key] = built
        return built

    def bank_for(
        self, bank_id: str, version: str, scope: ScopeManifest | None = None
    ):
        """The repository behind one orchestrator, for the payload fetch a presentation needs.

        Reached through the orchestrator's own bank rather than a fresh handle, so the item
        being rendered is the one that was ranked — same version, same parsed copy. The
        scope has to travel for that to hold: without it this would build a SECOND,
        unscoped orchestrator and render out of its bank instead.
        """
        return self.orchestrator_for(bank_id, version, scope)._bank

    @staticmethod
    def _minimum_failures_to_block(bank_id: str) -> int | None:
        """The bank's own blocking threshold, after the deployment floor is applied.

        Read from the resolved policy rather than the graph file, so a bank can only ever
        tighten it. None when the bank declares no graph and therefore no policy.
        """
        resolved = registry.get_propagation_policy(bank_id)
        if resolved is None:
            return None
        value = resolved.summary().get("minimum_failures_to_block")
        return int(value) if value is not None else None

    def reset(self) -> None:
        """Drop every cached orchestrator. For tests, and after a bank store rebuild."""
        self._orchestrators.clear()
