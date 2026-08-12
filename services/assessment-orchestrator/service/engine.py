"""Building an `Orchestrator` out of three service URLs.

WHAT THIS FILE IS EVIDENCE OF

Nothing in the engine's loop changed to make this possible. `UnifiedBankRepository` was
already a Protocol, `GraderAgent.grade` was already one method, and propagation was already
one call — so pointing the orchestrator at services is a wiring change, and that is the
whole claim of the decomposition, visible in one function.

ONE ORCHESTRATOR PER (BANK, VERSION)

Not per bank. A bank replaced through `PUT /banks/{id}` produces a new version, and a
session already running must keep the pool it began with — items disappearing from a queue
that already ranked them is not a change any candidate consented to. Keying the cache on
the version means a live session keeps its orchestrator, a new one gets the new bank, and
neither needs to know the other exists.

THE GRAPH IS FETCHED, THE TRAVERSAL IS LOCAL

Selection reads the graph on every candidate on every step. That cannot be a network call,
so the structure is fetched once per bank version and traversed here. Only PROPAGATION —
once per answered question, beside a grading call that already costs 500 ms to 30 s — goes
to the graph service.
"""

from __future__ import annotations

import logging

from adaptive_clients import (
    BankRegistryClient,
    CompetencyGraphClient,
    CompetencyScopeClient,
)
from adaptive_clients.engine import (
    HttpGrader,
    HttpGraphSource,
    HttpUnifiedBank,
    ScopedBank,
    scoped_graph_service,
)
from adaptive_contracts import ScopeManifest
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
from cat_engine.engine.services.orchestrator.propagation_port import InProcessPropagation

from .config import settings

logger = logging.getLogger(__name__)

_bank_client = BankRegistryClient(
    settings.bank_registry_url, timeout=settings.bank_timeout_seconds
)
_graph_source = HttpGraphSource(_bank_client)
_scope_client = (
    CompetencyScopeClient(
        settings.competency_scope_url, timeout=settings.scope_timeout_seconds
    )
    if settings.competency_scope_url.strip()
    else None
)

#: (bank_id, version, scope_hash) -> Orchestrator. Bounded by how many bank versions are in
#: flight, times how many distinct scopes are running against them. The scope hash is part
#: of the key because two scopes over one bank version are two different item pools and two
#: different coverage requirements — sharing an orchestrator between them would give one
#: candidate the other's allowlist.
_orchestrators: dict[tuple[str, str, str], Orchestrator] = {}


def bank_client() -> BankRegistryClient:
    return _bank_client


def scope_client() -> CompetencyScopeClient | None:
    """None when `COMPETENCY_SCOPE_URL` is unset — scoped assessments are then refused.

    Refused rather than silently widened to the whole bank: "you asked for three
    competencies and were assessed on eleven" is not something a candidate or a report
    could detect afterwards.
    """
    return _scope_client


def current_version(bank_id: str) -> str:
    return _bank_client.bank(bank_id).version


def propagation_is_remote() -> bool:
    return bool(settings.competency_graph_url.strip())


def orchestrator_for(
    bank_id: str, version: str, scope: ScopeManifest | None = None
) -> Orchestrator:
    """The orchestrator for one bank AT ONE VERSION, optionally narrowed to a scope.

    THE SCOPE IS APPLIED HERE, AND NOWHERE IN THE ENGINE.

    `Orchestrator` takes a `UnifiedBankRepository` and a graph service, both of which were
    already interfaces — that is what let the monolith become services at all. So a scope
    is two decorators over those seams:

      `ScopedBank`          filters `shortlist`, which is where every candidate pool in
                            `fill_queue` comes from. Ranking, exposure control, the picker
                            and the time filter are downstream of it and unchanged.
      `scoped_graph_service` hands over the INDUCED sub-graph, so `sub_nodes_for_main`
                            enumerates only retained nodes and the coverage requirement is
                            right at all four of its call sites with no argument threaded
                            through any of them.

    PROPAGATION KEEPS THE FULL GRAPH. A response that evidences a node outside the scope
    still made that observation; discarding it would be throwing away real evidence over a
    bookkeeping boundary. It simply counts toward no coverage requirement.
    """
    key = (bank_id, version, scope.scope_hash if scope else "")
    if key in _orchestrators:
        return _orchestrators[key]

    bank: object = HttpUnifiedBank(_bank_client, bank_id)
    bank.refresh()  # type: ignore[attr-defined]
    if scope is not None:
        bank = ScopedBank(
            bank,
            item_ids=set(scope.item_ids),
            mains={row.main for row in scope.mains},
        )

    grader = HttpGrader(
        settings.grader_url,
        timeout=settings.grader_timeout_seconds,
        bank_id=bank_id,
    )

    if propagation_is_remote():
        propagation = CompetencyGraphClient(
            settings.competency_graph_url,
            timeout=settings.graph_timeout_seconds,
            bank_id=bank_id,
        )
    else:
        # A supported single-container deployment, not a fallback. `/health` says which
        # one is in force, so choosing it by accident is visible rather than merely quiet.
        logger.info(
            "COMPETENCY_GRAPH_URL is unset — propagation will run in this process"
        )
        propagation = InProcessPropagation(
            lambda: _graph_source.service(bank_id),
            bank_id=bank_id,
            minimum_failures_provider=lambda: _graph_source.minimum_failures_to_block(
                bank_id
            ),
        )

    graph = _graph_source.service(bank_id)
    if scope is not None:
        graph = scoped_graph_service(graph, {node.node_id for node in scope.nodes})

    coverage_critical_only = _bank_client.bank(bank_id).coverage_critical_only
    if scope is not None:
        # The scope already resolved this — against the same bank policy, and possibly
        # overridden by the caller. Re-reading the bank here would let a scope built under
        # `critical_only=false` be gated under the bank's `true`, so the session would
        # require fewer nodes than the manifest promised and the two would disagree about
        # what was measured.
        coverage_critical_only = scope.coverage.critical_only_applied

    built = Orchestrator(
        bank,
        grader,  # type: ignore[arg-type]  # satisfies GraderAgent.grade structurally
        graph=graph,
        coverage_critical_only=coverage_critical_only,
        bank_id=bank_id,
        propagation=propagation,
    )
    _orchestrators[key] = built
    return built


def bank_for(bank_id: str, version: str, scope: ScopeManifest | None = None):
    """The repository behind one orchestrator, for the payload fetch a presentation needs.

    Reached through the orchestrator's own bank rather than a fresh client, so the item
    being rendered is the one that was ranked — from the same version, out of the same
    cache. The scope has to travel for that to hold: without it this would build a SECOND,
    unscoped orchestrator and render out of its cache instead.

    Inside a scope the return is a `ScopedBank`, which forwards `payload_for` to the
    underlying repository. Presentation is not a selection decision — the item was already
    chosen, from the narrowed pool — so it is deliberately not filtered again.
    """
    orchestrator = orchestrator_for(bank_id, version, scope)
    return orchestrator._bank  # noqa: SLF001 - the repository IS this module's product


def reset() -> None:
    """Drop every cached orchestrator. For tests, and for a bank store rebuild."""
    _orchestrators.clear()
