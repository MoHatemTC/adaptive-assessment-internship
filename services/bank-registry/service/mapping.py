"""Engine objects to wire types.

Kept out of `main.py` because the interesting part of this service is which fields cross
the boundary, and that argument is easier to read when it is not interleaved with routing.

THE ONE RULE

`item_ref` and `item_full` are separate functions over the same object, and `item_ref`
never touches the payload. That is ADR-0001's second boundary in code: the orchestrator
ranks the entire eligible pool on every step and must not be able to read a question, while
the grader needs the hidden tests and the answer key. One function with a flag would make
the difference a runtime argument rather than a type, and the wrong value would be a
question leak rather than a type error.
"""

from __future__ import annotations

from typing import Any

from adaptive_contracts import (
    BankItemFull,
    BankItemRef,
    BankSummary,
    BankValidationReport,
    CatParameters,
    CompetencyGraphDTO,
    EdgeDecisionDTO,
    GraphEdgeDTO,
    GraphNodeDTO,
    MeasuredVariableRef,
    ParityRowDTO,
    PolicyDTO,
    ValidationFinding,
)
from app.schemas.orchestration import BankItem
from app.services.competency_graph.models import CompetencyGraph
from app.services.competency_graph.policy import ResolvedPolicy
from app.services.orchestrator.bank_store import Validation


def item_ref(item: BankItem) -> BankItemRef:
    """What SELECTION needs. No stem, no options, no answer, no test cases."""
    return BankItemRef(
        item_id=item.item_id,
        modality=item.modality,
        measures=[
            MeasuredVariableRef(variable=m.variable, weight=m.weight)
            for m in item.measures
        ],
        cat=CatParameters(a=item.cat.a, b=item.cat.b, c=item.cat.c),
        estimated_time_seconds=item.estimated_time_seconds,
        minimum_success_confidence=item.minimum_success_confidence,
    )


def item_full(item: BankItem) -> BankItemFull:
    """A ref plus the modality payload. For rendering and for grading."""
    return BankItemFull(
        **item_ref(item).model_dump(),
        status=item.status,
        competency=item.competency,
        sub_competency=item.sub_competency,
        payload=dict(item.payload or {}),
    )


def submission_to_bank_item(entry: dict[str, Any]) -> dict[str, Any]:
    """A posted item into the shape the bank file uses.

    The wire form carries one `payload` field whatever the modality; the bank file nests it
    under the modality name. One field is easier to write a client against; the nested form
    is what makes `BankItem`'s validator able to say "this item claims to be code and has
    no code payload". Neither is wrong, so the translation lives here rather than either
    side changing to suit the other.
    """
    converted = {k: v for k, v in entry.items() if k != "payload"}
    converted[str(entry.get("modality"))] = dict(entry.get("payload") or {})
    return converted


def bank_summary(described: dict[str, Any]) -> BankSummary:
    """One row of `registry.describe()`. A bank that failed to load keeps its row.

    Listing it with its error rather than omitting it is deliberate: a picker that silently
    drops a broken bank makes "the bank is missing" and "the bank is broken" the same
    observation, and only one of them has a fix.
    """
    return BankSummary(
        bank_id=described["bank_id"],
        title=described.get("title", ""),
        version=described.get("version", ""),
        mains=described.get("mains", []),
        items=described.get("items", 0),
        modalities=described.get("modalities", []),
        has_graph=described.get("has_graph", False),
        coverage_critical_only=described.get("coverage_critical_only", False),
        source=described.get("source", "seed"),
        error=described.get("error", ""),
    )


def graph_dto(graph: CompetencyGraph) -> CompetencyGraphDTO:
    return CompetencyGraphDTO(
        schema_version=graph.schema_version,
        nodes=[
            GraphNodeDTO(
                competency_id=node.competency_id,
                title=node.title,
                node_type=node.node_type,
                critical=node.critical,
                context_specific=node.context_specific,
                main_competencies=list(node.main_competencies),
            )
            for node in graph.nodes.values()
        ],
        edges=[
            GraphEdgeDTO(
                from_id=edge.from_id,
                to_id=edge.to_id,
                relation=edge.relation,
                strength=edge.strength,
                weight=edge.weight,
                allow_upward_inference=edge.allow_upward_inference,
                allow_downward_blocking=edge.allow_downward_blocking,
            )
            for edge in graph.edges
        ],
        policy=graph.policy.as_dict(),
    )


def policy_dto(resolved: ResolvedPolicy) -> PolicyDTO:
    summary = resolved.summary()
    return PolicyDTO(
        prerequisite_edges=summary["prerequisite_edges"],
        inference_enabled=summary["inference_enabled"],
        blocking_enabled=summary["blocking_enabled"],
        minimum_failures_to_block=summary["minimum_failures_to_block"],
        deployment=summary["deployment"],
        bank_policy=summary["bank_policy"],
        inert_because=summary["inert_because"],
        edges=[EdgeDecisionDTO(**d.as_dict()) for d in resolved.decisions],
    )


def parity_rows(report: list[dict[str, Any]]) -> list[ParityRowDTO]:
    return [
        ParityRowDTO(
            variable=row.get("variable", ""),
            verdict=row.get("verdict", ""),
            detail={k: v for k, v in row.items() if k not in ("variable", "verdict")},
        )
        for row in report
    ]


def validation_report(validation: Validation, *, version: str = "") -> BankValidationReport:
    return BankValidationReport(
        bank_id=validation.bank_id,
        accepted=validation.accepted,
        version=version,
        items=validation.items,
        mains=list(validation.mains),
        modalities=list(validation.modalities),
        findings=[
            ValidationFinding(
                severity=f.severity, code=f.code, message=f.message, subject=f.subject
            )
            for f in validation.findings
        ],
    )
