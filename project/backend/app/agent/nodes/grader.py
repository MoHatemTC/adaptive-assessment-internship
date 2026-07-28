from supabase import AsyncClient
from app.agent.state import AgentState
from app.services.grading import grade_open_ended, grade_mcq, grade_code


async def grade_answer(state: AgentState, db: AsyncClient) -> dict:
    """Grade the pending answer and return grading result."""
    pending = state.get("pending_answer", {})
    question_id = pending.get("question_id")
    answer_text = pending.get("answer_text", "")
    answer_data = pending.get("answer_data", {})
    question_type = pending.get("question_type", "open_ended")
    skill = pending.get("skill", "thinking")
    rubric = pending.get("rubric")
    answer_key = pending.get("answer_key")
    question_body = pending.get("question_body", "")

    if question_type == "mcq":
        result = grade_mcq(answer_key or {}, answer_data.get("selected_id", ""))
    elif question_type == "coding":
        result = await grade_code(answer_data.get("test_results", {}), answer_text)
    else:
        result = await grade_open_ended(question_body, rubric, answer_text, skill)

    skill_scores = result.get("skill_scores", {})
    if not skill_scores.get(skill):
        skill_scores[skill] = result.get("overall_score", 0)

    return {
        "question_id": question_id,
        "skill": skill,
        "skill_scores": skill_scores,
        "score": result.get("overall_score", 0),
        "rationale": result.get("rationale", ""),
    }
