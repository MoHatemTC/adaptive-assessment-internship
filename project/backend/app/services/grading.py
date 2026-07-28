import json
from openai import AsyncOpenAI
from app.config.settings import settings

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


SKILL_DIMENSIONS = ["thinking", "soft", "work", "digital_ai", "growth"]

GRADING_SYSTEM = """You are a silent assessment grader. Given a question, its rubric, and a candidate answer,
score the answer across the five skill dimensions: thinking, soft, work, digital_ai, growth.
Each score is 0-5. Only the primary skill of the question typically scores above 0.
Return ONLY valid JSON in this exact format:
{
  "skill_scores": {"thinking": 0, "soft": 0, "work": 0, "digital_ai": 0, "growth": 0},
  "overall_score": 0,
  "rationale": "brief explanation"
}"""


async def grade_open_ended(
    question_body: str,
    rubric: dict | None,
    candidate_answer: str,
    primary_skill: str,
) -> dict:
    prompt = f"""Question: {question_body}
Rubric: {json.dumps(rubric or {})}
Primary skill: {primary_skill}
Candidate answer: {candidate_answer}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": GRADING_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(res.choices[0].message.content)


async def grade_video_visual(question_body: str, frames: list[str]) -> dict:
    """P4 E: score the VISUAL delivery of a video answer (presentation, professionalism,
    engagement, camera presence) from a few sampled still frames — separate from the
    transcript content score. Returns {"visual_score": 0-5, "rationale": str}."""
    frames = [f for f in (frames or []) if f][:3]   # cap cost: at most 3 frames
    if not frames:
        return {"visual_score": 0.0, "rationale": "No video frames provided."}
    content: list = [{"type": "text", "text": (
        f'You are assessing the VISUAL delivery of a candidate\'s spoken video answer to: "{question_body}". '
        "Based ONLY on these still frames, rate presentation, professionalism, engagement, and camera "
        "presence from 0 to 5. Do NOT judge the spoken content. "
        'Return ONLY JSON: {"visual_score": 0-5, "rationale": "one sentence"}.'
    )}]
    for f in frames:
        b64 = str(f).split(",", 1)[-1]   # strip any data: URI prefix
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    try:
        res = await _llm().chat.completions.create(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
        )
        data = json.loads(res.choices[0].message.content)
        score = max(0.0, min(5.0, float(data.get("visual_score") or 0)))
        return {"visual_score": score, "rationale": data.get("rationale", "")}
    except Exception as e:
        return {"visual_score": 0.0, "rationale": f"visual scoring unavailable: {str(e)[:100]}"}


def grade_mcq(answer_key: dict, selected_id: str) -> dict:
    correct = answer_key.get("correct_id")
    is_correct = selected_id == correct
    score = 5.0 if is_correct else 0.0
    return {
        "skill_scores": {},  # filled by caller with primary skill
        "overall_score": score,
        "rationale": "Correct" if is_correct else f"Incorrect. Expected: {correct}",
    }


async def grade_code_llm(challenge: dict, submitted_code: str) -> dict:
    """LLM evaluates submitted code holistically — no E2B needed."""
    test_cases = challenge.get("test_cases") or []
    tc_summary = json.dumps(
        [{"id": t.get("id"), "description": t.get("description"), "expected": t.get("expected_behavior")}
         for t in test_cases],
        indent=2,
    )

    prompt = f"""You are an expert code evaluator. Grade this coding submission honestly.

CHALLENGE:
{challenge.get("body", "")}

EXPECTED APPROACH:
{challenge.get("expected_approach", "")}

CONSTRAINTS:
{challenge.get("constraints", "")}

TEST CASES (assess whether the submitted code would pass each):
{tc_summary}

SUBMITTED CODE:
```python
{submitted_code}
```

Return ONLY valid JSON:
{{
  "correctness": 0,
  "code_quality": 0,
  "edge_case_handling": 0,
  "overall_score": 0,
  "feedback": "specific, actionable feedback",
  "test_results": [{{"id": 1, "likely_passes": true, "reason": "brief reason"}}]
}}

Score each dimension 0–5. overall_score = correctness*0.5 + code_quality*0.3 + edge_case_handling*0.2."""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    data = json.loads(res.choices[0].message.content)
    # Normalise overall_score to 0-5 range
    data["overall_score"] = round(min(5.0, max(0.0, float(data.get("overall_score") or 0))), 2)
    data["rationale"] = data.get("feedback", "")
    return data


# kept for backward-compat if called anywhere
async def grade_code(test_results: dict, code: str) -> dict:
    passed = test_results.get("passed", 0)
    total  = test_results.get("total", 1)
    pass_rate = passed / total if total else 0
    combined = round(pass_rate * 5, 2)
    return {
        "skill_scores": {},
        "overall_score": combined,
        "rationale": f"Tests: {passed}/{total}",
    }
