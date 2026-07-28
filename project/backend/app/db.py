from supabase import acreate_client, AsyncClient
from app.config.settings import settings

_supabase: AsyncClient | None = None


async def init_supabase() -> None:
    global _supabase
    if settings.connect_to_db:
        _supabase = await acreate_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
        print("Supabase connected.")


async def get_db() -> AsyncClient:
    return _supabase
