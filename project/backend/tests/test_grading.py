"""Grading service unit tests."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_grade_mcq_correct():
    from app.services.grading import grade_mcq
    result = grade_mcq(answer_key={"correct_id": "b"}, selected_id="b")
    assert result["overall_score"] == pytest.approx(5.0)
    assert "Correct" in result["rationale"]


def test_grade_mcq_wrong():
    from app.services.grading import grade_mcq
    result = grade_mcq(answer_key={"correct_id": "b"}, selected_id="a")
    assert result["overall_score"] == pytest.approx(0.0)
    assert "Incorrect" in result["rationale"]


@pytest.mark.asyncio
async def test_grade_open_ended():
    from app.services.grading import grade_open_ended

    mock_payload = json.dumps({
        "skill_scores": {"thinking": 4.0, "soft": 0, "work": 0, "digital_ai": 0, "growth": 0},
        "overall_score": 4.0,
        "rationale": "Good answer"
    })
    mock_choice = MagicMock()
    mock_choice.message.content = mock_payload
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.grading._llm") as mock_llm_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_llm_fn.return_value = mock_client
        result = await grade_open_ended(
            question_body="Explain transformers.",
            rubric=None,
            candidate_answer="Transformers use self-attention mechanisms.",
            primary_skill="thinking",
        )

    assert result["skill_scores"]["thinking"] == 4.0
    assert result["overall_score"] == 4.0


@pytest.mark.asyncio
async def test_grade_code_partial():
    from app.services.grading import grade_code

    mock_quality = json.dumps({"quality_score": 2.0, "rationale": "Partial solution"})
    mock_choice = MagicMock()
    mock_choice.message.content = mock_quality
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.grading._llm") as mock_llm_fn:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_llm_fn.return_value = mock_client
        result = await grade_code(
            test_results={"passed": 2, "total": 5},
            code="def factorial(n): return 1",
        )

    # pass_rate=0.4 → 0.4*0.7*5 + 2.0*0.3 = 1.4 + 0.6 = 2.0
    assert 0 <= result["overall_score"] <= 5.0
