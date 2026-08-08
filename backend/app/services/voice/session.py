"""Voice assessment session runner — wraps the vendored Orchestrator for open items."""

from __future__ import annotations

import logging
import time

import numpy as np

from app.config.voice_settings import voice_settings
from app.schemas.orchestration import AssessmentState, BankItem
from app.schemas.voice import GradedVoiceResponse
from app.services.orchestrator.bank import JsonUnifiedBank
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.voice import evaluator as voice_evaluator

logger = logging.getLogger(__name__)


class VoiceAssessmentRunner:
    """Drives a multi-competency open/voice assessment."""

    def __init__(
        self,
        bank: JsonUnifiedBank | None = None,
        *,
        bank_id: str | None = None,
        seed: int | None = None,
    ) -> None:
        from app.services.orchestrator import registry

        resolved = registry.resolve_bank_id(bank_id)
        self.bank_id = resolved
        self.bank = bank or registry.get_bank(resolved)
        self.orchestrator = Orchestrator(
            self.bank,
            GraderAgent(),
            graph=registry.get_graph_service(resolved),
            coverage_critical_only=registry.profile(resolved).coverage_critical_only,
            bank_id=resolved,
        )
        self._started = 0.0
        self._rng = np.random.default_rng(seed)

    def begin(
        self,
        competencies: list[str],
        intake: dict[str, int] | None = None,
        confidence: dict[str, bool] | None = None,
    ) -> AssessmentState:
        # Main competencies only (C1, C2, …)
        state = self.orchestrator.begin(
            competencies, intake=intake or {}, confidence=confidence or {}
        )
        self._started = time.time()
        return state

    async def fill_queue(
        self, state: AssessmentState, *, use_llm: bool = True, seed: int | None = None
    ) -> AssessmentState:
        rng = self._rng if seed is None else np.random.default_rng(seed)
        return await self.orchestrator.fill_queue(state, use_llm=use_llm, rng=rng)

    def ensure_presenting(self, state: AssessmentState) -> AssessmentState:
        return self.orchestrator.ensure_presenting(state)

    def next_item(self, state: AssessmentState):
        return self.orchestrator.next_item(state)

    def time_remaining_minutes(self) -> float:
        elapsed = (time.time() - self._started) / 60.0 if self._started else 0.0
        return max(voice_settings.voice_session_time_limit_minutes - elapsed, 0.0)

    def should_stop(self, state: AssessmentState) -> tuple[bool, str]:
        if self.time_remaining_minutes() <= 0:
            return True, "time_limit"
        return self.orchestrator.should_stop(state)

    async def submit_text(
        self,
        state: AssessmentState,
        item: BankItem,
        text: str,
        *,
        use_llm: bool = True,
        seed: int | None = None,
    ) -> tuple[AssessmentState, GradedVoiceResponse, object]:
        """Evaluate a typed transcript, then record via the vendored orchestrator."""
        package = voice_evaluator.package_from_text(item.item_id, text)
        return await self.submit_package(
            state, item, package, use_llm=use_llm, seed=seed
        )

    async def submit_package(
        self,
        state: AssessmentState,
        item: BankItem,
        package,
        *,
        use_llm: bool = True,
        seed: int | None = None,
    ) -> tuple[AssessmentState, GradedVoiceResponse, object]:
        """Evaluate a VoiceResponsePackage (from Live or text), then record."""
        graded_voice = await voice_evaluator.evaluate(item, package, use_llm=use_llm)
        new_state, graded = self.orchestrator.record_response(state, item, graded_voice)
        rng = self._rng if seed is None else np.random.default_rng(seed)
        new_state = await self.orchestrator.after_response(
            new_state, item, use_llm=use_llm, rng=rng
        )
        return new_state, graded_voice, graded

    def summarise(self, state: AssessmentState, stop_reason: str = ""):
        return self.orchestrator.summarise(state, stop_reason)
