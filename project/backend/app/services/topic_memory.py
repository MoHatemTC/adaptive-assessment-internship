"""
Cross-session topic memory using Qdrant.

Every time a memory card is written (knows / partial / gap), we embed
the topic text and upsert it to Qdrant under the candidate's email.

At the start of a new session, we scroll all past topics for that email
and pass them to the Planner so it avoids reassessing what is already known.
"""

import uuid
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)
from openai import AsyncOpenAI
from app.config.settings import settings

_qdrant: AsyncQdrantClient | None = None
_embed_client: AsyncOpenAI | None = None


def _get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
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


async def ensure_topics_collection() -> None:
    qdrant = _get_qdrant()
    existing = await qdrant.get_collections()
    names = [c.name for c in existing.collections]
    if settings.qdrant_topics_collection not in names:
        await qdrant.create_collection(
            collection_name=settings.qdrant_topics_collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


async def store_topic(
    candidate_email: str,
    topic: str,
    skill: str,
    verdict: str,
    session_id: str,
) -> None:
    """
    Embed a topic string and upsert it into the user-topics collection.
    Called after every memory card write in the orchestrator.
    Silently swallowed on error — never blocks the assessment.
    """
    if not candidate_email or not topic:
        return
    try:
        await ensure_topics_collection()
        vector = await _embed(topic)
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "candidate_email": candidate_email,
                "topic": topic,
                "skill": skill,
                "verdict": verdict,   # knows | partial | gap
                "session_id": session_id,
            },
        )
        await _get_qdrant().upsert(
            collection_name=settings.qdrant_topics_collection,
            points=[point],
        )
    except Exception as e:
        print(f"[topic_memory] store_topic failed (non-critical): {e}")


async def get_past_topics(
    candidate_email: str,
    limit: int = 40,
) -> list[dict]:
    """
    Return all topics previously assessed for this email across all sessions.
    Returns a list of dicts: [{topic, skill, verdict, session_id}, ...]
    Returns [] silently on any error.
    """
    if not candidate_email:
        return []
    try:
        await ensure_topics_collection()
        results, _ = await _get_qdrant().scroll(
            collection_name=settings.qdrant_topics_collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="candidate_email",
                        match=MatchValue(value=candidate_email),
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [r.payload for r in results] if results else []
    except Exception as e:
        print(f"[topic_memory] get_past_topics failed (non-critical): {e}")
        return []
