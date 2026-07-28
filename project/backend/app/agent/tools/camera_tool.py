def build_camera_payload(question: dict, time_limit_minutes: int = 10, mode: str = "technical") -> dict:
    return {
        "question_id": question["id"],
        "question_body": question["body"],
        "skill": question["skill"],
        "difficulty": question["difficulty"],
        "rubric": question.get("rubric"),
        "question_type": "camera",
        "mode": mode,
        "time_limit_seconds": time_limit_minutes * 60,
        "camera_required": True,
    }


def extract_camera_answer(tool_result: dict) -> dict:
    return {
        "question_id": tool_result.get("question_id"),
        "question_body": tool_result.get("question_body", ""),
        "question_type": "open_ended",
        "skill": tool_result.get("skill"),
        "answer_text": tool_result.get("transcript", ""),
        "answer_data": {"camera_on": tool_result.get("camera_on", True)},
        "rubric": tool_result.get("rubric"),
    }
