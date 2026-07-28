"""
Unified Qdrant memory store for New Masar.

Two collections:
  masar_memory_cards  — one point per graded answer, keyed by candidate_email (cross-session)
  masar_cv_chunks     — one point per CV section, keyed by session_id (within-session retrieval)

All functions swallow errors silently — Qdrant being down must never block an assessment.
"""

import uuid
import asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)
from openai import AsyncOpenAI
from app.config.settings import settings

_qdrant: AsyncQdrantClient | None = None
_embed_client: AsyncOpenAI | None = None

CARDS = settings.qdrant_cards_collection   # masar_memory_cards
CV    = settings.qdrant_cv_collection      # masar_cv_chunks
SIZE  = 768


def _get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return _qdrant


def _get_embed() -> AsyncOpenAI:
    global _embed_client
    if _embed_client is None:
        _embed_client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _embed_client


async def _embed(text: str) -> list[float]:
    res = await _get_embed().embeddings.create(
        model=settings.litellm_embedding_model,
        input=text,
    )
    return res.data[0].embedding


async def ensure_collections() -> None:
    qdrant = _get_qdrant()
    existing = await qdrant.get_collections()
    names = {c.name for c in existing.collections}
    for col in [CARDS, CV]:
        if col not in names:
            await qdrant.create_collection(
                collection_name=col,
                vectors_config=VectorParams(size=SIZE, distance=Distance.COSINE),
            )


# ── Memory cards (cross-session) ──────────────────────────────────────────────

async def store_card(
    candidate_email: str,
    topic: str,
    skill: str,
    verdict: str,
    score: float,
    evidence: str,
    session_id: str,
) -> None:
    """
    Embed a graded answer and upsert it into masar_memory_cards.
    Called after every memory card write in the orchestrator.
    """
    if not candidate_email or not topic:
        return
    try:
        await ensure_collections()
        vector = await _embed(f"{skill}: {topic}")
        await _get_qdrant().upsert(
            collection_name=CARDS,
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "candidate_email": candidate_email,
                    "topic": topic,
                    "skill": skill,
                    "verdict": verdict,
                    "score": round(score, 2),
                    "evidence": evidence[:300],
                    "session_id": session_id,
                },
            )],
        )
    except Exception as e:
        print(f"[memory_store] store_card failed (non-critical): {e}")


async def get_past_cards(candidate_email: str, limit: int = 40) -> list[dict]:
    """
    Return all memory cards for this candidate across all sessions.
    Used by the Planner at session start to avoid repeating known topics.
    """
    if not candidate_email:
        return []
    try:
        await ensure_collections()
        results, _ = await _get_qdrant().scroll(
            collection_name=CARDS,
            scroll_filter=Filter(must=[
                FieldCondition(key="candidate_email", match=MatchValue(value=candidate_email))
            ]),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [r.payload for r in results] if results else []
    except Exception as e:
        print(f"[memory_store] get_past_cards failed (non-critical): {e}")
        return []


async def find_near_duplicates(
    topic_hint: str,
    session_id: str,
    threshold: float = 0.88,
) -> list[str]:
    """
    Search memory cards within the CURRENT session for topics semantically
    similar to topic_hint (cosine ≥ threshold). Returns matching topic strings
    so the Generator can treat them as additional known_topics to avoid.

    This catches near-repeats that exact-string matching misses:
      e.g. "Python async" vs "asyncio event loop" → similarity ~0.91 → flagged.
    """
    if not topic_hint or not session_id:
        return []
    try:
        await ensure_collections()
        vector = await _embed(topic_hint)
        results = await _get_qdrant().search(
            collection_name=CARDS,
            query_vector=vector,
            query_filter=Filter(must=[
                FieldCondition(key="session_id", match=MatchValue(value=session_id))
            ]),
            limit=5,
            score_threshold=threshold,
            with_payload=True,
        )
        return [r.payload["topic"] for r in results if r.payload.get("topic")]
    except Exception as e:
        print(f"[memory_store] find_near_duplicates failed (non-critical): {e}")
        return []


# ── CV chunks (within-session semantic retrieval) ──────────────────────────────

def _split_cv(cv_data: dict) -> list[dict]:
    """Break structured CV JSON into embeddable text sections."""
    chunks = []

    skills = cv_data.get("skills") or []
    if skills:
        text = "Skills: " + ", ".join(
            f"{s['name']} ({s.get('level', '')})" for s in skills[:15]
        )
        chunks.append({"section_type": "skills", "content": text})

    for exp in (cv_data.get("experience") or [])[:6]:
        title   = exp.get("title", "")
        company = exp.get("company", "")
        desc    = (exp.get("description") or "")[:400]
        if title or company:
            chunks.append({
                "section_type": "experience",
                "content": f"{title} at {company}: {desc}",
            })

    summary = (cv_data.get("raw_summary") or "")[:600]
    if summary:
        chunks.append({"section_type": "summary", "content": summary})

    return chunks


async def store_cv_chunks(cv_data: dict, session_id: str) -> None:
    """
    Embed each section of the candidate's CV and store in masar_cv_chunks.
    Called once per session on the first turn, after CV is loaded.
    """
    if not cv_data or not session_id:
        return
    try:
        await ensure_collections()
        chunks = _split_cv(cv_data)
        if not chunks:
            return
        # Embed all sections concurrently — typically 3-8 chunks
        vectors = await asyncio.gather(*[_embed(c["content"]) for c in chunks])
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors[i],
                payload={
                    "session_id": session_id,
                    "section_type": chunks[i]["section_type"],
                    "content": chunks[i]["content"],
                },
            )
            for i in range(len(chunks))
        ]
        await _get_qdrant().upsert(collection_name=CV, points=points)
    except Exception as e:
        print(f"[memory_store] store_cv_chunks failed (non-critical): {e}")


async def get_relevant_cv_chunks(
    topic_hint: str,
    session_id: str,
    top_k: int = 2,
) -> list[str]:
    """
    Return the 2 most relevant CV sections for the current topic_hint.
    Injected into the Generator instead of the full raw_summary — tighter and cheaper.
    """
    if not topic_hint or not session_id:
        return []
    try:
        await ensure_collections()
        vector = await _embed(topic_hint)
        results = await _get_qdrant().search(
            collection_name=CV,
            query_vector=vector,
            query_filter=Filter(must=[
                FieldCondition(key="session_id", match=MatchValue(value=session_id))
            ]),
            limit=top_k,
            with_payload=True,
        )
        return [r.payload["content"] for r in results if r.payload.get("content")]
    except Exception as e:
        print(f"[memory_store] get_relevant_cv_chunks failed (non-critical): {e}")
        return []
