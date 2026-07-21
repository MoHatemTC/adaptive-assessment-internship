"""The Grader Agent: turn a response into graded outcomes, by modality.

Its output is always `list[GradedOutcome]` — the only currency the measurement layer
accepts — so the orchestrator never learns what an answer index or a test case is.

WHAT "GRADER AGENT" DOES NOT MEAN

The architecture calls this an agent, and for code and open items a model is genuinely
involved. It is not involved in deciding whether the answer was RIGHT:

    MCQ    exact index comparison. There is a correct answer; nothing is gained by asking
           a model, and a model that disagreed would simply be wrong.
    code   the measured split. Sandboxed test execution owns functional correctness at
           100%, static analysis owns algorithm choice, and the model owns code quality
           and misconception diagnosis — effective authority tests 60% / static 15% /
           LLM 25%. This is the arm that was selected by measurement over an LLM-led one;
           routing code grading wholesale to a model would undo that decision.
    open   not implemented, and deliberately not guessed.

WEIGHT IS WHERE MODALITIES DIFFER MOST

An MCQ answer is worth a full observation. A code submission is worth `evidence_strength x
loading`: a submission that did not compile demonstrates a syntax problem rather than the
absence of every competency the question touches, and an infrastructure failure carries
zero and must move nothing at all.
"""

from __future__ import annotations

import logging

from app.schemas.orchestration import BankItem, GradedResponse
from app.services.code_adaptive.session import CodeAdaptiveSession
from app.services.orchestrator.outcome import GradedOutcome

logger = logging.getLogger(__name__)


class GraderAgent:
    """Routes a response to the grader its modality requires."""

    def __init__(self, code_engine: CodeAdaptiveSession | None = None) -> None:
        # Injected so tests can stub execution, and so a deployment can share one engine
        # (and therefore one weight profile) across every session.
        self._code = code_engine

    def grade(self, item: BankItem, response: object) -> GradedResponse:
        if item.modality == "mcq":
            return self._grade_mcq(item, response)
        if item.modality == "code":
            return self._grade_code(item, response)
        raise NotImplementedError(
            f"no grader for modality {item.modality!r} — open-ended grading is not implemented"
        )

    # --- mcq ---------------------------------------------------------------
    def _grade_mcq(self, item: BankItem, response: object) -> GradedResponse:
        """Exact index comparison. Never delegated."""
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

    # --- code --------------------------------------------------------------
    def _grade_code(self, item: BankItem, response: object) -> GradedResponse:
        """Delegate to the code engine's evaluation pipeline, unchanged.

        `evaluate()` already returns per-competency evidence carrying a score, a confidence
        and an evidence strength — exactly a graded outcome. The code engine's own Beta
        estimator is simply not used here: the estimate lives in Examinee Variables on the
        theta scale, and having two estimators for one competency would be two answers to
        one question.
        """
        if self._code is None:
            raise RuntimeError("no code engine configured — cannot grade a code item")
        if not isinstance(response, str):
            raise ValueError(f"{item.item_id}: code response must be source text")

        question = self.as_code_question(item)
        graded = self._code.evaluate(question, response)
        report = graded["report"]

        outcomes = [
            GradedOutcome(
                variable=evidence.competency_id,
                score=float(evidence.score),
                # Loading is already folded into the criterion->competency projection, so
                # multiplying by it again here would penalise a broad question twice.
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
        """Rebuild the shape the code engine expects from the unified envelope.

        The engine predates the unified bank and consumes its own question dict. Adapting
        here rather than changing the engine keeps the measured grading path byte-identical
        to the one the study validated.

        Public because trial runs need the same translation. Sharing it is the point: if
        the examples a candidate runs against came from a different translation than the
        one that grades them, the two could drift apart and the practice runs would stop
        predicting the graded one.
        """
        payload = dict(item.payload)
        payload["question_id"] = item.item_id
        payload["difficulty"] = payload.get("difficulty", 0.5)
        payload["competencies"] = [
            {"competency_id": m.variable, "weight": m.weight} for m in item.measures
        ]
        return payload
