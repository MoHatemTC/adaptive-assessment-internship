from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import AsyncClient

from app.db import get_db
from app.services.platform_settings import accepting_sessions

router = APIRouter()


class SelfStartRequest(BaseModel):
    track_slug: str
    candidate_name: str
    candidate_email: str
    cv_upload_id: str
    consent_camera: bool = True
    consent_voice: bool = True
    consent_data: bool = True


@router.post("/self-start")
async def self_start_session(body: SelfStartRequest, db: AsyncClient = Depends(get_db)):
    """Create a session directly from a track slug using the default template for that track."""
    if not await accepting_sessions(db):
        raise HTTPException(503, "Assessments are paused right now. Please check back soon.")

    # Find the default template for this track
    tpl_res = await db.table("assessment_templates").select(
        "id, title, track, admin_prompt, tool_config, time_limit_minutes, assessment_type"
    ).eq("track_slug", body.track_slug).eq("is_default", True).limit(1).execute()

    if not tpl_res.data:
        raise HTTPException(404, f"No default template found for track '{body.track_slug}'")

    template = tpl_res.data[0]

    # Verify the CV upload exists
    cv_res = await db.table("cv_uploads").select("id").eq("id", body.cv_upload_id).execute()
    if not cv_res.data:
        raise HTTPException(400, "CV upload not found — please complete the onboarding form first")

    # Create session — status is 'in_progress' (CV and consent already collected in onboarding)
    session_res = await db.table("candidate_sessions").insert({
        "template_id":        template["id"],
        "candidate_name":     body.candidate_name,
        "candidate_email":    body.candidate_email,
        "track":              template.get("track"),
        "status":             "in_progress",
        "agent_state":        {},
        "ability_estimates":  {},
        "asked_question_ids": [],
        "cv_upload_id":       body.cv_upload_id,
        "time_limit_minutes": template.get("time_limit_minutes") or 60,
    }).execute()

    session = session_res.data[0]
    session_id = session["id"]

    # Link CV to this session so the orchestrator can find it
    await db.table("cv_uploads").update({"session_id": session_id}).eq("id", body.cv_upload_id).execute()

    return {
        "session_id":         session_id,
        "template_title":     template["title"],
        "track":              template.get("track"),
        "time_limit_minutes": template.get("time_limit_minutes") or 60,
        "assessment_type":    template.get("assessment_type") or "track",
    }
