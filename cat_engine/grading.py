"""Turning one response into graded outcomes, per modality.

WHAT SURVIVED THE SERVICE BOUNDARY, AND WHAT IT WAS FOR

`grader` was the only component with a sandbox and the only one that fetched its own item.
The fetch existed so the answer key, the hidden tests and the rubric never transited the
orchestrator — whose own responses reach a candidate's browser. In one process there is no
transit to avoid, but the property that made it safe is worth keeping deliberately rather
than losing by accident: NOTHING here returns a payload. `grade_*` return `GradedResponse`,
whose `detail` is the audit record, and `presentation.grade_receipt` is what a candidate
sees — an acknowledgement with no score in it.

THE SANDBOX IS STILL THE ONLY THING THAT EXECUTES ANYTHING

Untrusted candidate code runs in E2B, never in this process. That was never the service
boundary providing it — it is `code_adaptive` calling out — so collapsing to one module
does not change it. What the boundary did provide was the guarantee that no other component
could IMPORT the sandbox, and that is now a test rather than a deployment fact; see
`tests/test_module_boundaries.py`.

TRIAL RUNS GRADE NOTHING

`trial_run` exists because otherwise the first time a candidate's code ever executes is the
moment it is graded, which measures something other than competency. There is no code path
from a trial into a learner model — not an unused one, none at all.
"""

from __future__ import annotations

import logging

from cat_engine.contracts import BankItemFull, TrialCaseDTO, TrialRunResponse
from cat_engine.engine.schemas.orchestration import BankItem, GradedResponse
from cat_engine.engine.schemas.voice import VoiceResponsePackage
from cat_engine.engine.services.code_adaptive import (
    CodeAdaptiveSession,
    JsonQuestionRepository,
)
from cat_engine.engine.services.code_adaptive import trial as code_trial
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.voice.evaluator import evaluate as evaluate_voice
from cat_engine.errors import AnswerInvalid, AnswerTypeMismatch, BankUnknown

logger = logging.getLogger(__name__)

__all__ = [
    "Grader",
    "to_bank_item",
]


def to_bank_item(full: BankItemFull) -> BankItem:
    """A wire item into the engine's own envelope.

    The wire form carries one `payload` field whatever the modality; the engine nests it
    under the modality name, which is what lets `BankItem`'s validator say "this claims to
    be code and has no code payload". Kept because a host may hand back an item it received
    as a DTO — from a bank it uploaded, say — and the translation belongs at the boundary
    rather than in either definition.
    """
    data = full.model_dump()
    payload = data.pop("payload", {}) or {}
    data[full.modality] = payload
    return BankItem.model_validate(data)


class Grader:
    """The Grader Agent plus the item lookup it used to do over HTTP.

    One per module. The code engine is constructed once because `CodeAdaptiveSession`
    carries the question repository and the sandbox client, and building one per response
    would pay that cost on every code answer.
    """

    def __init__(self) -> None:
        self._agent = GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))

    # --- the agent itself, for the orchestrator -----------------------------
    @property
    def agent(self) -> GraderAgent:
        """The `GraderAgent` the orchestrator grades through.

        Handed over rather than wrapped: `Orchestrator.record_response` calls `.grade(item,
        response)` and nothing else, and interposing here would put a second implementation
        of the modality routing between the loop and the thing that does it.
        """
        return self._agent

    # --- item lookup --------------------------------------------------------
    def item(self, bank_id: str | None, item_id: str, *expected: str) -> BankItem:
        """The item this response is about, refusing a modality mismatch.

        A mismatch means the caller graded the wrong thing, and grading an MCQ as code
        produces a confident zero rather than an error — so it is refused rather than
        attempted.
        """
        resolved = registry.resolve_bank_id(bank_id)
        found = registry.get_bank(resolved).get(item_id)
        if found is None:
            raise BankUnknown(f"no item {item_id} in bank {resolved}")
        if expected and found.modality not in expected:
            raise AnswerTypeMismatch(
                f"{item_id} is a {found.modality} item; this grades {list(expected)}"
            )
        return found

    # --- grading, per modality ---------------------------------------------
    def grade_mcq(self, bank_id: str | None, item_id: str, chosen_index: int) -> GradedResponse:
        """Exact index comparison.

        No model is involved, and none ever will be — a model that graded multiple choice
        could disagree with the bank about its own answer key.
        """
        item = self.item(bank_id, item_id, "mcq")
        try:
            return self._agent.grade(item, chosen_index)
        except (TypeError, ValueError) as exc:
            raise AnswerInvalid(str(exc)) from exc

    def grade_code(self, bank_id: str | None, item_id: str, source: str) -> GradedResponse:
        """Tests 60%, static analysis 15%, model 25% — the measured split.

        A sandbox failure produces outcomes of weight 0, which move no estimate: an
        infrastructure fault is not evidence about a candidate.
        """
        item = self.item(bank_id, item_id, "code")
        try:
            return self._agent.grade(item, source)
        except (TypeError, ValueError) as exc:
            raise AnswerInvalid(str(exc)) from exc

    async def grade_open(
        self,
        bank_id: str | None,
        item_id: str,
        package: VoiceResponsePackage,
        *,
        use_llm: bool = True,
    ) -> GradedResponse:
        """Both halves: rubric evaluation, which is a model call, then the projection.

        `open` and `voice` grade identically — the modality records how the answer was
        collected, not how it was judged.
        """
        item = self.item(bank_id, item_id, "open", "voice")
        return self._agent.grade(item, await self.evaluate(item, package, use_llm=use_llm))

    @staticmethod
    async def evaluate(item: BankItem, package: VoiceResponsePackage, *, use_llm: bool = True):
        """Rubric evaluation on its own, which the assessment loop needs separately.

        THIS IS THE ONE ORDERING THE COLLAPSE HAD TO GET RIGHT.

        `GraderAgent._grade_open` requires an already-evaluated `GradedVoiceResponse` and
        raises `TypeError` otherwise, because evaluation is async and
        `Orchestrator.record_response` is sync. Behind HTTP that was invisible: the grader
        service awaited this itself before grading, so the orchestrator handed over a raw
        package and got outcomes back. In one process the await has to happen at the call
        site instead — see `facade._coerce_answer`.
        """
        return await evaluate_voice(item, package, use_llm=use_llm)

    # --- transcription ------------------------------------------------------
    @staticmethod
    def transcribe(audio: bytes, *, filename: str = "answer.wav") -> str:
        """Recorded audio to text.

        Separate from grading because the two fail differently: a transcription failure is a
        retry, a grading failure is an unscorable response that must move no estimate.
        Folding them together would make the second look like the first.
        """
        from cat_engine.engine.services.voice_live.transcribe import transcribe_audio_bytes

        if not audio:
            raise AnswerInvalid("empty audio payload", code="audio_empty")
        return transcribe_audio_bytes(audio, filename=filename).strip()

    # --- trial runs, which grade nothing ------------------------------------
    def public_tests(self, bank_id: str | None, item_id: str) -> list[dict]:
        """The example cases a candidate may run against.

        Public cases only, and the filter lives in the engine's trial module so a caller
        cannot widen it. A case nobody explicitly marked public is treated as hidden: an
        authoring slip must cost a candidate one example rather than void the question.
        """
        question = GraderAgent.as_code_question(self.item(bank_id, item_id, "code"))
        return code_trial.public_tests(question)

    def trial_run(self, bank_id: str | None, item_id: str, source: str) -> TrialRunResponse:
        """Run a candidate's code against the public cases. Grades nothing.

        `available: false` means the SANDBOX failed. It is not a statement about the
        candidate's code, and nothing here reaches a posterior.
        """
        question = GraderAgent.as_code_question(self.item(bank_id, item_id, "code"))
        result = code_trial.trial_run(question, source)
        return TrialRunResponse(
            available=result.available,
            compiled=result.compiled,
            cases=[
                TrialCaseDTO(
                    test_id=case.test_id,
                    arguments=list(case.arguments),
                    expected=case.expected,
                    passed=case.passed,
                    detail=case.detail,
                )
                for case in result.cases
            ],
            error_message=result.error_message,
            passed=result.passed,
            total=result.total,
        )
