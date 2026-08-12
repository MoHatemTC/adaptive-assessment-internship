"""Engine objects, projected onto what a host receives.

TWO VIEWS OF ONE ITEM, AND THE DIFFERENCE IS THE SECURITY BOUNDARY

They are in the same file on purpose, because the difference between them is easier to keep
right when you can see both:

    item_ref(item)       what SELECTION needs. Parameters. No stem, no options, no answer,
                         no test cases. There is no payload FIELD on `BankItemRef`, so a
                         caller holding one cannot leak a question by mistake.
    item_full(item)      a ref plus the payload — the answer key, the hidden tests, the
                         rubric. For rendering, grading and authoring.
    presented_item(item) what a CANDIDATE may see. Derived from the payload, and it decides
                         field by field what crosses.

One function with a flag would make that difference a runtime argument rather than a type,
and the wrong value would be a question leak rather than a type error.

TWO THINGS ARE WITHHELD FROM A CANDIDATE, FOR DIFFERENT REASONS

`reference_solution` and `answer_index` are withheld because a client that could read them
could harvest the bank. That one is obvious.

The grading DETAIL is withheld because feedback after each answer changes what the
assessment measures. A candidate told "wrong, the hidden test that failed was the empty-list
case" learns something between question four and question five, and the estimate that comes
out is of a person who was being taught mid-measurement. The engine's own audit record keeps
all of it; this boundary is about what goes back to a caller before the session ends.

WHAT IS NOT WITHHELD

That the sandbox fell over. The candidate's answer moved nothing and they may be asked
again — telling them so costs the measurement nothing and not telling them is just
confusing.
"""

from __future__ import annotations

from typing import Any

from cat_engine.contracts import (
    AssessmentReportDTO,
    BankItemFull,
    BankItemRef,
    BankSummary,
    CatParameters,
    CompetencyGraphDTO,
    EdgeDecisionDTO,
    GradeReceiptDTO,
    GraphEdgeDTO,
    GraphNodeDTO,
    MeasuredVariableRef,
    ParityRowDTO,
    PolicyDTO,
    PresentedItemDTO,
    PresentingDTO,
    ScopeManifest,
    ScopeSummaryDTO,
)
from cat_engine.engine.schemas.orchestration import (
    AssessmentReport,
    BankItem,
    GradedResponse,
)
from cat_engine.engine.services.competency_graph.models import CompetencyGraph
from cat_engine.engine.services.competency_graph.policy import ResolvedPolicy


#: Flags a candidate is told about mid-session. Everything else waits for the report.
#:
#: The prefixes are what the ENGINE actually emits. The monolith filtered on
#: `INFRASTRUCTURE_`, which nothing has ever produced — the code path emits
#: `SANDBOX_UNAVAILABLE: …` and the voice path `PACKAGE_INFRASTRUCTURE_ERROR` — so that
#: filter matched nothing on every response including the ones it existed for, and a
#: candidate whose sandbox died was told nothing at all.
INFRASTRUCTURE_FLAG_PREFIXES = (
    "SANDBOX_UNAVAILABLE",
    "PACKAGE_INFRASTRUCTURE_ERROR",
    "PACKAGE_UNSCORABLE",
    "INFRASTRUCTURE_",
)


def presented_item(item: BankItem) -> PresentedItemDTO:
    """An item as a candidate may see it."""
    payload = item.payload or {}
    dto = PresentedItemDTO(
        item_id=item.item_id,
        modality=item.modality,
        competency=item.competency,
        sub_competency=item.sub_competency,
        estimated_time_seconds=item.expected_seconds,
    )
    if item.modality == "mcq":
        dto.stem = payload.get("stem") or payload.get("question") or ""
        dto.options = list(payload.get("options") or [])
    elif item.modality == "code":
        dto.prompt = payload.get("prompt") or payload.get("question") or ""
        dto.language = payload.get("language", "python")
        dto.function_name = payload.get("function_name", "solve")
        # A bank may provide a deliberately incomplete scaffold. `reference_solution` is
        # grader-only and must never cross this boundary; doing so turns every code item
        # into an answer key.
        dto.starter_code = payload.get("starter_code", "")
    elif item.modality in ("open", "voice"):
        dto.question = payload.get("question") or payload.get("prompt") or ""
        dto.answer_format = payload.get("answer_format") or "spoken"
        # Voice is answered by speaking; open may be typed. A client needs to know which
        # without inspecting a payload it is not allowed to see.
        dto.spoken = item.modality == "voice"
    return dto


def presenting(item: BankItem, candidate) -> PresentingDTO:
    return PresentingDTO(
        variable=candidate.variable,
        criterion=candidate.criterion,
        item=presented_item(item),
    )


def grade_receipt(graded: GradedResponse) -> GradeReceiptDTO:
    """Minimal acknowledgement, safe to return while the assessment is still running."""
    return GradeReceiptDTO(
        item_id=graded.item_id,
        modality=graded.modality,
        accepted=True,
        flags=[
            str(flag)
            for flag in graded.flags
            if str(flag).startswith(INFRASTRUCTURE_FLAG_PREFIXES)
        ],
    )


def report_dto(report: AssessmentReport) -> AssessmentReportDTO:
    return AssessmentReportDTO.model_validate(report.model_dump())


def scope_summary(scope: ScopeManifest | None) -> ScopeSummaryDTO | None:
    """What a REPORT says about the scope it was measured under.

    The identity and the honesty flags, not the whole manifest: the manifest is
    reproducible from the selection at any time, and inlining it would grow every report by
    the size of a bank's taxonomy for no reader.

    `partial_mains` is the field that matters. A partially scoped main is estimated from a
    corner of itself, which is exactly what the coverage gate prevents when it happens by
    accident. It is legitimate on purpose — but an estimate that does not say so can be
    compared against a whole-bank score, and the two do not mean the same thing.
    """
    if scope is None:
        return None
    return ScopeSummaryDTO(
        scope_id=scope.scope_id,
        scope_hash=scope.scope_hash,
        selected=list(scope.selected),
        partial_mains=[row.main for row in scope.mains if row.partial],
        retained_weight_by_main={row.main: row.retained_weight for row in scope.mains},
    )


# --- the catalogue views ------------------------------------------------------

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
        status=item.status,
    )


def item_full(item: BankItem) -> BankItemFull:
    """A ref plus the modality payload. For rendering and for grading."""
    return BankItemFull(
        # `status` now travels on the ref itself, so it arrives through this spread. Passing
        # it again here would be a duplicate keyword argument.
        **item_ref(item).model_dump(),
        competency=item.competency,
        sub_competency=item.sub_competency,
        payload=dict(item.payload or {}),
    )


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
                metadata=dict(node.metadata or {}),
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
                metadata=dict(edge.metadata or {}),
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
