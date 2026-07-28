from supabase import AsyncClient
from app.agent.state import AgentState
from app.services.vector_store import find_next_question

SKILL_ORDER = ["thinking", "soft", "work", "digital_ai", "growth"]


def _next_skill(state: AgentState) -> str:
    """Pick the skill with the lowest ability estimate."""
    estimates = state.get("ability_estimates", {})
    return min(SKILL_ORDER, key=lambda s: estimates.get(s, 0.0))


def _next_difficulty(state: AgentState, skill: str) -> str:
    estimate = state.get("ability_estimates", {}).get(skill, 0.0)
    if estimate < 2.0:
        return "easy"
    elif estimate < 3.5:
        return "medium"
    return "hard"


async def select_next_question(state: AgentState, db: AsyncClient) -> dict | None:
    """Return a question dict from Supabase, chosen semantically via Qdrant."""
    blueprint = state["blueprint"]
    answered_count = len(state.get("answers", []))
    tool_sequence = blueprint.get("tool_sequence", [])

    if answered_count >= len(tool_sequence):
        return None  # assessment complete

    next_tool = tool_sequence[answered_count]
    skill = _next_skill(state)
    difficulty = _next_difficulty(state, skill)
    track = state.get("track")
    exclude_ids = state.get("asked_question_ids", [])

    # Semantic search for the best matching unused question
    question_type = next_tool if next_tool in ("mcq", "coding", "diagram") else "open_ended"
    context = f"{skill} {difficulty} {question_type} question for {track or 'general'} track"

    question_id = await find_next_question(
        skill=skill,
        difficulty=difficulty,
        track=track,
        exclude_ids=exclude_ids,
        context_text=context,
    )

    if not question_id:
        # Fallback: any question of the right type from Supabase
        res = await db.table("questions").select("*").eq("skill", skill).eq("difficulty", difficulty).eq("is_active", True).not_.in_("id", exclude_ids or ["none"]).limit(1).execute()
        if not res.data:
            return None
        return res.data[0]

    res = await db.table("questions").select("*").eq("id", question_id).execute()
    return res.data[0] if res.data else None
