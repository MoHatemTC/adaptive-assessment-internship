from supabase import AsyncClient


async def get_or_create_profile(db: AsyncClient, session_id: str) -> dict:
    res = (
        await db.table("learner_profiles")
        .select("*")
        .eq("session_id", session_id)
        .execute()
    )
    if res.data:
        return res.data[0]
    res = await db.table("learner_profiles").insert({"session_id": session_id}).execute()
    return res.data[0]


def _read_skill_entry(entry) -> tuple[float, int]:
    """Parse both old format (float) and new format (dict). Returns (ema, count)."""
    if isinstance(entry, dict):
        return float(entry.get("ema", 2.5)), int(entry.get("count", 0))
    if isinstance(entry, (int, float)):
        return float(entry), 1
    return 2.5, 0


async def update_profile(
    db: AsyncClient,
    session_id: str,
    skill: str,
    score: float,
    topic: str,
    verdict: str,
) -> dict:
    profile = await get_or_create_profile(db, session_id)
    skill_scores: dict = dict(profile.get("skill_scores") or {})

    old_ema, count = _read_skill_entry(skill_scores.get(skill))

    # Exponential moving average: recent performance weighted at 60%
    alpha = 0.6
    new_ema = round(alpha * score + (1 - alpha) * old_ema, 2) if count > 0 else round(score, 2)
    # Flag when the latest answer diverges significantly from the running trend
    inconsistent = abs(score - new_ema) > 1.5

    skill_scores[skill] = {
        "ema": new_ema,
        "last": round(score, 2),
        "count": count + 1,
        "inconsistent": inconsistent,
    }

    known: list = list(set(profile.get("known_topics") or []))
    gap: list = list(set(profile.get("gap_topics") or []))

    if verdict == "knows" and topic not in known:
        known.append(topic)
    elif verdict == "gap" and topic not in gap:
        gap.append(topic)

    res = (
        await db.table("learner_profiles")
        .update({
            "skill_scores": skill_scores,
            "known_topics": known,
            "gap_topics": gap,
            "questions_asked": count + 1,
            "updated_at": "now()",
        })
        .eq("session_id", session_id)
        .execute()
    )
    return res.data[0]


async def get_profile(db: AsyncClient, session_id: str) -> dict | None:
    res = (
        await db.table("learner_profiles")
        .select("*")
        .eq("session_id", session_id)
        .execute()
    )
    return res.data[0] if res.data else None
