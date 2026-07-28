from fastapi import APIRouter, Depends
from supabase import AsyncClient

from app.db import get_db
from app.services.proctoring import handle_event

router = APIRouter()


class ProctoringEventRequest:
    def __init__(self, session_id: str, event_type: str, metadata: dict = {}):
        self.session_id = session_id
        self.event_type = event_type
        self.metadata = metadata


from pydantic import BaseModel


class ProctoringEventBody(BaseModel):
    session_id: str
    event_type: str
    metadata: dict = {}


@router.post("/event")
async def proctor_event(body: ProctoringEventBody, db: AsyncClient = Depends(get_db)):
    result = await handle_event(db, body.session_id, body.event_type, body.metadata)
    return result
