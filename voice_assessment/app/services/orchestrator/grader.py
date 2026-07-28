"""The Grader Agent: turn a response into graded outcomes, by modality.

Voice / open items arrive pre-evaluated as GradedVoiceResponse — evaluation is async and
happens BEFORE this sync path, so orchestrator.record_response stays untouched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
        if item.modality == "open":
            return self._grade_open(item, response)
        raise NotImplementedError(f"no grader for modality {item.modality!r}")

    def _grade_open(self, item: BankItem, response: object) -> GradedResponse:
        """Sync. Expects a GradedVoiceResponse produced by VoiceResponseEvaluator.evaluate."""
        if not isinstance(response, GradedVoiceResponse):
            raise TypeError(
                f"{item.item_id}: open response must be GradedVoiceResponse "
                f"(got {type(response).__name__}) — evaluate() first"
            )
        return grade_voice(response)

    def _grade_mcq(self, item: BankItem, response: object) -> GradedResponse:
        try:
            chosen = int(response)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"{item.item_id}: MCQ response must be an option index") from None

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
            detail={"chosen_index": chosen, "correct": bool(score), "answer_index": answer_index},
        )

    def _grade_code(self, item: BankItem, response: object) -> GradedResponse:
        if self._code is None:
            raise RuntimeError("no code engine configured — cannot grade a code item")
        if not isinstance(response, str):
            raise ValueError(f"{item.item_id}: code response must be source text")
        raise NotImplementedError(
            "code grading is not part of the voice standalone package — use the combined engine"
        )
