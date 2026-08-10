"""The bank registry: which question bank, and which competency graph goes with it.

One process can serve several assessments. Before this module the bank path was a module
constant and `load_default_competency_graph()` took no argument, so "which bank" was a
deployment-wide fact and the graph was a second deployment-wide fact that nothing tied to
it. That pairing is not optional: the coverage gate asks the graph which sub-competencies
a main requires and the bank which items measure them, so a mismatched pair reports every
required node unmeasured and vetoes convergence for the whole session.

A profile is therefore the unit, not a path — bank and graph together, plus the coverage
policy that only makes sense in the light of how many sub-competencies the graph declares.

WHERE THE PROFILES COME FROM

They used to be a dict written into this file, and adding a bank meant editing Python and
redeploying. `bank_store.BankStore` replaced that with two layers — the checked-in seeds,
and a writable directory another service posts into. This module is now the process-wide
handle on that store, and it keeps the function names every caller already uses.

`REGISTRY` is still here and still behaves like a mapping. It is a live view now, so a
posted bank appears everywhere a checked-in one does without a single caller changing.
"""

from __future__ import annotations

import logging

from app.config.paths import DATA_DIR
from app.config.settings import settings
from app.services.competency_graph import load_competency_graph
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.policy import (
    ResolvedPolicy,
    apply_policy,
    resolve_policy,
)
from app.services.orchestrator.bank import BankReporting
from app.services.orchestrator.bank_store import (
    BankProfile,
    BankStore,
    ProfileView,
    Validation,
    _seed_profiles,
)
from app.services.orchestrator.bank_store import (
    UnknownBankError as _UnknownBankError,
)

logger = logging.getLogger(__name__)

DATA = DATA_DIR

#: The process-wide store. One per process on purpose: parsing a 1.8 MB bank per request
#: is the difference between selection inside its 100 ms budget and outside it.
STORE = BankStore(seeds=_seed_profiles())

#: bank id -> profile, live. Read by the test suite, the bank picker and the error below.
REGISTRY = ProfileView(STORE)

__all__ = [
    "DATA",
    "REGISTRY",
    "STORE",
    "BankProfile",
    "UnknownBankError",
    "Validation",
    "coverage_policy",
    "describe",
    "get_bank",
    "get_graph_service",
    "get_propagation_policy",
    "profile",
    "reset_caches",
    "resolve_bank_id",
    "use_store",
    "version",
]


def use_store(store: BankStore) -> None:
    """Point this process at a different bank store.

    For a service that resolves its store directory at startup rather than from the
    environment, and for tests, which must never write a bank into the checked-in data
    directory. Rebinding both names together is the whole of it — every function here
    reads them as module globals at call time.
    """
    global STORE, REGISTRY
    STORE = store
    REGISTRY = ProfileView(store)


class UnknownBankError(_UnknownBankError):
    """Retained as a name in this module: callers catch `registry.UnknownBankError`."""

    def __init__(self, bank_id: str) -> None:
        super().__init__(bank_id, list(REGISTRY))


def resolve_bank_id(bank_id: str | None = None) -> str:
    """The id to use, resolved before caching so `None` and the default share an entry."""
    resolved = (bank_id or settings.active_bank or "").strip()
    if resolved not in REGISTRY:
        raise UnknownBankError(resolved)
    return resolved


def profile(bank_id: str | None = None) -> BankProfile:
    return STORE.profile(resolve_bank_id(bank_id))


def get_bank(bank_id: str | None = None) -> BankReporting:
    """The bank for this id, parsed once per version per process."""
    return STORE.bank(resolve_bank_id(bank_id))


def version(bank_id: str | None = None) -> str:
    """A content hash over this bank, its graph and its declared profile.

    Pinned into an assessment at `begin`, so replacing a bank cannot change the item pool
    underneath a candidate half way through a session.
    """
    return STORE.version(resolve_bank_id(bank_id))


def get_graph_service(bank_id: str | None = None) -> CompetencyGraphService | None:
    """The competency graph paired with this bank, or None when it declares none."""
    return _graph_cached(resolve_bank_id(bank_id))


def coverage_policy(bank_id: str | None = None) -> bool:
    """Whether coverage requires only the critical sub-competencies, for this bank."""
    configured = profile(bank_id).coverage_critical_only
    if configured is None:
        return settings.graph_coverage_critical_only
    return configured


def describe() -> list[dict]:
    """Registered banks, for a bank picker. Item counts come from the file, not the row."""
    described: list[dict] = []
    for bank_id, prof in sorted(REGISTRY.items()):
        try:
            bank = get_bank(bank_id)
            coverage = bank.coverage()
            described.append(
                {
                    "bank_id": bank_id,
                    "title": prof.title,
                    "version": STORE.version(bank_id),
                    "source": prof.source,
                    "mains": bank.variables(),
                    "items": len(bank.all_items()),
                    "modalities": sorted({m for c in coverage.values() for m in c}),
                    "has_graph": get_graph_service(bank_id) is not None,
                    "coverage_critical_only": coverage_policy(bank_id),
                }
            )
        except (OSError, ValueError) as exc:
            # A broken bank must not hide the working ones from the picker.
            logger.error("bank %s could not be described: %s", bank_id, exc)
            described.append(
                {
                    "bank_id": bank_id,
                    "title": prof.title,
                    "source": prof.source,
                    "error": str(exc),
                }
            )
    return described


def get_propagation_policy(bank_id: str | None = None) -> ResolvedPolicy | None:
    """How deployment, bank and edge configuration resolved for this bank.

    Exposed so an operator can ask "why is this edge inert?" without reading three files
    and doing the AND in their head. `scripts/show_propagation_policy.py` prints it and
    the bank registry serves `.summary()`.
    """
    return _policy_cached(resolve_bank_id(bank_id))


def _policy_cached(bank_id: str) -> ResolvedPolicy | None:
    def build() -> ResolvedPolicy | None:
        graph_path = STORE.profile(bank_id).graph_path
        if graph_path is None:
            return None
        return resolve_policy(
            load_competency_graph(graph_path),
            deployment_inference=settings.graph_upward_inference_enabled,
            deployment_blocking=settings.graph_descendant_blocking_enabled,
            deployment_minimum_failures_to_block=settings.graph_minimum_failures_to_block,
            deployment_accepted_validation_statuses=settings.accepted_validation_statuses(),
        )

    return STORE._cached(STORE._policies, bank_id, build)


def _graph_cached(bank_id: str) -> CompetencyGraphService | None:
    """The graph for this bank, with the propagation policy already applied.

    Resolution happens HERE, once per bank version per process, rather than at every
    traversal. Everything downstream reads the edge flags, so baking the effective values
    in makes the policy apply everywhere without a new check in a hot path and without a
    second place that could disagree about the answer.
    """

    def build() -> CompetencyGraphService | None:
        graph_path = STORE.profile(bank_id).graph_path
        if graph_path is None:
            return None

        graph = load_competency_graph(graph_path)
        resolved = _policy_cached(bank_id)
        if resolved is not None:
            graph = apply_policy(graph, resolved)
            summary = resolved.summary()
            logger.info(
                "bank %s propagation policy: %d prerequisite edges, %d may infer, "
                "%d may block (deployment inference=%s blocking=%s, failures to block=%d)",
                bank_id,
                summary["prerequisite_edges"],
                summary["inference_enabled"],
                summary["blocking_enabled"],
                summary["deployment"]["upward_inference"],
                summary["deployment"]["descendant_blocking"],
                summary["minimum_failures_to_block"],
            )
        return CompetencyGraphService(graph)

    return STORE._cached(STORE._graphs, bank_id, build)


def reset_caches() -> None:
    """Drop parsed banks, graphs and resolved policies.

    For tests that rewrite a bank file on disk — and for any test that changes a
    propagation setting, since the policy is resolved once at load and would otherwise
    survive the change.
    """
    STORE.reset()
