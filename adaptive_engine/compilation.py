"""Pure assessment preparation: validate with the engine's own validators, wire the brain.

``compile_assessment`` turns a definition into a ready-to-run ``CompiledAssessment``:
an in-memory bank repository, the competency graph with its propagation policy resolved
and baked in, and an ``Orchestrator`` — the existing, proven engine loop — constructed
directly over them. No registry, no bank store, no session store, no wiring cache is
touched; the caller may cache the compiled object by (assessment_id, version), and the
engine itself keeps no cache.

Validation is REUSED, not duplicated: items are validated by the engine's ``BankItem``
schema, the graph by ``parse_and_validate_graph``, and the propagation policy by
``resolve_policy`` — the same code paths the legacy module runs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from adaptive_engine.errors import InvalidDefinition
from adaptive_engine.models import AssessmentDefinition
from cat_engine.engine.schemas.orchestration import BankItem
from cat_engine.engine.services.competency_graph import config as graph_config
from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService
from cat_engine.engine.services.competency_graph.policy import (
    ResolvedPolicy,
    apply_policy,
    resolve_policy,
)
from cat_engine.engine.services.competency_graph.validator import (
    CompetencyGraphValidationError,
    parse_and_validate_graph,
)
from cat_engine.engine.services.orchestrator.competency import main_competency
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
from cat_engine.engine.services.orchestrator.propagation_port import InProcessPropagation


class InMemoryBank:
    """The engine's four-method bank protocol over caller-supplied items.

    The same filtering rules as the engine's file-backed bank: only active items, never a
    served item, only items that load on the requested variable. Holding the items in
    memory is the whole difference — the host owns question persistence now.
    """

    def __init__(self, items: tuple[BankItem, ...]) -> None:
        self._items = items
        self._by_id = {item.item_id: item for item in items}

    def all_items(self) -> list[BankItem]:
        return list(self._items)

    def get(self, item_id: str) -> BankItem | None:
        return self._by_id.get(item_id)

    def shortlist(self, variable: str, exclude: set[str]) -> list[BankItem]:
        return [
            item
            for item in self._items
            if item.status == "active"
            and item.item_id not in exclude
            and item.loading(variable) > 0
        ]

    def variables(self) -> list[str]:
        return sorted({main_competency(m.variable) for i in self._items for m in i.measures})


@dataclass(frozen=True)
class CompiledAssessment:
    """An assessment validated and wired for execution. In-memory, immutable, cacheable.

    ``orchestrator`` is the existing engine loop, stateless between calls — safe to share
    across concurrent runs of this assessment. ``content_hash`` fingerprints the full
    definition (answer keys included) so a persisted state can never silently continue
    against edited content that kept the same version string.
    """

    definition: AssessmentDefinition
    orchestrator: Orchestrator = field(repr=False)
    bank: InMemoryBank = field(repr=False)
    graph_service: CompetencyGraphService | None = field(repr=False)
    resolved_policy: ResolvedPolicy | None = field(repr=False)
    content_hash: str
    targets: tuple[str, ...]

    @property
    def assessment_id(self) -> str:
        return self.definition.assessment_id

    @property
    def version(self) -> str:
        return self.definition.version


def content_hash_of(definition: AssessmentDefinition) -> str:
    """Deterministic fingerprint of the complete definition content."""
    canonical = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_assessment(definition: AssessmentDefinition) -> CompiledAssessment:
    """Validate the definition end to end and construct the engine over it.

    Raises ``InvalidDefinition`` for: an item the ``BankItem`` schema rejects, duplicate
    item ids, a graph the graph validator rejects, or a target no item measures.
    """
    if not isinstance(definition, AssessmentDefinition):
        definition = AssessmentDefinition.model_validate(definition)

    items: list[BankItem] = []
    seen: set[str] = set()
    for raw in definition.items:
        try:
            item = BankItem.model_validate(raw)
        except ValidationError as exc:
            identifier = raw.get("item_id", "<no id>") if isinstance(raw, dict) else "?"
            raise InvalidDefinition(f"item {identifier} is invalid: {exc}") from exc
        if item.item_id in seen:
            raise InvalidDefinition(f"duplicate item id: {item.item_id}")
        seen.add(item.item_id)
        _require_gradable(item)
        items.append(item)

    bank = InMemoryBank(tuple(items))
    available = bank.variables()
    targets = tuple(definition.targets) if definition.targets else tuple(available)
    unknown = [t for t in targets if t not in available]
    if unknown:
        raise InvalidDefinition(f"no item measures target competencies {unknown}")

    graph_service: CompetencyGraphService | None = None
    resolved: ResolvedPolicy | None = None
    if definition.graph is not None:
        try:
            graph = parse_and_validate_graph(
                definition.graph, source=definition.assessment_id
            )
        except CompetencyGraphValidationError as exc:
            raise InvalidDefinition(f"competency graph is invalid: {exc}") from exc
        # The same resolution the legacy registry performs once per bank: fold
        # deployment, bank and edge permissions into one decision per edge and bake the
        # result into the edge flags, so traversal never re-checks policy.
        resolved = resolve_policy(
            graph,
            deployment_inference=graph_config.upward_inference_enabled(),
            deployment_blocking=graph_config.descendant_blocking_enabled(),
        )
        graph_service = CompetencyGraphService(apply_policy(graph, resolved))

    minimum_failures = resolved.minimum_failures_to_block if resolved else None
    orchestrator = Orchestrator(
        bank,
        GraderAgent(None),  # mcq grades deterministically; other modalities arrive pre-graded
        graph=graph_service,
        coverage_critical_only=(
            definition.policy.coverage_critical_only if definition.policy else None
        ),
        bank_id=definition.assessment_id,
        propagation=InProcessPropagation(
            lambda: graph_service,
            bank_id=definition.assessment_id,
            minimum_failures_provider=lambda: minimum_failures,
        ),
    )
    if graph_service is None:
        # The orchestrator's deprecated fallback would otherwise try to load the ACTIVE
        # bank's graph from the registry on first use. A definition without a graph means
        # "no graph", not "somebody else's graph".
        orchestrator._graph_checked = True

    if definition.policy is not None:
        _apply_measurement_policy(definition.policy)

    return CompiledAssessment(
        definition=definition,
        orchestrator=orchestrator,
        bank=bank,
        graph_service=graph_service,
        resolved_policy=resolved,
        content_hash=content_hash_of(definition),
        targets=targets,
    )


def _require_gradable(item: BankItem) -> None:
    """An mcq item must carry a usable answer key — found at compile, not mid-session.

    The engine's ``BankItem`` schema checks that the payload matches the modality but
    leaves payload contents to the bank store's validators, which this layer bypasses.
    """
    if item.modality != "mcq":
        return
    options = item.payload.get("options")
    answer_index = item.payload.get("answer_index")
    if not isinstance(options, list) or len(options) < 2:
        raise InvalidDefinition(f"item {item.item_id}: mcq needs at least 2 options")
    if (
        isinstance(answer_index, bool)
        or not isinstance(answer_index, int)
        or not 0 <= answer_index < len(options)
    ):
        raise InvalidDefinition(
            f"item {item.item_id}: mcq needs an answer_index within its options"
        )


def _apply_measurement_policy(policy) -> None:
    """Route policy overrides through the engine's own config layer.

    ``CatConfig.apply`` enforces the one-measurement-policy-per-process guarantee: a
    second, DIFFERENT policy raises ``ConfigConflict`` loudly rather than silently
    re-tuning estimates mid-flight. Identical policies re-apply freely.
    """
    from cat_engine.config import CatConfig

    overrides = policy.overrides()
    if overrides:
        CatConfig(**overrides).apply()
