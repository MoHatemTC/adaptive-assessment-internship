import json
from openai import AsyncOpenAI
from app.agent.state import AgentState
from app.schemas.report import FinalReport, SkillScore
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
PRO_THRESHOLD = 18.0
PRO_THINKING_MIN = 3.0


def _aggregate_scores(answers: list[dict]) -> dict[str, SkillScore]:
    totals: dict[str, list[float]] = {s: [] for s in SKILL_DIMENSIONS}
    evidence: dict[str, list[str]] = {s: [] for s in SKILL_DIMENSIONS}

    for a in answers:
        skill_scores = a.get("skill_scores", {})
        for skill in SKILL_DIMENSIONS:
            val = skill_scores.get(skill, 0)
            if val > 0:
                totals[skill].append(val)
                evidence[skill].append(a.get("rationale", "")[:100])

    result = {}
    for skill in SKILL_DIMENSIONS:
        vals = totals[skill]
        avg = sum(vals) / len(vals) if vals else 0.0
        result[skill] = SkillScore(score=round(avg, 2), evidence=evidence[skill][:3])
    return result


async def generate_report(state: AgentState) -> FinalReport:
    answers = state.get("answers", [])
    skill_scores = _aggregate_scores(answers)
    total = sum(s.score for s in skill_scores.values())

    thinking_score = skill_scores["thinking"].score
    placement = "PRO" if total >= PRO_THRESHOLD and thinking_score >= PRO_THINKING_MIN else "BEGINNER"

    scores_summary = {k: v.score for k, v in skill_scores.items()}
    feedback_prompt = f"""Assessment scores: {json.dumps(scores_summary)}
Placement: {placement}
Write 2-3 sentences of constructive feedback and 3 specific recommendations.
Return JSON: {{"feedback": "...", "recommendations": ["...", "...", "..."]}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": feedback_prompt}],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    content = json.loads(res.choices[0].message.content)

    return FinalReport(
        session_id=state["session_id"],
        candidate_name=state["candidate_name"],
        candidate_email=state["candidate_email"],
        track=state.get("track"),
        skill_scores=skill_scores,
        total_score=round(total, 2),
        placement=placement,
        feedback=content.get("feedback", ""),
        recommendations=content.get("recommendations", []),
        integrity_status=state.get("proctoring_flags", []) and "warned" or "clean",
        proctoring_flags=state.get("proctoring_flags", []),
    )
