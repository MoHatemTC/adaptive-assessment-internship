"""
Agentic RAG service for the question bank.

Flow for each assessment slot:
1. find_candidate_question() → searches question_bank by tool_type + skill_target + difficulty
2. If found → personalize_question() rewrites body/options for the candidate while preserving the outcome
3. If not found → caller falls back to generate_question() (existing generation path)
"""

import json
from json_repair import repair_json
from openai import AsyncOpenAI
from supabase import AsyncClient

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


async def find_candidate_question(
    db: AsyncClient,
    template_id: str,
    tool_type: str | None,
    skill_target: str | None,
    difficulty: str,
    topic_hint: str | None,
    exclude_ids: list[str] | None = None,
    question_set_id: str | None = None,
) -> dict | None:
    """
    Retrieve the best matching question from the bank for this slot.
    Scope (P3 B1/B2):
      • question_set_id given → restrict to questions in that reusable set.
      • otherwise → GENERIC bank: this template's own questions OR global ones
        (template_id IS NULL). Matches tool_type + skill_target, filters difficulty,
        then ranks by topic_hint relevance. Returns None if no match.
    """
    if not tool_type or not skill_target:
        return None

    q = (
        db.table("question_bank")
        .select("id, tool_type, skill_target, difficulty, tags, body, payload")
        .eq("tool_type", tool_type)
        .eq("skill_target", skill_target)
        .eq("is_active", True)
    )
    if question_set_id:
        items = await (
            db.table("question_set_items").select("question_id").eq("set_id", question_set_id).execute()
        )
        set_ids = [r["question_id"] for r in (items.data or [])]
        if not set_ids:
            return None
        q = q.in_("id", set_ids)
    else:
        # Generic bank: this assessment's questions OR shared/global ones.
        q = q.or_(f"template_id.eq.{template_id},template_id.is.null")
    # Expand difficulty: medium slots can use easy OR medium questions
    if difficulty == "medium":
        q = q.in_("difficulty", ["easy", "medium"])
    elif difficulty == "hard":
        q = q.in_("difficulty", ["medium", "hard"])
    else:
        q = q.eq("difficulty", difficulty)

    if exclude_ids:
        q = q.not_.in_("id", exclude_ids)

    res = await q.limit(20).execute()
    candidates = res.data or []
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Rank by topic_hint similarity using simple keyword overlap
    topic_words = set((topic_hint or "").lower().split())
    if not topic_words:
        return candidates[0]

    def _relevance(row: dict) -> int:
        body_words = set((row.get("body") or "").lower().split())
        tag_words  = set(t.lower() for t in (row.get("tags") or []))
        return len(topic_words & (body_words | tag_words))

    return max(candidates, key=_relevance)


PERSONALIZATION_SYSTEM = """You are personalizing an assessment question for a specific candidate.
Your job: rewrite the question body (and options if MCQ) to feel personally relevant to this candidate.

RULES:
1. Keep the SAME competency/topic being assessed — do not change what the question tests.
2. For MCQ: you MAY rewrite both the question body AND the options, but:
   - Preserve which option is correct (same correct_id)
   - Keep the same number of options
   - The correct option must still be unambiguously correct
3. Use the candidate's professional background to make examples/scenarios feel relevant.
4. Do NOT make the question easier or harder — keep the same cognitive demand.
5. The rewrite should feel natural, not forced.

Return ONLY valid JSON."""


async def personalize_question(
    bank_question: dict,
    cv_context: str,
    candidate_level: str = "intermediate",
) -> dict:
    """
    Rewrite a bank question for a specific candidate.
    Preserves the question's core competency and answer key.
    Returns a dict with the same structure as generate_question() output.
    """
    tool_type = bank_question["tool_type"]
    payload   = bank_question.get("payload") or {}

    if tool_type == "mcq":
        options_str = json.dumps(payload.get("options") or [])
        answer_key  = payload.get("answer_key") or {}
        correct_id  = answer_key.get("correct_id", "")

        prompt = f"""Personalize this MCQ question for the candidate below.

ORIGINAL QUESTION:
{bank_question['body']}

ORIGINAL OPTIONS:
{options_str}

CORRECT ANSWER: option "{correct_id}"

CANDIDATE BACKGROUND:
{cv_context}

CANDIDATE LEVEL: {candidate_level}

Rewrite the body and all options to feel relevant to the candidate.
Keep option "{correct_id}" as the correct answer — just rephrase it.

Return JSON:
{{
  "body": "personalized question text",
  "options": [{{"id": "a", "text": "..."}}, {{"id": "b", "text": "..."}}, ...],
  "answer_key": {json.dumps(answer_key)}
}}"""

    elif tool_type == "voice":
        criteria = payload.get("evaluation_criteria") or []
        prompt = f"""Personalize this interview question for the candidate below.

ORIGINAL QUESTION:
{bank_question['body']}

EVALUATION CRITERIA (keep these intact):
{json.dumps(criteria)}

CANDIDATE BACKGROUND:
{cv_context}

Rewrite the question body to reference the candidate's professional context.
Keep the evaluation criteria exactly as-is.

Return JSON:
{{
  "body": "personalized question text",
  "evaluation_criteria": {json.dumps(criteria)},
  "time_limit_seconds": {payload.get("time_limit_seconds", 120)}
}}"""

    else:
        # For other types (coding, task, etc.), just lightly personalize the body
        prompt = f"""Lightly personalize this question's framing for the candidate.

ORIGINAL:
{bank_question['body']}

CANDIDATE BACKGROUND:
{cv_context}

Return JSON: {{"body": "personalized question text"}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": PERSONALIZATION_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    try:
        personalized = json.loads(repair_json(res.choices[0].message.content))
    except Exception:
        personalized = {}

    # Merge personalized fields — but never allow LLM to override the original answer_key
    original_answer_key = payload.get("answer_key")
    merged_payload = {**payload, **personalized}
    if original_answer_key is not None:
        merged_payload["answer_key"] = original_answer_key

    topic_tag = next((t for t in (bank_question.get("tags") or []) if t), bank_question["skill_target"])

    return {
        "body":         personalized.get("body") or bank_question["body"],
        "tool_type":    tool_type,
        "payload":      merged_payload,
        "skill_target": bank_question["skill_target"],
        "topic":        topic_tag,
        "difficulty":   bank_question["difficulty"],
        "from_bank":    True,
        "bank_question_id": bank_question["id"],
    }
