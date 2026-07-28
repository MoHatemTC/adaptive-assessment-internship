from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue, HasIdCondition,
)
from openai import AsyncOpenAI
from app.config.settings import settings

_qdrant: AsyncQdrantClient | None = None
_embed_client: AsyncOpenAI | None = None


def _get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return _qdrant


def _get_embed_client() -> AsyncOpenAI:
    global _embed_client
    if _embed_client is None:
        _embed_client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _embed_client


async def _embed(text: str) -> list[float]:
    client = _get_embed_client()
    res = await client.embeddings.create(
        model=settings.litellm_embedding_model,
        input=text,
    )
    return res.data[0].embedding


async def ensure_collection() -> None:
    qdrant = _get_qdrant()
    existing = await qdrant.get_collections()
    names = [c.name for c in existing.collections]
    if settings.qdrant_collection not in names:
        await qdrant.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )


async def store_question(question: dict) -> None:
    """Embed a question and upsert it into Qdrant."""
    await ensure_collection()
    embedding = await _embed(question["body"])
    point = PointStruct(
        id=question["id"],
        vector=embedding,
        payload={
            "skill": question.get("skill"),
            "difficulty": question.get("difficulty"),
            "track": question.get("track"),
            "question_type": question.get("question_type"),
            "body": question.get("body"),
        },
    )
    await _get_qdrant().upsert(
        collection_name=settings.qdrant_collection,
        points=[point],
    )


async def find_next_question(
    skill: str,
    difficulty: str,
    track: str | None,
    exclude_ids: list[str],
    context_text: str = "",
) -> str | None:
    """Return the ID of the most semantically relevant unused question."""
    await ensure_collection()
    query_vector = await _embed(context_text or f"{skill} {difficulty} question")

    must_conditions = [
        FieldCondition(key="skill", match=MatchValue(value=skill)),
        FieldCondition(key="difficulty", match=MatchValue(value=difficulty)),
    ]
    if track:
        must_conditions.append(FieldCondition(key="track", match=MatchValue(value=track)))

    search_filter = Filter(must=must_conditions)
    if exclude_ids:
        search_filter.must_not = [HasIdCondition(has_id=exclude_ids)]

    results = await _get_qdrant().search(
        collection_name=settings.qdrant_collection,
        query_vector=query_vector,
        query_filter=search_filter,
        limit=1,
    )
    return results[0].id if results else None
