"""The Grader Agent: turn a response into graded outcomes, by modality.

Voice / open items arrive pre-evaluated as GradedVoiceResponse — evaluation is async and
happens BEFORE this sync path, so orchestrator.record_response stays untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from app.schemas.orchestration import BankItem, GradedResponse
from app.schemas.voice import GradedVoiceResponse
from app.services.orchestrator.outcome import GradedOutcome
from app.services.voice.grader import grade_voice

if TYPE_CHECKING:
    from app.services.code_adaptive.session import CodeAdaptiveSession

logger = logging.getLogger(__name__)


class GraderAgent:
    """Routes a response to the grader its modality requires."""

    def __init__(self, code_engine: CodeAdaptiveSession | None = None) -> None:
        self._code = code_engine

    def grade(self, item: BankItem, response: object) -> GradedResponse:
        if item.modality == "mcq":
            return self._grade_mcq(item, response)
        if item.modality == "code":
            return self._grade_code(item, response)
        if item.modality in ("open", "voice"):
            return self._grade_open(item, response)
        raise NotImplementedError(f"no grader for modality {item.modality!r}")

    def _grade_open(self, item: BankItem, response: object) -> GradedResponse:
        """Sync. Expects a GradedVoiceResponse produced by VoiceResponseEvaluator.evaluate.

        `open` and `voice` grade identically — the modality only decides how the answer was
        collected and how the report describes it.
        """
        if not isinstance(response, GradedVoiceResponse):
            raise TypeError(
                f"{item.item_id}: {item.modality} response must be GradedVoiceResponse "
                f"(got {type(response).__name__}) — evaluate() first"
            )
        return grade_voice(response, modality=item.modality)

    def _grade_mcq(self, item: BankItem, response: object) -> GradedResponse:
        try:
            chosen = int(cast(Any, response))
        except (TypeError, ValueError):
            raise ValueError(
                f"{item.item_id}: MCQ response must be an option index"
            ) from None

        answer_index = int(item.payload["answer_index"])
        options = item.payload.get("options") or []
        if not 0 <= chosen < len(options):
            raise ValueError(
                f"{item.item_id}: option index {chosen} out of range for {len(options)} options"
            )

        score = 1.0 if chosen == answer_index else 0.0
        outcomes = [
            GradedOutcome(
                variable=entry.variable,
                score=score,
                weight=entry.weight,
                confidence=1.0,
                source_item_id=item.item_id,
                modality="mcq",
            )
            for entry in item.measures
        ]
        return GradedResponse(
            item_id=item.item_id,
            modality="mcq",
            outcomes=[o.__dict__ for o in outcomes],
            detail={
                "chosen_index": chosen,
                "correct": bool(score),
                "answer_index": answer_index,
            },
        )

    def _grade_code(self, item: BankItem, response: object) -> GradedResponse:
        if self._code is None:
            raise RuntimeError("no code engine configured — cannot grade a code item")
        if not isinstance(response, str):
            raise TypeError(f"{item.item_id}: code response must be source text")

        question = self.as_code_question(item)
        graded = self._code.evaluate(question, response)
        report = graded["report"]

        outcomes = [
            GradedOutcome(
                variable=evidence.competency_id,
                score=float(evidence.score),
                # Loading is already folded into criterion->competency projection, so do
                # not multiply by it again here.
                weight=min(max(float(evidence.evidence_strength), 0.0), 1.0),
                confidence=float(evidence.confidence),
                source_item_id=item.item_id,
                modality="code",
            )
            for evidence in graded["competency_evidence"]
        ]
        return GradedResponse(
            item_id=item.item_id,
            modality="code",
            outcomes=[o.__dict__ for o in outcomes],
            detail=report.model_dump(),
            flags=list(report.flags),
        )

    @staticmethod
    def as_code_question(item: BankItem) -> dict:
        """Rebuild the shape CodeAdaptiveSession expects from unified envelope."""
        payload = dict(item.payload)
        payload["question_id"] = item.item_id
        payload["difficulty"] = payload.get("difficulty", 0.5)
        payload["competencies"] = [
            {"competency_id": m.variable, "weight": m.weight} for m in item.measures
        ]
        return payload
