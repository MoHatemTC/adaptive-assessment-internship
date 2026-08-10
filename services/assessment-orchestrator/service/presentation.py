"""The candidate boundary: what a client may see while an assessment is still running.

TWO THINGS ARE WITHHELD, FOR DIFFERENT REASONS

`reference_solution` and `answer_index` are withheld because a client that could read them
could harvest the bank. That one is obvious.

The grading DETAIL is withheld because feedback after each answer changes what the
assessment measures. A candidate told "wrong, the hidden test that failed was the empty-list
case" learns something between question four and question five, and the estimate that comes
out is of a person who was being taught mid-measurement. The engine's own audit record
keeps all of it; this boundary is about what goes back over the wire before the session
ends.

WHAT IS NOT WITHHELD

That the sandbox fell over. The candidate's answer moved nothing and they may be asked
again — telling them so costs the measurement nothing and not telling them is just
confusing.
"""

from __future__ import annotations

from adaptive_contracts import (
    AssessmentReportDTO,
    GradeReceiptDTO,
    PresentedItemDTO,
    PresentingDTO,
)
from app.schemas.orchestration import AssessmentReport, BankItem, GradedResponse

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
