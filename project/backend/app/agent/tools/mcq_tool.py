from app.agent.state import AgentState


def build_mcq_payload(question: dict) -> dict:
    """Format a question row into the payload the MCQWidget expects."""
    return {
        "question_id": question["id"],
        "question_body": question["body"],
        "skill": question["skill"],
        "difficulty": question["difficulty"],
        "options": question.get("options") or [],
        "answer_key": question.get("answer_key"),
        "rubric": question.get("rubric"),
        "question_type": "mcq",
    }


def extract_mcq_answer(tool_result: dict) -> dict:
    """Convert MCQWidget submission into a pending_answer dict."""
    return {
        "question_id": tool_result.get("question_id"),
        "question_body": tool_result.get("question_body", ""),
        "question_type": "mcq",
        "skill": tool_result.get("skill"),
        "answer_text": tool_result.get("selected_label", ""),
        "answer_data": {"selected_id": tool_result.get("selected_id", "")},
        "answer_key": tool_result.get("answer_key"),
        "rubric": tool_result.get("rubric"),
    }
