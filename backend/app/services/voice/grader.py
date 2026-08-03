"""Sync pure grader — package + evaluation → GradedOutcome list. No I/O."""

from __future__ import annotations

from app.schemas.orchestration import GradedResponse, Modality
from app.schemas.voice import GradedVoiceResponse
from app.services.orchestrator.outcome import GradedOutcome
from app.services.voice.evidence import project_competency_evidence


def grade_voice(
    graded: GradedVoiceResponse, *, modality: Modality = "open"
) -> GradedResponse:
    """Turn a pre-evaluated voice response into GradedOutcomes.

    Loading is NOT multiplied again — it is folded into the criterion→competency
    projection, matching `_grade_code`.

    `modality` is carried through to every outcome so a report can say whether the answer
    was spoken or typed. The grading itself is identical for both: same evaluator, same
    rubric criteria, same projection. Defaulting to "open" keeps every existing caller.
    """
    package = graded.package
    evaluation = graded.evaluation
    evidence = project_competency_evidence(package, evaluation, graded.rubric)

    outcomes = [
        GradedOutcome(
            variable=e.competency_id,
            score=float(e.score),
            weight=min(max(float(e.evidence_strength) * float(e.confidence), 0.0), 1.0)
            if e.evidence_strength > 0
            else 0.0,
            confidence=float(e.confidence),
            source_item_id=package.item_id,
            modality=modality,
        )
        for e in evidence
    ]

    # If unscorable / zero evidence, still emit zero-weight outcomes for measured vars
    if not outcomes and graded.rubric.get("criteria"):
        seen = {c["competency_id"] for c in graded.rubric["criteria"]}
        outcomes = [
            GradedOutcome(
                variable=cid,
                score=0.0,
                weight=0.0,
                confidence=0.0,
                source_item_id=package.item_id,
                modality=modality,
            )
            for cid in sorted(seen)
        ]

    return GradedResponse(
        item_id=package.item_id,
        modality=modality,
        outcomes=[o.__dict__ for o in outcomes],
        detail={
            "outcome_status": package.outcome_status,
            "reason_code": package.reason_code,
            "evaluation_confidence": evaluation.evaluation_confidence,
            "degraded": evaluation.degraded,
            "flags": evaluation.flags,
            "rationale": evaluation.overall_rationale,
            "transcript_preview": package.transcript[:400],
            "competency_evidence": [e.model_dump() for e in evidence],
        },
        flags=list(evaluation.flags),
    )
