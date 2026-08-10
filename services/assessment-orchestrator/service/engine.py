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

from adaptive_clients import BankRegistryClient, CompetencyGraphClient
from adaptive_clients.engine import HttpGrader, HttpGraphSource, HttpUnifiedBank
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.propagation_port import InProcessPropagation

from .config import settings

logger = logging.getLogger(__name__)

_bank_client = BankRegistryClient(
    settings.bank_registry_url, timeout=settings.bank_timeout_seconds
)
_graph_source = HttpGraphSource(_bank_client)

#: (bank_id, version) -> Orchestrator. Bounded by how many bank versions are in flight,
#: which is one per bank except in the seconds after a replacement.
_orchestrators: dict[tuple[str, str], Orchestrator] = {}


def bank_client() -> BankRegistryClient:
    return _bank_client


def current_version(bank_id: str) -> str:
    return _bank_client.bank(bank_id).version


def propagation_is_remote() -> bool:
    return bool(settings.competency_graph_url.strip())


def orchestrator_for(bank_id: str, version: str) -> Orchestrator:
    """The orchestrator for one bank AT ONE VERSION, built once and reused."""
    key = (bank_id, version)
    if key in _orchestrators:
        return _orchestrators[key]

    bank = HttpUnifiedBank(_bank_client, bank_id)
    bank.refresh()

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

    built = Orchestrator(
        bank,
        grader,  # type: ignore[arg-type]  # satisfies GraderAgent.grade structurally
        graph=_graph_source.service(bank_id),
        coverage_critical_only=_bank_client.bank(bank_id).coverage_critical_only,
        bank_id=bank_id,
        propagation=propagation,
    )
    _orchestrators[key] = built
    return built


def bank_for(bank_id: str, version: str) -> HttpUnifiedBank:
    """The repository behind one orchestrator, for the payload fetch a presentation needs.

    Reached through the orchestrator's own bank rather than a fresh client, so the item
    being rendered is the one that was ranked — from the same version, out of the same
    cache.
    """
    orchestrator = orchestrator_for(bank_id, version)
    return orchestrator._bank  # noqa: SLF001 - the repository IS this module's product


def reset() -> None:
    """Drop every cached orchestrator. For tests, and for a bank store rebuild."""
    _orchestrators.clear()
