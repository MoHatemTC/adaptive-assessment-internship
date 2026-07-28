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


JUDGE_SYSTEM = """You are a grading quality judge. Review this grading result for consistency
and accuracy. Return JSON: {"approved": true/false, "adjusted_score": float, "reason": "brief"}"""


async def judge_grading(grading_result: dict, question_body: str, answer_text: str) -> dict:
    prompt = f"""Question: {question_body}
Answer: {answer_text}
Grading result: {json.dumps(grading_result)}
Is this grading accurate and consistent? Approve or adjust."""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    verdict = json.loads(res.choices[0].message.content)
    if not verdict.get("approved", True):
        grading_result["score"] = verdict.get("adjusted_score", grading_result["score"])
        grading_result["rationale"] += f" [Judge: {verdict.get('reason','')}]"
    return verdict
