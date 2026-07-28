from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import AsyncClient

from app.db import get_db
from app.schemas.session import SessionStartRequest, SessionStateResponse
from app.services.platform_settings import accepting_sessions

router = APIRouter()

_PAUSED_MSG = "Assessments are paused right now. Please check back soon."


class InviteSessionStartRequest(BaseModel):
    invitation_token: str
    candidate_name: str
    candidate_email: str
    cv_upload_id: str
    consent_camera: bool = True
    consent_voice: bool = True
    consent_data: bool = True


@router.get("/config/{token}")
async def get_assessment_config(token: str, db: AsyncClient = Depends(get_db)):
    """Public: pre-start info the learner flow needs to render the intake step —
    the candidate-facing description, whether a CV is required, and any custom
    intake questions the admin configured."""
    res = await db.table("assessment_templates").select(
        "title, description, intake_config"
    ).eq("public_link_token", token).eq("is_published", True).execute()
    if not res.data:
        raise HTTPException(404, "Assessment link is invalid or unpublished")
    t = res.data[0]
    cfg = t.get("intake_config") or {}
    return {
        "title": t.get("title"),
        "description": t.get("description"),
        "cv_required": cfg.get("cv_required", True),
        "questions": cfg.get("questions") or [],
    }


@router.post("/start", response_model=dict)
async def start_session(body: SessionStartRequest, db: AsyncClient = Depends(get_db)):
    if not await accepting_sessions(db):
        raise HTTPException(503, _PAUSED_MSG)
    # Verify the token maps to a published template
    tpl_res = await db.table("assessment_templates").select("id, title, track, admin_prompt, tool_config, time_limit_minutes, assessment_type, intake_config, blueprints(*)").eq("public_link_token", body.token).eq("is_published", True).execute()
    if not tpl_res.data:
        raise HTTPException(404, "Assessment link is invalid or unpublished")

    template = tpl_res.data[0]
    cv_required = (template.get("intake_config") or {}).get("cv_required", True)

    # Verify the CV upload exists (only when required / provided)
    if body.cv_upload_id:
        cv_res = await db.table("cv_uploads").select("id").eq("id", body.cv_upload_id).execute()
        if not cv_res.data:
            raise HTTPException(400, "CV upload not found — please upload your CV first")
    elif cv_required:
        raise HTTPException(400, "This assessment requires a CV — please upload your CV first")

    # Create session row with cv_upload_id already linked
    session_res = await db.table("candidate_sessions").insert({
        "template_id":        template["id"],
        "candidate_name":     body.candidate_name,
        "candidate_email":    body.candidate_email,
        "track":              body.track or template.get("track"),
        "status":             "identity",
        "agent_state":        {},
        "ability_estimates":  {},
        "asked_question_ids": [],
        "cv_upload_id":       body.cv_upload_id,
        "intake_answers":     body.intake_answers or {},
        # Copy time limit from template so the orchestrator knows the budget
        "time_limit_minutes": template.get("time_limit_minutes") or 60,
    }).execute()

    session = session_res.data[0]
    session_id = session["id"]

    # Back-link: stamp the session_id onto the cv_uploads row so the
    # orchestrator query (eq("session_id", session_id)) finds the CV
    if body.cv_upload_id:
        await db.table("cv_uploads").update({"session_id": session_id}).eq("id", body.cv_upload_id).execute()

    return {
        "session_id":         session_id,
        "template_title":     template["title"],
        "track":              session["track"],
        "status":             session["status"],
        "time_limit_minutes": template.get("time_limit_minutes") or 60,
        "assessment_type":    template.get("assessment_type") or "track",
    }


@router.post("/start-invite", response_model=dict)
async def start_invite_session(body: InviteSessionStartRequest, db: AsyncClient = Depends(get_db)):
    """Start a session from a personal invitation token (not the public template link)."""
    if not await accepting_sessions(db):
        raise HTTPException(503, _PAUSED_MSG)
    inv_res = await db.table("invitations").select(
        "id, status, expires_at, candidate_email, template_id, "
        "assessment_templates(id, title, track, time_limit_minutes, assessment_type, is_published)"
    ).eq("invitation_token", body.invitation_token).execute()

    if not inv_res.data:
        raise HTTPException(404, "Invitation not found")

    inv = inv_res.data[0]

    if inv.get("expires_at"):
        try:
            exp_str = inv["expires_at"]
            exp = datetime.fromisoformat(exp_str.replace(" ", "T").replace("+00", "+00:00").rstrip("Z") + ("+00:00" if "+" not in exp_str else ""))
        except ValueError:
            exp = None
        if exp and datetime.now(timezone.utc) > exp:
            raise HTTPException(410, "This invitation has expired")

    if inv["status"] in ("completed", "started"):
        raise HTTPException(409, f"This assessment is already {inv['status']}")
    if inv["status"] == "expired":
        raise HTTPException(410, "This invitation has expired")

    tpl = inv.get("assessment_templates") or {}
    if not tpl.get("is_published"):
        raise HTTPException(404, "This assessment is no longer available")

    cv_res = await db.table("cv_uploads").select("id").eq("id", body.cv_upload_id).execute()
    if not cv_res.data:
        raise HTTPException(400, "CV upload not found — please upload your CV first")

    # Use the invitation's stored email (not caller-supplied) to prevent hijacking
    candidate_email = inv["candidate_email"]

    session_res = await db.table("candidate_sessions").insert({
        "template_id":        inv["template_id"],
        "candidate_name":     body.candidate_name,
        "candidate_email":    candidate_email,
        "track":              tpl.get("track"),
        "status":             "identity",
        "agent_state":        {},
        "ability_estimates":  {},
        "asked_question_ids": [],
        "cv_upload_id":       body.cv_upload_id,
        "time_limit_minutes": tpl.get("time_limit_minutes") or 60,
    }).execute()

    session = session_res.data[0]
    session_id = session["id"]

    await db.table("cv_uploads").update({"session_id": session_id}).eq("id", body.cv_upload_id).execute()

    # Conditional update: only succeed if invitation is still in a startable state
    # This closes the race condition window where two concurrent requests both create sessions
    update_res = await db.table("invitations").update({
        "status":     "started",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
    }).eq("id", inv["id"]).in_("status", ["pending", "opened"]).execute()

    if not update_res.data:
        # A concurrent request won the race — clean up the orphaned session we just created
        await db.table("candidate_sessions").delete().eq("id", session_id).execute()
        raise HTTPException(409, "Assessment session already started by a concurrent request")

    return {
        "session_id":         session_id,
        "template_title":     tpl.get("title"),
        "track":              session["track"],
        "status":             session["status"],
        "time_limit_minutes": tpl.get("time_limit_minutes") or 60,
        "assessment_type":    tpl.get("assessment_type") or "track",
    }


@router.get("/history", response_model=list)
async def get_session_history(email: str, db: AsyncClient = Depends(get_db)):
    """Return all past sessions for a candidate email, newest first."""
    res = await db.table("candidate_sessions").select(
        "id, status, integrity_status, started_at, completed_at, "
        "assessment_templates(title, assessment_type, time_limit_minutes), final_reports(total_score, placement)"
    ).eq("candidate_email", email).order("started_at", desc=True).limit(20).execute()
    return res.data or []


@router.get("/{session_id}", response_model=SessionStateResponse)
async def get_session(session_id: str, db: AsyncClient = Depends(get_db)):
    res = await db.table("candidate_sessions").select("*").eq("id", session_id).execute()
    if not res.data:
        raise HTTPException(404, "Session not found")
    s = res.data[0]
    return {
        "session_id": s["id"],
        "status": s["status"],
        "integrity_status": s["integrity_status"],
        "current_step": s["agent_state"].get("current_tool"),
        "progress": {
            "asked": len(s.get("asked_question_ids") or []),
            "ability_estimates": s.get("ability_estimates", {}),
        },
    }
