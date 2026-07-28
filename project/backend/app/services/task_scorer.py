import json
from openai import AsyncOpenAI
from app.config.settings import settings
from app.services.zip_extractor import extract_zip_contents

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


async def score_task_submission(
    zip_bytes: bytes,
    rubric: list[dict],
    task_brief: str,
    cv_summary: str,
) -> dict:
    """Extract all content from ZIP (text, docs, images, audio, video) and score against rubric."""
    files_text = await extract_zip_contents(zip_bytes)
    rubric_text = json.dumps(rubric, indent=2)

    prompt = f"""Task Brief:
{task_brief}

Candidate Background:
{cv_summary or "Not provided"}

Submitted Files:
{files_text}

Rubric (evaluate each criterion as PASS or FAIL with evidence from the files above):
{rubric_text}

Return ONLY valid JSON:
{{
  "criteria_scores": [{{"criterion": "str", "passed": true, "evidence": "one sentence citing specific file/content"}}],
  "total_score": 0.0,
  "rationale": "2-3 sentence overall assessment of the submission"
}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(res.choices[0].message.content)
