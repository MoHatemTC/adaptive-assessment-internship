import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from supabase import AsyncClient

from app.db import get_db
from app.services.email import send_invitation_email
from app.config.settings import settings

# Admin router — all routes require caller to be an authenticated admin
# (add dependencies=[Depends(require_admin)] here once auth middleware is wired up)
router = APIRouter()

# Public router — candidate-facing endpoint, no auth required
public_router = APIRouter()


# ── Schemas ────────────────────────────────────────────────────────────────────

class InviteBulkBody(BaseModel):
    template_id: str
    emails: list[EmailStr]
    names: dict[str, str] = {}          # email → name (optional)
    notes: str | None = None
    expires_in_days: int | None = Field(None, ge=1)   # None = never expires; 0 is invalid


class InviteSingleBody(BaseModel):
    template_id: str
    candidate_email: EmailStr
    candidate_name: str | None = None
    notes: str | None = None
    expires_in_days: int | None = Field(None, ge=1)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_template(db: AsyncClient, template_id: str) -> dict:
    res = await db.table("assessment_templates").select(
        "id, title, time_limit_minutes, is_published, tool_config"
    ).eq("id", template_id).execute()
    if not res.data:
        raise HTTPException(404, "Template not found")
    tpl = res.data[0]
    if not tpl.get("is_published"):
        raise HTTPException(400, "Template must be published before sending invitations")
    return tpl


def _make_invite_row(
    template_id: str,
    email: str,
    name: str | None,
    notes: str | None,
    expires_in_days: int | None,
    invited_by: str | None,
) -> dict:
    token = secrets.token_urlsafe(20)
    expires_at = None
    if expires_in_days is not None:   # 0 would be caught by Field(ge=1) so this is always >=1
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    return {
        "template_id":      template_id,
        "candidate_email":  email.strip().lower(),
        "candidate_name":   name or None,
        "invitation_token": token,
        "notes":            notes,
        "expires_at":       expires_at,
        "invited_by":       invited_by,
    }


# ── Admin Routes ───────────────────────────────────────────────────────────────

@router.get("", response_model=list)
async def list_invitations(
    template_id: str | None = None,
    status: str | None = None,
    email: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    q = db.table("invitations").select(
        "id, template_id, candidate_email, candidate_name, "
        "status, expires_at, invited_at, opened_at, started_at, completed_at, "
        "notes, invited_by, session_id, "
        "assessment_templates(title)"
    ).order("invited_at", desc=True)
    if template_id:
        q = q.eq("template_id", template_id)
    if status:
        q = q.eq("status", status)
    if email:
        q = q.eq("candidate_email", email.strip().lower())
    res = await q.execute()
    return res.data or []


@router.post("", response_model=dict)
async def create_invitation(body: InviteSingleBody, db: AsyncClient = Depends(get_db)):
    tpl = await _get_template(db, body.template_id)
    row = _make_invite_row(
        body.template_id,
        str(body.candidate_email),
        body.candidate_name,
        body.notes,
        body.expires_in_days,
        None,
    )
    res = await db.table("invitations").insert(row).execute()
    invitation = res.data[0]

    invite_url = f"{settings.frontend_url}/assess/i/{invitation['invitation_token']}"
    email_sent = True
    email_error = None
    try:
        await send_invitation_email(
            candidate_name=invitation.get("candidate_name") or invitation["candidate_email"].split("@")[0],
            candidate_email=invitation["candidate_email"],
            template_title=tpl["title"],
            time_limit_minutes=tpl.get("time_limit_minutes") or 60,
            invite_url=invite_url,
            db=db,
            subject_override=(tpl.get("tool_config") or {}).get("email_subject") or None,
            html_override=(tpl.get("tool_config") or {}).get("email_body") or None,
        )
    except Exception as e:
        print(f"[Invite] Email send failed for {invitation['candidate_email']}: {e}")
        email_sent = False
        email_error = str(e)

    return {"id": invitation["id"], "invite_url": invite_url, "email_sent": email_sent, "email_error": email_error}


@router.post("/bulk", response_model=dict)
async def create_invitations_bulk(body: InviteBulkBody, db: AsyncClient = Depends(get_db)):
    tpl = await _get_template(db, body.template_id)

    rows = [
        _make_invite_row(
            body.template_id,
            str(email),
            body.names.get(str(email)),
            body.notes,
            body.expires_in_days,
            None,
        )
        for email in body.emails
        if str(email).strip()
    ]
    if not rows:
        raise HTTPException(400, "No valid email addresses provided")

    res = await db.table("invitations").insert(rows).execute()
    created = res.data or []

    frontend_base = settings.frontend_url
    sent, failed = 0, 0
    errors: list[str] = []
    for inv in created:
        invite_url = f"{frontend_base}/assess/i/{inv['invitation_token']}"
        try:
            await send_invitation_email(
                candidate_name=inv.get("candidate_name") or inv["candidate_email"].split("@")[0],
                candidate_email=inv["candidate_email"],
                template_title=tpl["title"],
                time_limit_minutes=tpl.get("time_limit_minutes") or 60,
                invite_url=invite_url,
                db=db,
                subject_override=(tpl.get("tool_config") or {}).get("email_subject") or None,
                html_override=(tpl.get("tool_config") or {}).get("email_body") or None,
            )
            sent += 1
        except Exception as e:
            print(f"[Invite] Email failed for {inv['candidate_email']}: {e}")
            failed += 1
            errors.append(str(e))

    return {
        "created": len(created),
        "emails_sent": sent,
        "emails_failed": failed,
        # de-duplicated sample of failure reasons so the admin sees WHY
        "errors": list(dict.fromkeys(errors))[:3],
    }


@router.delete("/{invitation_id}", response_model=dict)
async def cancel_invitation(invitation_id: str, db: AsyncClient = Depends(get_db)):
    inv_res = await db.table("invitations").select("id, status").eq("id", invitation_id).execute()
    if not inv_res.data:
        raise HTTPException(404, "Invitation not found")
    current_status = inv_res.data[0]["status"]
    if current_status in ("started", "completed"):
        raise HTTPException(409, f"Cannot cancel an invitation that is already {current_status}")
    await db.table("invitations").delete().eq("id", invitation_id).execute()
    return {"message": "Invitation cancelled"}


@router.post("/{invitation_id}/resend", response_model=dict)
async def resend_invitation(invitation_id: str, db: AsyncClient = Depends(get_db)):
    inv_res = await db.table("invitations").select(
        "*, assessment_templates(title, time_limit_minutes)"
    ).eq("id", invitation_id).execute()
    if not inv_res.data:
        raise HTTPException(404, "Invitation not found")
    inv = inv_res.data[0]
    if inv["status"] in ("completed", "expired"):
        raise HTTPException(409, f"Cannot resend to a {inv['status']} invitation")
    tpl = inv.get("assessment_templates") or {}

    invite_url = f"{settings.frontend_url}/assess/i/{inv['invitation_token']}"
    try:
        await send_invitation_email(
            candidate_name=inv.get("candidate_name") or inv["candidate_email"].split("@")[0],
            candidate_email=inv["candidate_email"],
            template_title=tpl.get("title") or "Assessment",
            time_limit_minutes=tpl.get("time_limit_minutes") or 60,
            invite_url=invite_url,
            db=db,
        )
    except Exception as e:
        raise HTTPException(502, f"Email delivery failed: {e}")
    return {"message": "Invitation resent"}


@router.get("/pipeline", response_model=list)
async def get_pipeline(
    email: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    q = db.table("invitations").select(
        "id, candidate_email, candidate_name, status, invited_at, completed_at, "
        "template_id, session_id, "
        "assessment_templates(id, title, assessment_type), "
        "candidate_sessions(id, status, final_reports(total_score, placement, skill_scores))"
    ).order("invited_at", desc=True)
    if email:
        q = q.eq("candidate_email", email.strip().lower())
    res = await q.execute()
    return res.data or []


# ── Public: candidate opens invite link ────────────────────────────────────────

@public_router.get("/open/{token}", response_model=dict)
async def open_invitation(token: str, db: AsyncClient = Depends(get_db)):
    """Called when a candidate opens their invite link. Returns template info."""
    # Use Postgres-side expiry check to avoid timezone parsing issues
    res = await db.table("invitations").select(
        "id, status, expires_at, candidate_email, candidate_name, template_id, "
        "assessment_templates(id, title, description, time_limit_minutes, assessment_type, is_published)"
    ).eq("invitation_token", token).execute()

    if not res.data:
        raise HTTPException(404, "Invitation not found or no longer valid")

    inv = res.data[0]

    # Check expiry server-side (safe UTC comparison)
    if inv.get("expires_at"):
        try:
            exp_str = inv["expires_at"]
            # Postgres returns e.g. "2024-01-15 12:00:00+00" or "2024-01-15T12:00:00+00:00"
            exp = datetime.fromisoformat(exp_str.replace(" ", "T").replace("+00", "+00:00").rstrip("Z") + ("+00:00" if "+" not in exp_str else ""))
        except ValueError:
            exp = None
        if exp and datetime.now(timezone.utc) > exp:
            await db.table("invitations").update({"status": "expired"}).eq("id", inv["id"]).execute()
            raise HTTPException(410, "Invitation not found or no longer valid")

    # Block any terminal or in-progress state
    status_blocks = {
        "completed": "This assessment has already been completed",
        "started":   "This assessment is already in progress",
        "expired":   "This invitation has expired",
    }
    if inv["status"] in status_blocks:
        raise HTTPException(409, status_blocks[inv["status"]])

    tpl = inv.get("assessment_templates") or {}
    if not tpl.get("is_published"):
        raise HTTPException(404, "Invitation not found or no longer valid")

    # Mark opened (only on first open)
    if inv["status"] == "pending":
        await db.table("invitations").update({
            "status": "opened",
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", inv["id"]).execute()

    return {
        "invitation_id":      inv["id"],
        "candidate_email":    inv["candidate_email"],
        "candidate_name":     inv["candidate_name"],
        "template_id":        inv["template_id"],
        "template_title":     tpl.get("title"),
        "description":        tpl.get("description"),
        "time_limit_minutes": tpl.get("time_limit_minutes") or 60,
        "assessment_type":    tpl.get("assessment_type") or "track",
    }
