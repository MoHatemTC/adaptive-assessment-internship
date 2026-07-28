from e2b_code_interpreter import AsyncSandbox


def build_coding_payload(question: dict, time_limit_minutes: int = 30) -> dict:
    return {
        "question_id": question["id"],
        "question_body": question["body"],
        "skill": question["skill"],
        "difficulty": question["difficulty"],
        "answer_key": question.get("answer_key", {}),   # contains test_cases
        "rubric": question.get("rubric"),
        "question_type": "coding",
        "time_limit_seconds": time_limit_minutes * 60,
        "languages": ["python"],
    }


async def run_in_sandbox(code: str, test_cases: list[dict]) -> dict:
    """Execute code in E2B and run test cases. Returns results dict."""
    async with await AsyncSandbox.create() as sandbox:
        # Run the candidate's code
        exec_result = await sandbox.run_code(code)
        stdout = exec_result.logs.stdout
        stderr = exec_result.logs.stderr

        passed = 0
        results = []
        for tc in test_cases:
            test_code = tc.get("code", "")
            expected = tc.get("expected_output", "")
            tc_result = await sandbox.run_code(code + "\n" + test_code)
            actual = "".join(tc_result.logs.stdout).strip()
            ok = actual == expected.strip()
            if ok:
                passed += 1
            results.append({"input": tc.get("input"), "expected": expected, "actual": actual, "passed": ok})

        return {
            "passed": passed,
            "total": len(test_cases),
            "results": results,
            "stdout": stdout,
            "stderr": stderr,
        }


def extract_coding_answer(tool_result: dict) -> dict:
    return {
        "question_id": tool_result.get("question_id"),
        "question_body": tool_result.get("question_body", ""),
        "question_type": "coding",
        "skill": tool_result.get("skill"),
        "answer_text": tool_result.get("code", ""),
        "answer_data": {"test_results": tool_result.get("test_results", {})},
        "rubric": tool_result.get("rubric"),
    }
