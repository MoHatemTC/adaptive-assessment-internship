"""The bank registry: which question bank, and which competency graph goes with it.

One process can serve several assessments. Before this module the bank path was a module
constant and `load_default_competency_graph()` took no argument, so "which bank" was a
deployment-wide fact and the graph was a second deployment-wide fact that nothing tied to
it. That pairing is not optional: the coverage gate asks the graph which sub-competencies
a main requires and the bank which items measure them, so a mismatched pair reports every
required node unmeasured and vetoes convergence for the whole session.

A profile is therefore the unit, not a path — bank and graph together, plus the coverage
policy that only makes sense in the light of how many sub-competencies the graph declares.

Adding a bank is adding a row here and dropping two files into `app/data`. Nothing else in
the engine knows a bank id exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from app.config.settings import settings
from app.services.competency_graph import load_competency_graph
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.policy import (
    ResolvedPolicy,
    apply_policy,
    resolve_policy,
)
from app.services.orchestrator.bank import BankReporting, JsonUnifiedBank

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parents[2] / "data"


class UnknownBankError(KeyError):
    """A bank id that is not in the registry. Names the valid ones — the caller is
    usually an env var or a request field, and 'KeyError: AIE2' helps nobody."""

    def __init__(self, bank_id: str) -> None:
        super().__init__(f"unknown bank {bank_id!r}; registered: {sorted(REGISTRY)}")


@dataclass(frozen=True)
class BankProfile:
    """One assessable bank and everything that must travel with it."""

    bank_id: str
    title: str
    bank_path: Path
    graph_path: Path | None = None
    # Declared for tests and the bank picker. Authoritative source is still the bank file;
    # this is what the bank is *supposed* to contain, so a silent content change is caught.
    mains: tuple[str, ...] = ()
    # None defers to `settings.graph_coverage_critical_only`. Set per bank because the
    # right answer depends on how many sub-competencies sit under a main versus how many
    # questions the budget allows — see `coverage_policy` below.
    coverage_critical_only: bool | None = None


REGISTRY: dict[str, BankProfile] = {
    "DA": BankProfile(
        bank_id="DA",
        title="Prepare and Analyze Data",
        bank_path=DATA / "question_bank.json",
        graph_path=DATA / "competency_graph.json",
        mains=("DA",),
        # Six sub-nodes against a twelve-question cap: full coverage is reachable.
        coverage_critical_only=None,
    ),
    "PY": BankProfile(
        bank_id="PY",
        title="Python Engineering",
        bank_path=DATA / "question_bank_PY_20260803_083616.json",
        graph_path=DATA / "competency_graph_PY_20260803_083616.json",
        mains=("PY",),
        coverage_critical_only=None,
    ),
    "AIE": BankProfile(
        bank_id="AIE",
        title="AI Engineer",
        bank_path=DATA / "question_bank_AIE.json",
        graph_path=DATA / "competency_graph_AIE.json",
        mains=("C1", "C3", "C6"),
        # C6 declares sixteen sub-competencies and every AIE item measures exactly one, so
        # full coverage would cost sixteen questions against a cap of twelve — the gate
        # could never be satisfied and every session would end on the budget escape.
        # Critical-only reduces the requirement to five. See `docs/competency_graph.md`.
        coverage_critical_only=True,
    ),
    "AIE-JR-V3": BankProfile(
        bank_id="AIE-JR-V3",
        title="Junior AI Engineer v3 (60 items)",
        bank_path=DATA / "question_bank_AIE_JR_v3.json",
        graph_path=DATA / "competency_graph_AIE_JR_v3.json",
        mains=("C1",),
        # The generated coverage graph contains exactly the three measurable nodes.
        coverage_critical_only=False,
    ),
    "JAI-600": BankProfile(
        bank_id="JAI-600",
        title="Junior AI Engineer 2026 (600 items)",
        bank_path=DATA / "question_bank_JAI_2026_600.json",
        graph_path=DATA / "competency_graph_JAI_2026_600.json",
        mains=("C1", "C2", "C3", "C4", "C5", "C6"),
        # Each main has only 3-5 nodes, so full direct coverage is reachable under the
        # twelve-question per-main limit and is preferable to silently weakening it.
        coverage_critical_only=False,
    ),
}


def resolve_bank_id(bank_id: str | None = None) -> str:
    """The id to use, resolved before caching so `None` and the default share an entry."""
    resolved = (bank_id or settings.active_bank or "").strip()
    if resolved not in REGISTRY:
        raise UnknownBankError(resolved)
    return resolved


def profile(bank_id: str | None = None) -> BankProfile:
    return REGISTRY[resolve_bank_id(bank_id)]


def get_bank(bank_id: str | None = None) -> BankReporting:
    """The bank for this id, parsed once per process."""
    return _bank_cached(resolve_bank_id(bank_id))


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
    for bank_id, prof in REGISTRY.items():
        try:
            bank = get_bank(bank_id)
            coverage = bank.coverage()
            described.append(
                {
                    "bank_id": bank_id,
                    "title": prof.title,
                    "mains": bank.variables(),
                    "items": len(bank.all_items()),
                    "modalities": sorted({m for c in coverage.values() for m in c}),
                    "has_graph": get_graph_service(bank_id) is not None,
                }
            )
        except (OSError, ValueError) as exc:
            # A broken bank must not hide the working ones from the picker.
            logger.error("bank %s could not be described: %s", bank_id, exc)
            described.append(
                {"bank_id": bank_id, "title": prof.title, "error": str(exc)}
            )
    return described


@cache
def _bank_cached(bank_id: str) -> JsonUnifiedBank:
    return JsonUnifiedBank(REGISTRY[bank_id].bank_path)


def get_propagation_policy(bank_id: str | None = None) -> ResolvedPolicy | None:
    """How deployment, bank and edge configuration resolved for this bank.

    Exposed so an operator can ask "why is this edge inert?" without reading three files
    and doing the AND in their head. `scripts/show_propagation_policy.py` prints it and
    the diagnostics endpoint returns `.summary()`.
    """
    return _policy_cached(resolve_bank_id(bank_id))


@cache
def _policy_cached(bank_id: str) -> ResolvedPolicy | None:
    graph_path = REGISTRY[bank_id].graph_path
    if graph_path is None:
        return None
    return resolve_policy(
        load_competency_graph(graph_path),
        deployment_inference=settings.graph_upward_inference_enabled,
        deployment_blocking=settings.graph_descendant_blocking_enabled,
        deployment_minimum_failures_to_block=settings.graph_minimum_failures_to_block,
        deployment_accepted_validation_statuses=settings.accepted_validation_statuses(),
    )


@cache
def _graph_cached(bank_id: str) -> CompetencyGraphService | None:
    """The graph for this bank, with the propagation policy already applied.

    Resolution happens HERE, once per bank per process, rather than at every traversal.
    Everything downstream reads the edge flags, so baking the effective values in makes
    the policy apply everywhere without a new check in a hot path and without a second
    place that could disagree about the answer.
    """
    graph_path = REGISTRY[bank_id].graph_path
    if graph_path is None:
        return None

    graph = load_competency_graph(graph_path)
    resolved = _policy_cached(bank_id)
    if resolved is not None:
        graph = apply_policy(graph, resolved)
        summary = resolved.summary()
        logger.info(
            "bank %s propagation policy: %d prerequisite edges, %d may infer, %d may block "
            "(deployment inference=%s blocking=%s, failures to block=%d)",
            bank_id,
            summary["prerequisite_edges"],
            summary["inference_enabled"],
            summary["blocking_enabled"],
            summary["deployment"]["upward_inference"],
            summary["deployment"]["descendant_blocking"],
            summary["minimum_failures_to_block"],
        )
    return CompetencyGraphService(graph)


def reset_caches() -> None:
    """Drop parsed banks, graphs and resolved policies.

    For tests that rewrite a bank file on disk — and for any test that changes a
    propagation setting, since the policy is resolved once at load and would otherwise
    survive the change.
    """
    _bank_cached.cache_clear()
    _graph_cached.cache_clear()
    _policy_cached.cache_clear()
