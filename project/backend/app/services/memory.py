from supabase import AsyncClient


async def create_memory_card(
    db: AsyncClient,
    session_id: str,
    question_number: int,
    skill: str,
    topic: str,
    verdict: str,
    evidence: str,
    score: float,
) -> dict:
    res = await db.table("memory_cards").insert({
        "session_id": session_id,
        "question_number": question_number,
        "skill": skill,
        "topic": topic,
        "verdict": verdict,
        "evidence": evidence,
        "score": score,
    }).execute()
    return res.data[0]


async def get_memory_cards(db: AsyncClient, session_id: str) -> list[dict]:
    res = (
        await db.table("memory_cards")
        .select("*")
        .eq("session_id", session_id)
        .order("question_number")
        .execute()
    )
    return res.data


async def get_known_topics(db: AsyncClient, session_id: str) -> list[str]:
    cards = await get_memory_cards(db, session_id)
    return [c["topic"] for c in cards if c["verdict"] == "knows"]


async def get_gap_topics(db: AsyncClient, session_id: str) -> list[str]:
    cards = await get_memory_cards(db, session_id)
    return [c["topic"] for c in cards if c["verdict"] == "gap"]
