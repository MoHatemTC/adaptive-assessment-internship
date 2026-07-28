from fastapi import APIRouter
from app.db import get_db

router = APIRouter()


@router.get("/health")
async def health():
    db = await get_db()
    db_ok = db is not None
    return {"status": "ok", "supabase": db_ok}
