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


async def verify_identity(candidate_name: str, declared_name: str) -> dict:
    """
    Basic identity verification: compare declared name to captured name.
    In a full implementation this would compare a face capture frame.
    """
    prompt = f"""The candidate declared their name as "{declared_name}".
They typed their name as "{candidate_name}" during intake.
Do these names match the same person? Return JSON:
{{"match": true/false, "confidence": 0-1, "reason": "brief reason"}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(res.choices[0].message.content)
