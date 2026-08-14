"""Authoring: create and revise assessment definitions — graphs included. Pure.

    from adaptive_engine import authoring

    graph = authoring.derive_graph("python-backend", items)
    report = authoring.validate_content("python-backend", items, graph)
    definition = authoring.build_assessment("python-backend", "v1", items)
    updated = authoring.revise_assessment(definition, version="v2", add_items=[...])

Every function returns values; nothing writes. The host persists definitions, exactly as
it persists adaptive states. Every decision is REUSED from the engine's own authoring
path: graph derivation is ``cat_engine.ingest.derive`` (item co-measurement — no LLM, no
network, fully deterministic), and validation is the same checks the engine's checked-in
banks are asserted against (`BankStore.validate`), returned as typed findings.

THE BOUNDARY IS TYPED. This module is called by an outside service, so every input is a
declared model — items are engine ``BankItem``s, graphs are ``CompetencyGraph``,
declarations are ``CompetencyDeclaration`` — and raw JSON dicts coerce into each with
field-level errors (`InvalidDefinition`) rather than KeyErrors downstream.

Two deliberate departures from the legacy upload pipeline, both boundary fixes:

- ``relation_threshold`` and ``edge_floor`` are explicit per-call arguments AND stamped
  into the produced graph (``graph.derivation``), never ambient settings — two graphs
  built to different rules now say so in the artifact.
- Nothing is registered anywhere. The legacy path's final step (write to the bank
  store) is the host's job now.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError

from adaptive_engine.compilation import compile_assessment
from adaptive_engine.errors import InvalidDefinition
from adaptive_engine.models import (
    AssessmentDefinition,
    BankItem,
    CompetencyDeclaration,
    CompetencyGraph,
    FrozenModel,
    MeasurementPolicy,
)
from cat_engine.engine.services.orchestrator.bank_store import BankStore
from cat_engine.ingest.derive import DEFAULT_EDGE_FLOOR, DEFAULT_RELATION_THRESHOLD
from cat_engine.ingest.derive import derive_graph as _engine_derive_graph

#: The engine's shipped per-competency question cap; the coverage-feasibility check
#: needs a budget, and this is the default the runtime enforces.
DEFAULT_QUESTION_BUDGET = 12

#: "Leave it as the definition has it" for revise_assessment's optional overrides.
_KEEP = object()


class Finding(FrozenModel):
    """One thing wrong, or one thing worth knowing, about assessment content."""

    severity: Literal["error", "warning"]
    code: str
    message: str
    subject: str = ""


class ValidationReport(FrozenModel):
    """Why content was accepted or refused, in enough detail to fix it."""

    assessment_id: str
    accepted: bool
    findings: list[Finding] = Field(default_factory=list)
    items: int = 0
    mains: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]


def derive_graph(
    assessment_id: str,
    items: Sequence[BankItem | dict[str, Any]],
    *,
    declaration: Sequence[CompetencyDeclaration | dict[str, Any]] | None = None,
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD,
    edge_floor: float = DEFAULT_EDGE_FLOOR,
) -> CompetencyGraph:
    """THE general-purpose operation: a competency graph, from the question list alone.

    Nodes come from the items' ``measures`` (sub-competencies and their mains); edge
    weights come from item co-measurement — how often two competencies are tested by the
    same questions. A sub-to-sub relation at or above ``relation_threshold`` becomes
    PREREQUISITE, below it CONTRIBUTES_TO, and below ``edge_floor`` no edge at all.
    Every derived prerequisite edge ships inert and unvalidated: an edge derived from
    co-occurrence has strictly less authority than one an expert authored.

    ``declaration`` carries the two facts a question list cannot imply — which
    sub-competencies are critical, and which serve more than one main — plus display
    titles.

    The thresholds used are stamped into ``graph.derivation``, so an artifact always
    says which rules built it.
    """
    validated = _coerce_items(items)
    raw_graph = _engine_derive_graph(
        assessment_id,
        [item.model_dump(mode="json") for item in validated],
        declared=_declared_mapping(declaration),
        relation_threshold=relation_threshold,
        edge_floor=edge_floor,
    )
    raw_graph["derivation"] = {
        "basis": "item_co_measurement",
        "relation_threshold": relation_threshold,
        "edge_floor": edge_floor,
    }
    return CompetencyGraph.model_validate(raw_graph)


def validate_content(
    assessment_id: str,
    items: Sequence[BankItem | dict[str, Any]],
    graph: CompetencyGraph | dict[str, Any] | None,
    *,
    question_budget: int = DEFAULT_QUESTION_BUDGET,
    critical_only: bool | None = None,
) -> ValidationReport:
    """Everything checkable about (items, graph) before they measure anybody.

    The same findings the engine's own seed banks are held to: invalid or duplicate
    items, an invalid graph, a graph that does not belong to these items, items
    measuring nodes the graph does not know, a main whose required coverage cannot fit
    the question budget, and required nodes no active item measures.

    ``critical_only`` left None is derived from the graph itself — coverage requires
    only critical sub-competencies as soon as the graph marks any node non-critical,
    which is how the legacy upload path resolves it too.
    """
    validated_graph = _coerce_graph(graph)
    validation = BankStore(seeds={}, store_dir=Path(".")).validate(
        bank_id=assessment_id,
        items=[item.model_dump(mode="json") for item in _coerce_items(items)],
        graph=validated_graph.wire_format() if validated_graph is not None else None,
        coverage_critical_only=(
            critical_only
            if critical_only is not None
            else _critical_only_from(validated_graph)
        ),
        question_budget=question_budget,
        deployment_critical_only=False,
    )
    return ValidationReport(
        assessment_id=assessment_id,
        accepted=validation.accepted,
        findings=[
            Finding(
                severity=finding.severity,
                code=finding.code,
                message=finding.message,
                subject=finding.subject,
            )
            for finding in validation.findings
        ],
        items=validation.items,
        mains=validation.mains,
        modalities=validation.modalities,
    )


def build_assessment(
    assessment_id: str,
    version: str,
    items: Sequence[BankItem | dict[str, Any]],
    *,
    graph: CompetencyGraph | dict[str, Any] | None = None,
    declaration: Sequence[CompetencyDeclaration | dict[str, Any]] | None = None,
    targets: list[str] | None = None,
    policy: MeasurementPolicy | None = None,
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD,
    edge_floor: float = DEFAULT_EDGE_FLOOR,
) -> AssessmentDefinition:
    """Create a new assessment: questions in, validated ``AssessmentDefinition`` out.

    ``graph`` left None is derived from the items; a hand-authored graph is accepted and
    held to the same pairing checks. Content that fails validation raises
    ``InvalidDefinition`` naming every error finding, and the result is compiled once as
    the final gate — what this returns, the runtime runs.
    """
    validated_items = _coerce_items(items)
    validated_graph = (
        _coerce_graph(graph)
        if graph is not None
        else derive_graph(
            assessment_id,
            validated_items,
            declaration=declaration,
            relation_threshold=relation_threshold,
            edge_floor=edge_floor,
        )
    )

    budget = (
        policy.max_questions_per_competency
        if policy is not None and policy.max_questions_per_competency is not None
        else DEFAULT_QUESTION_BUDGET
    )
    report = validate_content(
        assessment_id,
        validated_items,
        validated_graph,
        question_budget=budget,
        critical_only=policy.coverage_critical_only if policy is not None else None,
    )
    if not report.accepted:
        details = "; ".join(
            f"{f.code}({f.subject}): {f.message}" if f.subject else f"{f.code}: {f.message}"
            for f in report.errors
        )
        raise InvalidDefinition(f"assessment {assessment_id!r} content is invalid: {details}")

    # The graph decides how coverage is gated: as soon as it marks any node non-critical,
    # coverage must require only the critical ones — the rule the legacy path derives at
    # upload — and that decision has to travel WITH the definition, not sit in settings.
    derived_critical_only = _critical_only_from(validated_graph)
    if policy is None:
        policy = MeasurementPolicy(coverage_critical_only=True) if derived_critical_only else None
    elif policy.coverage_critical_only is None and derived_critical_only:
        policy = policy.model_copy(update={"coverage_critical_only": True})

    definition = AssessmentDefinition(
        assessment_id=assessment_id,
        version=version,
        items=validated_items,
        graph=validated_graph,
        targets=targets,
        policy=policy,
    )
    compile_assessment(definition)
    return definition


def revise_assessment(
    definition: AssessmentDefinition,
    *,
    version: str,
    add_items: Sequence[BankItem | dict[str, Any]] | None = None,
    update_items: Sequence[BankItem | dict[str, Any]] | None = None,
    retire_item_ids: Sequence[str] | None = None,
    graph: CompetencyGraph | dict[str, Any] | Literal["rederive", "keep"] = "rederive",
    declaration: Sequence[CompetencyDeclaration | dict[str, Any]] | None = None,
    targets: Any = _KEEP,
    policy: Any = _KEEP,
    relation_threshold: float = DEFAULT_RELATION_THRESHOLD,
    edge_floor: float = DEFAULT_EDGE_FLOOR,
) -> AssessmentDefinition:
    """Revise an assessment: returns a NEW definition, never mutates the old one.

    Item operations: ``add_items`` appends, ``update_items`` replaces by ``item_id``,
    ``retire_item_ids`` marks items retired (kept in the definition so history stays
    whole; the engine never administers a retired item). A revision must carry a new
    ``version`` — the runtime's content hash would reject a silently edited same-version
    definition anyway, so this fails at authoring time where the author can act.

    ``graph="rederive"`` rebuilds the graph from the revised items; ``"keep"`` carries
    the old one forward (the pairing checks still run, so an item measuring a node the
    kept graph does not know is refused); an explicit graph replaces it.
    """
    if version == definition.version:
        raise InvalidDefinition(
            f"a revision needs a new version; {version!r} is the current one"
        )

    items = list(definition.items)
    by_id = {item.item_id: index for index, item in enumerate(items)}

    for replacement in _coerce_items(update_items or []):
        if replacement.item_id not in by_id:
            raise InvalidDefinition(f"cannot update unknown item {replacement.item_id!r}")
        items[by_id[replacement.item_id]] = replacement
    for item_id in retire_item_ids or []:
        if item_id not in by_id:
            raise InvalidDefinition(f"cannot retire unknown item {item_id!r}")
        items[by_id[item_id]] = items[by_id[item_id]].model_copy(
            update={"status": "retired"}
        )
    items.extend(_coerce_items(add_items or []))

    if graph == "rederive":
        revised_graph: CompetencyGraph | dict[str, Any] | None = None
    elif graph == "keep":
        revised_graph = definition.graph
    else:
        revised_graph = graph

    return build_assessment(
        definition.assessment_id,
        version,
        items,
        graph=revised_graph,
        declaration=declaration,
        targets=definition.targets if targets is _KEEP else targets,
        policy=definition.policy if policy is _KEEP else policy,
        relation_threshold=relation_threshold,
        edge_floor=edge_floor,
    )


def _coerce_items(items: Sequence[BankItem | dict[str, Any]]) -> list[BankItem]:
    validated: list[BankItem] = []
    for entry in items:
        if isinstance(entry, BankItem):
            validated.append(entry)
            continue
        try:
            validated.append(BankItem.model_validate(entry))
        except ValidationError as exc:
            identifier = entry.get("item_id", "<no id>") if isinstance(entry, dict) else "?"
            raise InvalidDefinition(f"item {identifier} is invalid: {exc}") from exc
    return validated


def _coerce_graph(
    graph: CompetencyGraph | dict[str, Any] | None,
) -> CompetencyGraph | None:
    if graph is None or isinstance(graph, CompetencyGraph):
        return graph
    try:
        return CompetencyGraph.model_validate(graph)
    except ValidationError as exc:
        raise InvalidDefinition(f"competency graph is invalid: {exc}") from exc


def _declared_mapping(
    declaration: Sequence[CompetencyDeclaration | dict[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    """The engine derivation's ``declared`` mapping, from the typed declaration."""
    if declaration is None:
        return None
    declared: dict[str, dict[str, Any]] = {}
    for entry in declaration:
        if not isinstance(entry, CompetencyDeclaration):
            try:
                entry = CompetencyDeclaration.model_validate(entry)
            except ValidationError as exc:
                raise InvalidDefinition(f"competency declaration is invalid: {exc}") from exc
        body: dict[str, Any] = {"title": entry.title, "critical": entry.critical}
        if entry.mains is not None:
            body["mains"] = list(entry.mains)
        declared[entry.competency_id] = body
    return declared


def _critical_only_from(graph: CompetencyGraph | None) -> bool:
    """Coverage requires only critical nodes iff the graph marks any node non-critical.

    The legacy upload path derives this from content rather than configuration, and the
    reason holds: a graph that distinguishes critical from non-critical is asking for
    the distinction to matter.
    """
    if graph is None:
        return False
    return any(
        node.node_type == "sub_competency" and node.critical is False
        for node in graph.nodes
    )
