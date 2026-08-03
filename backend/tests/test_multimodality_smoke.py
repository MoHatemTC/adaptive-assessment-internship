"""Smoke tests for mixed-modality CAT wiring (mcq + code + open)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.schemas.orchestration import BankItem
from app.services.orchestrator import registry
from app.services.orchestrator.grader import GraderAgent
from app.services.voice.evaluator import evaluate, package_from_text
from app.services.voice.grader import grade_voice


@dataclass
class _FakeEvidence:
    competency_id: str
    score: float
    confidence: float
    evidence_strength: float


class _FakeReport:
    flags = ["FAKE_CODE_PATH"]

    def model_dump(self):
        return {"overall_score": 0.7}


class _FakeCodeEngine:
    def evaluate(self, question: dict, code: str):
        assert question["question_id"]
        assert isinstance(code, str)
        return {
            "report": _FakeReport(),
            "competency_evidence": [
                _FakeEvidence(
                    competency_id=question["competencies"][0]["competency_id"],
                    score=0.75,
                    confidence=0.9,
                    evidence_strength=0.8,
                )
            ],
        }


def test_bank_contains_all_three_modalities():
    items = registry.get_bank("DA").all_items()
    modalities = {i.modality for i in items}
    assert {"mcq", "code", "open"}.issubset(modalities)


def test_open_items_carry_voice_or_inline_rubric():
    opens = [i for i in registry.get_bank("DA").all_items() if i.modality == "open"]
    assert opens
    for item in opens:
        payload = item.payload
        assert payload.get("prompt") or payload.get("question")
        has_rubric_id = bool(payload.get("rubric_id"))
        has_inline = bool(payload.get("rubric_criteria"))
        assert has_rubric_id or has_inline


def test_open_heuristic_path_grades_without_llm():
    item = next(i for i in registry.get_bank("DA").all_items() if i.modality == "open")
    package = package_from_text(
        item.item_id,
        "Lists are mutable while tuples are immutable. I use tuples for fixed records.",
    )
    graded_voice = asyncio.run(evaluate(item, package, use_llm=False))
    graded = grade_voice(graded_voice)
    assert graded.modality == "open"
    assert graded.outcomes


def test_code_grader_uses_injected_code_engine():
    item: BankItem = next(i for i in registry.get_bank("DA").all_items() if i.modality == "code")
    grader = GraderAgent(code_engine=_FakeCodeEngine())  # type: ignore[arg-type]
    graded = grader.grade(item, "def solve():\n    return 1\n")
    assert graded.modality == "code"
    assert graded.outcomes
    assert graded.flags == ["FAKE_CODE_PATH"]
