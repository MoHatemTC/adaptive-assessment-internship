"""Global platform settings (key-value in the `app_settings` table).

Tolerant reads: if the table/row doesn't exist yet (pre-migration 013), we default
to "accepting sessions" so nothing breaks before the migration is applied.
"""

from supabase import AsyncClient


async def get_platform_settings(db: AsyncClient) -> dict:
    try:
        res = await db.table("app_settings").select("value").eq("key", "platform").execute()
        return (res.data[0]["value"] if res.data else {}) or {}
    except Exception:
        return {}


async def accepting_sessions(db: AsyncClient) -> bool:
    return bool((await get_platform_settings(db)).get("accepting_sessions", True))


async def set_platform_settings(db: AsyncClient, patch: dict) -> dict:
    current = await get_platform_settings(db)
    current.update(patch)
    await db.table("app_settings").upsert({"key": "platform", "value": current}).execute()
    return current
