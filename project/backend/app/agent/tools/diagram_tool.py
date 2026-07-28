def build_diagram_payload(question: dict) -> dict:
    return {
        "question_id": question["id"],
        "question_body": question["body"],
        "image_url": question.get("image_url", ""),
        "skill": question["skill"],
        "difficulty": question["difficulty"],
        "rubric": question.get("rubric"),
        "question_type": "diagram",
    }


def extract_diagram_answer(tool_result: dict) -> dict:
    return {
        "question_id": tool_result.get("question_id"),
        "question_body": tool_result.get("question_body", ""),
        "question_type": "diagram",
        "skill": tool_result.get("skill"),
        "answer_text": tool_result.get("answer_text", ""),
        "answer_data": {},
        "rubric": tool_result.get("rubric"),
    }
