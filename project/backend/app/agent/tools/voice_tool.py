def build_voice_payload(question: dict, time_limit_minutes: int = 10, mode: str = "technical") -> dict:
    return {
        "question_id": question["id"],
        "question_body": question["body"],
        "skill": question["skill"],
        "difficulty": question["difficulty"],
        "rubric": question.get("rubric"),
        "question_type": "voice",
        "mode": mode,                           # "technical" | "hr"
        "time_limit_seconds": time_limit_minutes * 60,
    }


def extract_voice_answer(tool_result: dict) -> dict:
    return {
        "question_id": tool_result.get("question_id"),
        "question_body": tool_result.get("question_body", ""),
        "question_type": "open_ended",
        "skill": tool_result.get("skill"),
        "answer_text": tool_result.get("transcript", ""),
        "answer_data": {"duration_seconds": tool_result.get("duration_seconds", 0)},
        "rubric": tool_result.get("rubric"),
    }
