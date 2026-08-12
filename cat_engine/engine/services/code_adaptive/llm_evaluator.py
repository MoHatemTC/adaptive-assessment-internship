"""LLM competency diagnosis (section 8), with the validation that makes it safe to use.

The model's job is INTERPRETATION: why a submission failed, which misconception it shows,
whether the algorithm chosen was appropriate. It is given every objective signal and is
forbidden from contradicting any of them.

Validation is not a formality here. An unchecked reply can name a competency the question
does not assess, cite a test that was never run, or claim correctness that execution
disproves — and each of those becomes a wrong number attached to a real candidate. Every
one is rejected in `validate`, and rejection degrades to the objective evidence rather
than failing the submission.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cat_engine.engine.config.settings import CodeRubric as Rubric
from cat_engine.engine.services.code_adaptive import prompts as rubric_module
from cat_engine.engine.services.code_adaptive.execution import ExecutionEvidence
from cat_engine.engine.services.code_adaptive.llm import LLMUnavailable, chat_json
from cat_engine.engine.services.code_adaptive.static_analysis import StaticSignals

logger = logging.getLogger(__name__)

EVALUATION_SYSTEM = """You diagnose a learner's programming competencies from their code
and the objective evidence of running it.

You are given the problem, the learner's code, which tests passed and failed, compiler and
runtime output, and structural analysis. Your job is to explain WHAT THE EVIDENCE MEANS
about the learner's competencies. It is not to decide whether the code works — that has
already been measured.

HARD RULES. A response breaking any of these is discarded entirely:
- Never contradict a test result. If tests failed, the code failed.
- Never claim full functional correctness when any test failed.
- Never invent a test, a test id, or an execution result.
- Only score the criterion ids and competency ids given to you.
- Every score must cite evidence: a specific failed test, or a specific span of the code.

Score each criterion from 0.0 to 1.0, where 0 is no evidence of the competency and 1 is
fully demonstrated.

Return JSON only:
{
  "evaluation_confidence": 0.0-1.0,
  "criterion_evidence": [
    {
      "criterion_id": "<from the given list>",
      "competency_id": "<from the given list>",
      "score": 0.0-1.0,
      "confidence": 0.0-1.0,
      "evidence": [{"type": "failed_test|code_span|test_summary",
                    "reference": "<test id or line range>",
                    "description": "<what it shows>"}],
      "misconception_code": "<code or null>"
    }
  ],
  "overall_diagnostic": "<one or two sentences>"
}
"""

KNOWN_MISCONCEPTIONS = {
    "MISSING_EMPTY_INPUT_GUARD",
    "OFF_BY_ONE_BOUNDARY",
    "WRONG_DATA_STRUCTURE",
    "QUADRATIC_WHEN_LINEAR_POSSIBLE",
    "HARDCODED_OUTPUT",
    "SYNTAX_ERROR",
    "MUTATES_INPUT",
    "IGNORES_RETURN_CONTRACT",
}


@dataclass
class LLMCriterionEvidence:
    criterion_id: str
    competency_id: str
    score: float
    confidence: float
    misconception_code: str | None = None
    evidence: list[dict] = field(default_factory=list)


@dataclass
class LLMEvaluation:
    """Validated model output. `available` is False whenever it may not be used."""

    available: bool
    evaluation_confidence: float = 0.0
    criterion_evidence: list[LLMCriterionEvidence] = field(default_factory=list)
    overall_diagnostic: str = ""
    flags: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def score_for(self, criterion_id: str) -> tuple[float | None, float]:
        for item in self.criterion_evidence:
            if item.criterion_id == criterion_id:
                return item.score, item.confidence
        return None, 0.0


def build_payload(
    question: dict,
    code: str,
    evidence: ExecutionEvidence,
    signals: StaticSignals,
    rubric: dict | None = None,
) -> dict:
    """Everything the model may reason from — and nothing it could use to guess.

    The failing tests are named and described, because that is what the model is being
    asked to interpret. Expected VALUES are not included: the model does not need them to
    diagnose a misconception, and supplying them invites it to grade rather than diagnose.
    """
    rubric = rubric or rubric_module.load()
    return {
        "rubric": rubric_module.render(
            rubric, [c["criterion_id"] for c in question["rubric_criteria"]]
        ),
        "question": {
            "title": question["title"],
            "prompt": question["prompt"],
            "allowed_competency_ids": [
                c["competency_id"] for c in question["competencies"]
            ],
            "allowed_criterion_ids": [
                c["criterion_id"] for c in question["rubric_criteria"]
            ],
        },
        "learner_code": code,
        "objective_evidence": {
            "compiled": evidence.compiled,
            "compile_error": evidence.error_message if not evidence.compiled else "",
            "passed_tests": evidence.passed_tests,
            "total_tests": evidence.total_tests,
            "passed_test_ratio": round(evidence.passed_test_ratio, 3),
            "timeout": evidence.timeout,
            "failed_tests": [
                {
                    "test_id": o.test_id,
                    "failure_type": o.failure_type,
                    "detail": o.detail,
                }
                for o in evidence.test_results
                if not o.passed
            ],
            "passed_test_ids": [o.test_id for o in evidence.test_results if o.passed],
        },
        "static_analysis": signals.as_dict(),
        "known_misconception_codes": sorted(KNOWN_MISCONCEPTIONS),
    }


def validate(reply: dict, question: dict, evidence: ExecutionEvidence) -> LLMEvaluation:
    """Reject everything the model was not entitled to say; keep the rest.

    Invalid entries are dropped individually rather than discarding the whole reply: one
    hallucinated competency should not throw away three sound diagnoses. The exception is
    a functional-correctness overclaim, which is corrected in place and flagged, because
    silently dropping it would leave the criterion unscored and look like model silence.
    """
    allowed_competencies = {c["competency_id"] for c in question["competencies"]}
    allowed_criteria = {c["criterion_id"] for c in question["rubric_criteria"]}
    real_test_ids = {o.test_id for o in evidence.test_results}
    flags: list[str] = []

    kept: list[LLMCriterionEvidence] = []
    for raw in reply.get("criterion_evidence") or []:
        if not isinstance(raw, dict):
            continue
        criterion = str(raw.get("criterion_id", ""))
        competency = str(raw.get("competency_id", ""))

        if criterion not in allowed_criteria:
            flags.append(f"UNKNOWN_CRITERION: {criterion!r}")
            continue
        if competency not in allowed_competencies:
            flags.append(f"UNKNOWN_COMPETENCY: {competency!r}")
            continue

        try:
            score_raw = raw.get("score")
            if score_raw is None:
                raise ValueError("missing score")
            score = float(score_raw)
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            flags.append(f"UNPARSEABLE_SCORE: {criterion}")
            continue
        if not (0.0 <= score <= 1.0 and 0.0 <= confidence <= 1.0):
            flags.append(f"SCORE_OUT_OF_RANGE: {criterion}")
            continue

        cited = [e for e in (raw.get("evidence") or []) if isinstance(e, dict)]

        def cites_unknown_test(entry: dict) -> bool:
            """True only when a cited test id does not exist.

            A reference may name SEVERAL tests in one string — models routinely write
            "tc_empty, tc_single, tc_zeros" rather than three separate evidence entries.
            Treating the whole string as one id made every such citation look fabricated:
            measured at 10 of 13 INVENTED_TEST_EVIDENCE flags across a 540-run grid, each
            of which discarded a sound diagnosis and depressed the branch's score for
            formatting rather than for substance.
            """
            if entry.get("type") != "failed_test":
                return False
            raw_reference = str(entry.get("reference", ""))
            parts = {
                p.strip()
                for p in raw_reference.replace(";", ",").split(",")
                if p.strip()
            }
            return not parts or not parts <= real_test_ids

        invented = [e.get("reference") for e in cited if cites_unknown_test(e)]
        if invented:
            # Fabricated evidence invalidates the claim it supports. Keeping the score
            # while discarding its justification would preserve exactly the number that
            # the fabrication was invented to defend.
            flags.append(f"INVENTED_TEST_EVIDENCE: {criterion} cited {invented[:2]}")
            continue

        if (
            criterion == "functional_correctness"
            and evidence.total_tests
            and score > evidence.passed_test_ratio + 0.15
        ):
            flags.append(
                f"LLM_OBJECTIVE_CONFLICT: functional_correctness {score:.2f} > measured "
                f"{evidence.passed_test_ratio:.2f} (objective source won)"
            )
            score = evidence.passed_test_ratio

        misconception = raw.get("misconception_code")
        if misconception and misconception not in KNOWN_MISCONCEPTIONS:
            flags.append(f"UNKNOWN_MISCONCEPTION: {misconception!r}")
            misconception = None

        kept.append(
            LLMCriterionEvidence(
                criterion, competency, score, confidence, misconception, cited
            )
        )

    try:
        overall_confidence = float(reply.get("evaluation_confidence", 0.0))
    except (TypeError, ValueError):
        overall_confidence = 0.0

    usage = reply.get("_usage") or {}
    return LLMEvaluation(
        available=bool(kept),
        evaluation_confidence=max(0.0, min(overall_confidence, 1.0)),
        criterion_evidence=kept,
        overall_diagnostic=str(reply.get("overall_diagnostic", ""))[:500],
        flags=flags,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def evaluate(
    question: dict,
    code: str,
    evidence: ExecutionEvidence,
    signals: StaticSignals,
    rubric_id: Rubric | None = None,
) -> LLMEvaluation:
    """Diagnose competencies from the submission. Never raises."""
    rubric = rubric_module.load(rubric_id)
    try:
        reply = chat_json(
            EVALUATION_SYSTEM,
            json.dumps(
                build_payload(question, code, evidence, signals, rubric), indent=2
            ),
            require=("criterion_evidence",),
        )
    except LLMUnavailable as exc:
        logger.warning("llm evaluation unavailable (%s) — objective evidence only", exc)
        return LLMEvaluation(available=False, flags=[f"LLM_UNAVAILABLE: {exc}"])

    result = validate(reply, question, evidence)
    for flag in result.flags:
        logger.warning("llm evaluation flag: %s", flag)
    return result
