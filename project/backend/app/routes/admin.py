import json
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from openai import AsyncOpenAI, APIError
from supabase import AsyncClient

from app.db import get_db
from app.config.settings import settings
from app.services.llm_retry import chat_completion
from app.schemas.admin import (
    TemplateCreate, TemplateUpdate, TemplateResponse,
    StepCreate, StepResponse,
    QuestionCreate, QuestionResponse,
    PublishResponse, MessageResponse,
)
from app.services.vector_store import store_question
from app.services.platform_settings import get_platform_settings, set_platform_settings

router = APIRouter()
# Candidate-facing endpoints that must NOT require admin (mounted ungated in main.py).
public_router = APIRouter()

# ── Admin authentication ────────────────────────────────────
# Admin = a signed-in Supabase user whose email is on the org domain (mirrors the
# frontend rule in lib/auth.ts). Applied as a router-level dependency in main.py.
_ADMIN_EMAIL_DOMAIN = "@sprints.ai"

async def require_admin(authorization: str | None = Header(None), db: AsyncClient = Depends(get_db)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        resp = await db.auth.get_user(token)     # validates the JWT against Supabase
        user = getattr(resp, "user", None)
        email = (getattr(user, "email", None) or "").lower()
    except Exception:
        raise HTTPException(401, "Invalid or expired session")
    if not email.endswith(_ADMIN_EMAIL_DOMAIN):
        raise HTTPException(403, "Admin access required")
    return email

_llm_client: AsyncOpenAI | None = None

def _llm() -> AsyncOpenAI:
    global _llm_client
    if not _llm_client:
        _llm_client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _llm_client


# ── helpers ────────────────────────────────────────────────

async def _get_template(db: AsyncClient, template_id: str) -> dict:
    res = await db.table("assessment_templates").select("*").eq("id", template_id).execute()
    if not res.data:
        raise HTTPException(404, "Template not found")
    return res.data[0]


# ── templates ──────────────────────────────────────────────

@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(db: AsyncClient = Depends(get_db)):
    templates = (await db.table("assessment_templates").select("*, workflow_steps(*)").order("created_at", desc=True).execute()).data
    for t in templates:
        t["steps"] = t.pop("workflow_steps", [])
    return templates


@router.post("/templates", response_model=TemplateResponse)
async def create_template(body: TemplateCreate, db: AsyncClient = Depends(get_db)):
    payload = body.model_dump()
    if not payload.get("type_configs"):
        payload.pop("type_configs", None)   # don't write empty per-type config (safe pre-migration 018)
    res = await db.table("assessment_templates").insert(payload).select("*, workflow_steps(*)").execute()
    t = res.data[0]
    t["steps"] = t.pop("workflow_steps", [])
    return t


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str, db: AsyncClient = Depends(get_db)):
    res = await db.table("assessment_templates").select("*, workflow_steps(*)").eq("id", template_id).execute()
    if not res.data:
        raise HTTPException(404, "Template not found")
    t = res.data[0]
    t["steps"] = t.pop("workflow_steps", [])
    return t


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template(template_id: str, body: TemplateUpdate, db: AsyncClient = Depends(get_db)):
    await _get_template(db, template_id)
    payload = body.model_dump(exclude_unset=True)
    if "type_configs" in payload and not payload["type_configs"]:
        payload.pop("type_configs")   # don't write empty per-type config (safe pre-migration 018)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await db.table("assessment_templates").update(payload).eq("id", template_id).select("*, workflow_steps(*)").execute()
    t = res.data[0]
    t["steps"] = t.pop("workflow_steps", [])
    return t


@router.delete("/templates/{template_id}", response_model=MessageResponse)
async def delete_template(template_id: str, db: AsyncClient = Depends(get_db)):
    await _get_template(db, template_id)
    # Collect blueprint IDs belonging to this template
    bp_res = await db.table("blueprints").select("id").eq("template_id", template_id).execute()
    bp_ids = [r["id"] for r in (bp_res.data or [])]
    # Null-out session FKs so cascades can proceed
    await db.table("candidate_sessions").update({"template_id": None}).eq("template_id", template_id).execute()
    for bp_id in bp_ids:
        await db.table("candidate_sessions").update({"blueprint_id": None}).eq("blueprint_id", bp_id).execute()
    await db.table("assessment_templates").delete().eq("id", template_id).execute()
    return {"message": "Template deleted"}


@router.post("/templates/{template_id}/publish", response_model=PublishResponse)
async def publish_template(template_id: str, db: AsyncClient = Depends(get_db)):
    await _get_template(db, template_id)
    token = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc).isoformat()
    await db.table("assessment_templates").update({
        "is_published": True,
        "public_link_token": token,
        "updated_at": now,
    }).eq("id", template_id).execute()
    return {
        "public_link_token": token,
        "public_url": f"{settings.frontend_url}/assess/{token}",
    }


@router.post("/templates/{template_id}/unpublish", response_model=MessageResponse)
async def unpublish_template(template_id: str, db: AsyncClient = Depends(get_db)):
    await _get_template(db, template_id)
    await db.table("assessment_templates").update({
        "is_published": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", template_id).execute()
    return {"message": "Template unpublished"}


# ── steps ──────────────────────────────────────────────────

@router.post("/templates/{template_id}/steps", response_model=StepResponse)
async def add_step(template_id: str, body: StepCreate, db: AsyncClient = Depends(get_db)):
    await _get_template(db, template_id)
    payload = body.model_dump()
    payload["template_id"] = template_id
    res = await db.table("workflow_steps").insert(payload).execute()
    return res.data[0]


@router.delete("/templates/{template_id}/steps/{step_id}", response_model=MessageResponse)
async def delete_step(template_id: str, step_id: str, db: AsyncClient = Depends(get_db)):
    await db.table("workflow_steps").delete().eq("id", step_id).eq("template_id", template_id).execute()
    return {"message": "Step removed"}


# ── questions ──────────────────────────────────────────────

@router.get("/questions", response_model=list[QuestionResponse])
async def list_questions(
    skill: str | None = None,
    difficulty: str | None = None,
    question_type: str | None = None,
    track: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    q = db.table("questions").select("*").eq("is_active", True)
    if skill:
        q = q.eq("skill", skill)
    if difficulty:
        q = q.eq("difficulty", difficulty)
    if question_type:
        q = q.eq("question_type", question_type)
    if track:
        q = q.eq("track", track)
    res = await q.order("created_at", desc=True).execute()
    return res.data


@router.post("/questions", response_model=QuestionResponse)
async def create_question(body: QuestionCreate, db: AsyncClient = Depends(get_db)):
    payload = body.model_dump()
    res = await db.table("questions").insert(payload).execute()
    question = res.data[0]
    # Embed and store in Qdrant asynchronously (fire-and-forget style)
    await store_question(question)
    return question


@router.delete("/questions/{question_id}", response_model=MessageResponse)
async def deactivate_question(question_id: str, db: AsyncClient = Depends(get_db)):
    await db.table("questions").update({"is_active": False}).eq("id", question_id).execute()
    return {"message": "Question deactivated"}


# ── generated questions (AI-generated on-the-fly) ──────────

@router.get("/generated-questions")
async def list_generated_questions(
    session_id: str | None = None,
    tool_type: str | None = None,
    skill_target: str | None = None,
    limit: int = 100,
    db: AsyncClient = Depends(get_db),
):
    q = db.table("generated_questions").select(
        "id, session_id, question_number, tool_type, skill_target, topic, difficulty, body, payload, created_at, "
        "candidate_sessions(candidate_name, candidate_email)"
    )
    if session_id:
        q = q.eq("session_id", session_id)
    if tool_type:
        q = q.eq("tool_type", tool_type)
    if skill_target:
        q = q.eq("skill_target", skill_target)
    res = await q.order("created_at", desc=True).limit(limit).execute()
    questions = res.data or []

    # Attach candidate answer to each question via generated_question_id
    if questions:
        qids = [q["id"] for q in questions]
        try:
            ans_res = await db.table("candidate_answers").select(
                "generated_question_id, score, grading_rationale, answer_text, answer_data"
            ).in_("generated_question_id", qids).execute()
            ans_map = {a["generated_question_id"]: a for a in (ans_res.data or [])}
            for q in questions:
                q["candidate_answer"] = ans_map.get(q["id"])
        except Exception:
            for q in questions:
                q["candidate_answer"] = None

    return questions


# ── AI task config generator ────────────────────────────────

class GenerateTaskConfigRequest(BaseModel):
    title: str
    description: str | None = None
    admin_prompt: str | None = None


@router.post("/generate-task-config")
async def generate_task_config(body: GenerateTaskConfigRequest):
    prompt = f"""You are designing a practical take-home task for a technical assessment.

Assessment title: {body.title}
Description: {body.description or "Not provided"}
Admin instructions: {body.admin_prompt or "Not provided"}

Generate a focused, realistic task that:
- Can be completed in 30–45 minutes
- Has clear, measurable deliverables
- Has binary (pass/fail) rubric criteria an AI can evaluate from submitted files

Return ONLY valid JSON — no markdown, no explanation:
{{
  "brief": "Full task description in 3–5 sentences, written for the candidate",
  "deliverables": ["file or folder the candidate must submit", "..."],
  "rubric": [
    {{"criterion": "Specific, verifiable criterion", "weight": 1}},
    "..."
  ],
  "time_limit_minutes": 30
}}

Rubric should have 4–6 criteria. Each must be objectively verifiable from submitted files."""

    try:
        res = await chat_completion(
            _llm(),
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
    except APIError:
        raise HTTPException(
            status_code=503,
            detail="The AI service is busy right now. Please click Generate with AI again in a moment.",
        )

    raw = res.choices[0].message.content or "{}"
    try:
        cfg = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=502, detail="The AI returned an unexpected format. Please try again.")

    # Validate the shape the frontend relies on (brief + deliverables[] + rubric[]).
    if not isinstance(cfg, dict) or not cfg.get("brief") or not isinstance(cfg.get("deliverables"), list):
        raise HTTPException(status_code=502, detail="The AI returned an incomplete task. Please try again.")
    cfg.setdefault("deliverables", [])
    cfg.setdefault("rubric", [])
    cfg.setdefault("time_limit_minutes", 30)
    return cfg


# ── AI competency suggester ─────────────────────────────────

class SuggestCompetenciesRequest(BaseModel):
    title: str
    track: str | None = None
    admin_prompt: str | None = None
    tag: str | None = None   # "technical" | "behavioural" | None → both


@router.post("/suggest-competencies")
async def suggest_competencies(body: SuggestCompetenciesRequest):
    """Propose role-specific competencies (each tagged technical|behavioural) from
    the assessment setup. Powers the template UI's competency 'Generate with AI'."""
    want = (
        f"Only propose '{body.tag}' competencies."
        if body.tag in ("technical", "behavioural")
        else "Propose a balanced mix of technical and behavioural competencies."
    )
    prompt = f"""You design competency frameworks for skills assessments.

Assessment title:   {body.title}
Target role/track:  {body.track or "Not specified"}
Admin instructions: {body.admin_prompt or "Not specified"}

Propose 6–10 concrete competencies to assess for this role. {want}
- technical  = role-specific hard skills (e.g. "Python", "Content strategy", "SQL", "Copywriting").
- behavioural = transferable/soft skills (e.g. "Communication", "Problem solving", "Ownership").

Return ONLY valid JSON:
{{"competencies": [{{"name": "string", "tag": "technical|behavioural"}}]}}"""

    try:
        res = await chat_completion(
            _llm(),
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
    except APIError:
        raise HTTPException(503, "The AI service is busy right now. Please try Generate with AI again in a moment.")

    raw = res.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPException(502, "The AI returned an unexpected format. Please try again.")

    items = data.get("competencies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise HTTPException(502, "The AI returned no competencies. Please try again.")

    out: list[dict] = []
    seen: set[str] = set()
    for c in items:
        raw = c.get("name") if isinstance(c, dict) else (c if isinstance(c, str) else None)
        name = (raw or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        tag = (c.get("tag") if isinstance(c, dict) else "") or "behavioural"
        out.append({"name": name, "tag": "technical" if str(tag).lower() == "technical" else "behavioural"})
    return {"competencies": out}


# ── AI assessment planner (prompt → full config) ────────────

class GenerateAssessmentRequest(BaseModel):
    prompt: str


@router.post("/generate-assessment")
async def generate_assessment(body: GenerateAssessmentRequest):
    """Turn a short admin prompt into a complete assessment configuration that the
    'Plan with AI' button pre-fills into the New Assessment form (admin reviews + saves)."""
    prompt = f"""You design assessment blueprints for the Masar platform.
From the admin's request, produce ONE complete assessment configuration.

Admin request: {body.prompt}

Return ONLY valid JSON (no markdown) with this exact shape:
{{
  "title": "short assessment title",
  "description": "1-2 sentence candidate-facing description",
  "admin_prompt": "a focused instruction for the question planner",
  "track": "role/track name, or empty string",
  "modes": ["technical","behavioural","career","psychometric"],
  "adaptivity_level": "low|medium|high",
  "time_limit_minutes": 45,
  "competencies": [{{"name": "string", "tag": "technical|behavioural"}}],
  "tools": {{"mcq": true, "voice": true, "coding": false, "visualization": false, "task": false}}
}}
Rules:
- Choose modes/tools/competencies that genuinely fit the request; include 5–8 competencies.
- modes must be a subset of [technical, behavioural, career, psychometric]; keep only what fits.
- time_limit_minutes between 20 and 90; adaptivity_level defaults to "high" unless the request implies otherwise."""

    try:
        res = await chat_completion(
            _llm(),
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.5,
        )
    except APIError:
        raise HTTPException(status_code=503, detail="The AI service is busy right now. Please click Plan with AI again in a moment.")

    raw = res.choices[0].message.content or "{}"
    try:
        cfg = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=502, detail="The AI returned an unexpected format. Please try again.")
    if not isinstance(cfg, dict) or not cfg.get("title"):
        raise HTTPException(status_code=502, detail="The AI returned an incomplete plan. Please try again.")

    # Normalize to safe defaults the form expects.
    valid_modes = [m for m in (cfg.get("modes") or []) if m in ("technical", "behavioural", "career", "psychometric")]
    cfg["modes"] = valid_modes or ["technical"]
    lvl = str(cfg.get("adaptivity_level") or "high").lower()
    cfg["adaptivity_level"] = lvl if lvl in ("low", "medium", "high") else "high"
    try:
        cfg["time_limit_minutes"] = max(20, min(90, int(cfg.get("time_limit_minutes") or 45)))
    except Exception:
        cfg["time_limit_minutes"] = 45

    # Normalize competencies to [{name, tag}] (drop malformed/dupes) and tools to a bool map.
    comps: list[dict] = []
    seen_c: set[str] = set()
    for c in (cfg.get("competencies") or []):
        raw = c.get("name") if isinstance(c, dict) else (c if isinstance(c, str) else None)
        nm = (raw or "").strip()
        if not nm or nm.lower() in seen_c:
            continue
        seen_c.add(nm.lower())
        tag = (c.get("tag") if isinstance(c, dict) else "") or "behavioural"
        comps.append({"name": nm, "tag": "technical" if str(tag).lower() == "technical" else "behavioural"})
    cfg["competencies"] = comps
    _tools = cfg.get("tools")
    cfg["tools"] = (
        {k: bool(v) for k, v in _tools.items() if k in ("mcq", "voice", "video", "coding", "visualization", "task")}
        if isinstance(_tools, dict) else {}
    )
    return cfg


# ── platform settings (global on/off) ──────────────────────

@router.get("/settings")
async def get_settings(db: AsyncClient = Depends(get_db)):
    s = await get_platform_settings(db)
    return {"accepting_sessions": s.get("accepting_sessions", True)}


class SettingsUpdate(BaseModel):
    accepting_sessions: bool | None = None


@router.patch("/settings")
async def update_settings(body: SettingsUpdate, db: AsyncClient = Depends(get_db)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    s = await set_platform_settings(db, patch)
    return {"accepting_sessions": s.get("accepting_sessions", True)}


# ── proctoring review ───────────────────────────────────────

@router.get("/proctoring")
async def list_proctoring_sessions(
    status: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    """
    Return sessions that have at least one integrity event (warned or flagged).
    Includes per-session event severity counts and flagged frame count.
    """
    q = (
        db.table("candidate_sessions")
        .select("id, candidate_name, candidate_email, integrity_status, status, started_at, completed_at, template_id")
        .order("started_at", desc=True)
    )
    if status:
        q = q.eq("integrity_status", status)
    else:
        q = q.neq("integrity_status", "clean")

    sessions_res = await q.execute()
    sessions = sessions_res.data or []

    result = []
    for s in sessions:
        sid = s["id"]
        events_res = await db.table("proctoring_events").select("severity, event_type").eq("session_id", sid).execute()
        try:
            frames_res = await db.table("proctoring_frames").select("id").eq("session_id", sid).execute()
            flagged_count = len(frames_res.data or [])
        except Exception:
            flagged_count = 0

        counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
        for e in (events_res.data or []):
            sev = e.get("severity", "low")
            counts[sev] = counts.get(sev, 0) + 1

        result.append({
            **s,
            "event_counts": counts,
            "flagged_frames_count": flagged_count,
        })

    return result


@router.get("/analytics")
async def assessment_analytics(template_id: str | None = None, db: AsyncClient = Depends(get_db)):
    """P3 P2: per-assessment analytics — pass/fail, score + skill averages, integrity
    summary and cheating (flagged) counts. With no template_id, returns the latest
    integrity logs across all assessments."""
    if not template_id:
        recent = await (
            db.table("candidate_sessions")
            .select("id, candidate_name, candidate_email, integrity_status, status, started_at, template_id")
            .neq("integrity_status", "clean")
            .order("started_at", desc=True)
            .limit(25)
            .execute()
        )
        return {"mode": "latest_logs", "sessions": recent.data or []}

    sess = await (
        db.table("candidate_sessions")
        .select("id, candidate_name, candidate_email, integrity_status, status, completed_at")
        .eq("template_id", template_id)
        .execute()
    )
    sessions = sess.data or []
    sids = [s["id"] for s in sessions]
    total = len(sessions)
    completed = sum(1 for s in sessions if s.get("status") == "completed")

    integrity = {"clean": 0, "warned": 0, "flagged": 0}
    for s in sessions:
        st = s.get("integrity_status") or "clean"
        integrity[st] = integrity.get(st, 0) + 1

    reports: list[dict] = []
    if sids:
        rres = await (
            db.table("final_reports")
            .select("session_id, total_score, placement, skill_scores")
            .in_("session_id", sids)
            .execute()
        )
        reports = rres.data or []

    scores = [float(r.get("total_score") or 0) for r in reports]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0
    # Pass/fail is only meaningful for track-style placements. Non-track placements
    # (DISCOVER, HR_ASSESSED, MBTI types) are neither pass nor fail — don't count
    # them as failed. A full placement_breakdown is returned so the UI shows truth.
    _placement_breakdown: dict[str, int] = {}
    for r in reports:
        _p = str(r.get("placement") or "—").upper()
        _placement_breakdown[_p] = _placement_breakdown.get(_p, 0) + 1
    _PASS = {"PRO", "PASS", "ADVANCED"}
    _FAIL = {"BEGINNER", "FAIL"}
    passed = sum(n for p, n in _placement_breakdown.items() if p in _PASS)
    failed = sum(n for p, n in _placement_breakdown.items() if p in _FAIL)

    skill_totals: dict[str, list] = {}
    for r in reports:
        for k, v in (r.get("skill_scores") or {}).items():
            val = v.get("score") if isinstance(v, dict) else v
            try:
                skill_totals.setdefault(k, []).append(float(val))
            except (TypeError, ValueError):
                pass
    skill_averages = {k: round(sum(v) / len(v), 2) for k, v in skill_totals.items() if v}

    violation_counts: dict[str, int] = {}
    if sids:
        ev = await db.table("proctoring_events").select("event_type").in_("session_id", sids).execute()
        for e in (ev.data or []):
            et = e.get("event_type") or "other"
            violation_counts[et] = violation_counts.get(et, 0) + 1

    return {
        "mode": "assessment",
        "template_id": template_id,
        "totals": {"sessions": total, "completed": completed, "reports": len(reports)},
        "placement": {"passed": passed, "failed": failed},
        "placement_breakdown": _placement_breakdown,
        "avg_score": avg_score,
        "skill_averages": skill_averages,
        "integrity": integrity,
        "violations": violation_counts,
        "cheating_sessions": integrity.get("flagged", 0),
    }


@router.get("/proctoring/{session_id}")
async def get_proctoring_detail(
    session_id: str,
    db: AsyncClient = Depends(get_db),
):
    """
    Full proctoring detail for one session:
      - session metadata
      - every flagged frame with its Gemini verdict
      - chronological event timeline
    """
    session_res = await db.table("candidate_sessions").select(
        "id, candidate_name, candidate_email, integrity_status, status, started_at, completed_at"
    ).eq("id", session_id).execute()

    if not session_res.data:
        raise HTTPException(404, "Session not found")

    frames_res = await db.table("proctoring_frames").select(
        "id, frame_base64, gemini_verdict, is_flagged, created_at"
    ).eq("session_id", session_id).eq("is_flagged", True).order("created_at").execute()

    events_res = await db.table("proctoring_events").select(
        "id, event_type, severity, metadata, created_at"
    ).eq("session_id", session_id).order("created_at").execute()

    return {
        "session": session_res.data[0],
        "frames": frames_res.data or [],
        "events": events_res.data or [],
    }


# ── Candidate Sessions + Results ───────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(
    template_id: str | None = None,
    status: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    """All candidate sessions with their final report summary. Filter by template or status."""
    q = db.table("candidate_sessions").select(
        "id, candidate_name, candidate_email, status, integrity_status, "
        "started_at, completed_at, time_limit_minutes, template_id, "
        "assessment_templates(title, track)"
    ).order("started_at", desc=True).limit(200)

    if template_id:
        q = q.eq("template_id", template_id)
    if status:
        q = q.eq("status", status)

    sessions_res = await q.execute()
    sessions = sessions_res.data or []

    if not sessions:
        return []

    # Attach final report summary to each session
    sids = [s["id"] for s in sessions]
    reports_res = await db.table("final_reports").select(
        "session_id, total_score, placement, feedback"
    ).in_("session_id", sids).execute()
    report_map = {r["session_id"]: r for r in (reports_res.data or [])}

    # Attach answered_count from agent_state
    answers_res = await db.table("candidate_answers").select(
        "session_id"
    ).in_("session_id", sids).execute()
    answer_counts: dict[str, int] = {}
    for a in (answers_res.data or []):
        answer_counts[a["session_id"]] = answer_counts.get(a["session_id"], 0) + 1

    for s in sessions:
        sid = s["id"]
        report = report_map.get(sid)
        s["report"] = {
            "total_score": report["total_score"] if report else None,
            "placement":   report["placement"]   if report else None,
            "feedback":    report["feedback"]    if report else None,
        }
        s["answered_count"] = answer_counts.get(sid, 0)
        s["template_title"] = (s.get("assessment_templates") or {}).get("title") or "—"
        s["template_track"] = (s.get("assessment_templates") or {}).get("track") or "—"
        s.pop("assessment_templates", None)

    return sessions


@router.get("/sessions/{session_id}")
async def get_session_result(
    session_id: str,
    db: AsyncClient = Depends(get_db),
):
    """
    Full session result for admin review:
      - session metadata + final report
      - all generated questions with candidate answers
    """
    session_res = await db.table("candidate_sessions").select(
        "id, candidate_name, candidate_email, status, integrity_status, "
        "started_at, completed_at, time_limit_minutes, template_id, "
        "assessment_templates(title, track, admin_prompt)"
    ).eq("id", session_id).execute()

    if not session_res.data:
        raise HTTPException(404, "Session not found")

    session = session_res.data[0]
    session["template_title"] = (session.get("assessment_templates") or {}).get("title") or "—"
    session["template_track"] = (session.get("assessment_templates") or {}).get("track") or "—"
    session["admin_prompt"]   = (session.get("assessment_templates") or {}).get("admin_prompt") or ""
    session.pop("assessment_templates", None)

    # Final report
    report_res = await db.table("final_reports").select("*").eq("session_id", session_id).execute()
    report = report_res.data[0] if report_res.data else None

    # All generated questions for this session
    questions_res = await db.table("generated_questions").select(
        "id, question_number, tool_type, skill_target, topic, difficulty, body, payload"
    ).eq("session_id", session_id).order("question_number").execute()
    questions = questions_res.data or []

    # Candidate answers
    answers_res = await db.table("candidate_answers").select(
        "question_number, generated_question_id, skill, score, grading_rationale, answer_text, skill_scores"
    ).eq("session_id", session_id).order("question_number").execute()
    answer_map = {a["generated_question_id"]: a for a in (answers_res.data or []) if a.get("generated_question_id")}
    answer_by_num = {a["question_number"]: a for a in (answers_res.data or [])}

    # Merge answers into questions
    for q in questions:
        answer = answer_map.get(q["id"]) or answer_by_num.get(q["question_number"])
        q["candidate_answer"] = answer

    return {
        "session": session,
        "report":  report,
        "questions": questions,
    }


# ── Coding Challenges ───────────────────────────────────────────────────────

@router.get("/coding")
async def list_coding_challenges(db: AsyncClient = Depends(get_db)):
    """All coding challenges across all sessions, newest first."""
    res = await db.table("coding_challenges").select(
        "id, session_id, question_number, topic, difficulty, skill_target, "
        "overall_score, submitted_at, created_at, "
        "candidate_sessions(candidate_name, candidate_email)"
    ).order("created_at", desc=True).limit(100).execute()
    return res.data or []


@router.get("/coding/{challenge_id}")
async def get_coding_challenge(challenge_id: str, db: AsyncClient = Depends(get_db)):
    """Full challenge detail: body, test cases, submission, LLM scores."""
    res = await db.table("coding_challenges").select(
        "*, candidate_sessions(candidate_name, candidate_email, status)"
    ).eq("id", challenge_id).execute()
    if not res.data:
        raise HTTPException(404, "Challenge not found")
    return res.data[0]


# ── Question Bank ────────────────────────────────────────────────────────────

class QuestionBankCreate(BaseModel):
    template_id: str | None = None   # None = GLOBAL (generic bank), usable by any assessment
    tool_type: str
    skill_target: str
    difficulty: str
    tags: list[str] = []
    body: str
    payload: dict = {}
    scope: str | None = None
    created_by: str | None = None


class QuestionSetCreate(BaseModel):
    name: str
    description: str | None = None
    track: str | None = None
    created_by: str | None = None


class QuestionSetItemsBody(BaseModel):
    question_ids: list[str]


class QuestionBankUpdate(BaseModel):
    tool_type: str | None = None
    skill_target: str | None = None
    difficulty: str | None = None
    tags: list[str] | None = None
    body: str | None = None
    payload: dict | None = None
    is_active: bool | None = None


@router.get("/question-bank")
async def list_bank_questions(
    template_id: str | None = None,
    tool_type: str | None = None,
    skill_target: str | None = None,
    difficulty: str | None = None,
    search: str | None = None,
    db: AsyncClient = Depends(get_db),
):
    q = db.table("question_bank").select("*").eq("is_active", True).order("created_at", desc=True)
    if template_id:
        q = q.eq("template_id", template_id)
    if tool_type:
        q = q.eq("tool_type", tool_type)
    if skill_target:
        q = q.eq("skill_target", skill_target)
    if difficulty:
        q = q.eq("difficulty", difficulty)
    res = await q.limit(200).execute()
    rows = res.data or []

    # Client-side search filter (FTS can be done in DB but this is simpler)
    if search:
        s = search.lower()
        rows = [r for r in rows if s in (r.get("body") or "").lower() or
                any(s in t.lower() for t in (r.get("tags") or []))]
    return rows


@router.post("/question-bank")
async def create_bank_question(body: QuestionBankCreate, db: AsyncClient = Depends(get_db)):
    payload = body.model_dump()
    res = await db.table("question_bank").insert(payload).execute()
    return res.data[0]


@router.post("/question-bank/bulk")
async def bulk_create_bank_questions(
    questions: list[QuestionBankCreate],
    db: AsyncClient = Depends(get_db),
):
    if not questions:
        raise HTTPException(400, "No questions provided")
    rows = [q.model_dump() for q in questions]
    try:
        res = await db.table("question_bank").insert(rows).execute()
    except Exception as e:
        raise HTTPException(422, detail=str(e))
    return {"created": len(res.data or [])}


@router.patch("/question-bank/{question_id}")
async def update_bank_question(
    question_id: str,
    body: QuestionBankUpdate,
    db: AsyncClient = Depends(get_db),
):
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(400, "No fields to update")
    res = await db.table("question_bank").update(payload).eq("id", question_id).execute()
    if not res.data:
        raise HTTPException(404, "Question not found")
    return res.data[0]


@router.delete("/question-bank/{question_id}")
async def delete_bank_question(question_id: str, db: AsyncClient = Depends(get_db)):
    await db.table("question_bank").update({"is_active": False}).eq("id", question_id).execute()
    return {"message": "Question deactivated"}


# ── Question Sets (P3 B2) — reusable groups of bank questions ────────────────

@router.get("/question-sets")
async def list_question_sets(db: AsyncClient = Depends(get_db)):
    res = await db.table("question_sets").select("*").order("created_at", desc=True).execute()
    sets = res.data or []
    # attach item counts
    for s in sets:
        cnt = await db.table("question_set_items").select("question_id").eq("set_id", s["id"]).execute()
        s["item_count"] = len(cnt.data or [])
    return sets


@router.post("/question-sets")
async def create_question_set(body: QuestionSetCreate, db: AsyncClient = Depends(get_db)):
    try:
        res = await db.table("question_sets").insert(body.model_dump()).execute()
    except Exception as e:
        raise HTTPException(503, f"Question sets are unavailable — has migration 015 been applied? ({str(e)[:120]})")
    return res.data[0]


@router.get("/question-sets/{set_id}")
async def get_question_set(set_id: str, db: AsyncClient = Depends(get_db)):
    s = await db.table("question_sets").select("*").eq("id", set_id).execute()
    if not s.data:
        raise HTTPException(404, "Question set not found")
    items = await db.table("question_set_items").select("question_id, sort_order").eq("set_id", set_id).order("sort_order").execute()
    ordered_ids = [r["question_id"] for r in (items.data or [])]
    questions = []
    if ordered_ids:
        qres = await db.table("question_bank").select("*").in_("id", ordered_ids).execute()
        by_id = {q["id"]: q for q in (qres.data or [])}
        questions = [by_id[i] for i in ordered_ids if i in by_id]   # preserve set order
    return {**s.data[0], "questions": questions}


@router.post("/question-sets/{set_id}/items")
async def add_questions_to_set(set_id: str, body: QuestionSetItemsBody, db: AsyncClient = Depends(get_db)):
    if not body.question_ids:
        raise HTTPException(400, "No question_ids provided")
    # append after existing items, preserving order via sort_order
    existing = await db.table("question_set_items").select("question_id").eq("set_id", set_id).execute()
    base = len(existing.data or [])
    rows = [{"set_id": set_id, "question_id": qid, "sort_order": base + i} for i, qid in enumerate(body.question_ids)]
    # upsert to tolerate re-adding the same question
    await db.table("question_set_items").upsert(rows, on_conflict="set_id,question_id").execute()
    return {"added": len(rows)}


@router.delete("/question-sets/{set_id}/items/{question_id}")
async def remove_question_from_set(set_id: str, question_id: str, db: AsyncClient = Depends(get_db)):
    await db.table("question_set_items").delete().eq("set_id", set_id).eq("question_id", question_id).execute()
    return {"message": "Removed"}


@router.delete("/question-sets/{set_id}")
async def delete_question_set(set_id: str, db: AsyncClient = Depends(get_db)):
    await db.table("question_sets").delete().eq("id", set_id).execute()
    return {"message": "Question set deleted"}


# ── P3 L2: Editable email templates ──────────────────────────────────────────

class EmailTemplateBody(BaseModel):
    subject: str
    html_body: str


_DEFAULT_INVITATION_SUBJECT = "You're invited to complete the {{assessment_title}} assessment"
_DEFAULT_INVITATION_HTML = """<div style="font-family:Inter,-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;background:#ffffff;color:#111827">
  <h1 style="margin:0 0 16px;font-size:22px;color:#1d4ed8">Masar Assessment</h1>
  <p style="font-size:16px;margin:0 0 8px">Hi {{candidate_name}},</p>
  <p style="font-size:14px;color:#374151;line-height:1.6;margin:0 0 20px">
    You have been invited to complete the <strong>{{assessment_title}}</strong> assessment.
    It takes approximately {{time_limit}} minutes.
  </p>
  <p style="text-align:center;margin:0 0 24px">
    <a href="{{invite_url}}" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-weight:600;padding:14px 36px;border-radius:10px">Start Assessment</a>
  </p>
  <p style="font-size:12px;color:#9ca3af;line-height:1.6">
    If the button does not work, copy and paste this link into your browser:<br>
    <a href="{{invite_url}}" style="color:#2563eb;word-break:break-all">{{invite_url}}</a>
  </p>
</div>"""


def _default_email_template(key: str) -> dict:
    """Sensible built-in default (invitation) used when no DB row exists / table missing."""
    return {"key": key, "subject": _DEFAULT_INVITATION_SUBJECT, "html_body": _DEFAULT_INVITATION_HTML}


@router.get("/email-templates/{key}")
async def get_email_template(key: str, db: AsyncClient = Depends(get_db)):
    try:
        res = await db.table("email_templates").select("key, subject, html_body").eq("key", key).execute()
        if res.data:
            row = res.data[0]
            return {"key": row["key"], "subject": row["subject"], "html_body": row["html_body"]}
    except Exception:
        pass
    return _default_email_template(key)


@router.put("/email-templates/{key}")
async def save_email_template(key: str, body: EmailTemplateBody, db: AsyncClient = Depends(get_db)):
    row = {
        "key": key,
        "subject": body.subject,
        "html_body": body.html_body,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        res = await db.table("email_templates").upsert(row, on_conflict="key").execute()
        if res.data:
            r = res.data[0]
            return {"key": r["key"], "subject": r["subject"], "html_body": r["html_body"]}
    except Exception:
        pass
    # Table missing / write failed — echo the submitted template (never 500)
    return {"key": key, "subject": body.subject, "html_body": body.html_body}


# ── P3 P1: Assessment pipelines ──────────────────────────────────────────────

class PipelineCreate(BaseModel):
    name: str
    description: str | None = None


class PipelineStagesBody(BaseModel):
    template_ids: list[str]


class PipelineEnrollBody(BaseModel):
    emails: list[str]
    names: dict[str, str] = {}


async def _pipeline_stages(db: AsyncClient, pipeline_id: str) -> list[dict]:
    """Ordered stages [{template_id, title, stage_order}] for a pipeline. []-safe."""
    try:
        sres = await (
            db.table("pipeline_stages")
            .select("template_id, stage_order")
            .eq("pipeline_id", pipeline_id)
            .order("stage_order")
            .execute()
        )
        stages = sres.data or []
    except Exception:
        return []
    if not stages:
        return []
    tids = [s["template_id"] for s in stages]
    title_map: dict[str, str] = {}
    try:
        tres = await db.table("assessment_templates").select("id, title").in_("id", tids).execute()
        title_map = {t["id"]: t.get("title") for t in (tres.data or [])}
    except Exception:
        title_map = {}
    return [
        {
            "template_id": s["template_id"],
            "title": title_map.get(s["template_id"]) or "",
            "stage_order": s["stage_order"],
        }
        for s in stages
    ]


@router.get("/pipelines")
async def list_pipelines(db: AsyncClient = Depends(get_db)):
    try:
        pres = await db.table("assessment_pipelines").select("*").order("created_at", desc=True).execute()
        pipelines = pres.data or []
    except Exception:
        return []
    out: list[dict] = []
    for p in pipelines:
        stages = await _pipeline_stages(db, p["id"])
        try:
            eres = await db.table("pipeline_enrollments").select("id").eq("pipeline_id", p["id"]).execute()
            enrollment_count = len(eres.data or [])
        except Exception:
            enrollment_count = 0
        out.append({
            "id": p["id"],
            "name": p["name"],
            "description": p.get("description"),
            "is_active": p.get("is_active", True),
            "created_at": p.get("created_at"),
            "stages": stages,
            "enrollment_count": enrollment_count,
        })
    return out


@router.post("/pipelines")
async def create_pipeline(body: PipelineCreate, db: AsyncClient = Depends(get_db)):
    payload = {"name": body.name, "description": body.description}
    try:
        res = await db.table("assessment_pipelines").insert(payload).execute()
    except Exception as e:
        raise HTTPException(503, f"Pipelines are unavailable — has migration 016 been applied? ({str(e)[:120]})")
    return res.data[0]


@public_router.get("/status")
async def my_pipelines(email: str, db: AsyncClient = Depends(get_db)):
    """A candidate's ACTIVE enrollments with per-stage token + completion flag.
    Candidate-facing (mounted ungated at /pipelines/status), so no admin required."""
    email_n = (email or "").strip().lower()
    if not email_n:
        return []
    try:
        eres = await (
            db.table("pipeline_enrollments")
            .select("pipeline_id")
            .eq("candidate_email", email_n)
            .eq("status", "active")
            .execute()
        )
        enrollments = eres.data or []
    except Exception:
        return []
    if not enrollments:
        return []
    pipeline_ids = list({en["pipeline_id"] for en in enrollments})

    name_map: dict[str, str] = {}
    try:
        pres = await db.table("assessment_pipelines").select("id, name").in_("id", pipeline_ids).execute()
        name_map = {p["id"]: p.get("name") for p in (pres.data or [])}
    except Exception:
        name_map = {}

    done_tids: set = set()
    try:
        sres = await (
            db.table("candidate_sessions")
            .select("template_id")
            .eq("candidate_email", email_n)
            .eq("status", "completed")
            .execute()
        )
        done_tids = {s["template_id"] for s in (sres.data or []) if s.get("template_id")}
    except Exception:
        done_tids = set()

    out: list[dict] = []
    for pid in pipeline_ids:
        try:
            sgres = await (
                db.table("pipeline_stages")
                .select("template_id, stage_order")
                .eq("pipeline_id", pid)
                .order("stage_order")
                .execute()
            )
            stage_rows = sgres.data or []
        except Exception:
            stage_rows = []
        tids = [s["template_id"] for s in stage_rows]
        tmeta: dict[str, dict] = {}
        if tids:
            try:
                tres = await (
                    db.table("assessment_templates")
                    .select("id, title, public_link_token")
                    .in_("id", tids)
                    .execute()
                )
                tmeta = {t["id"]: t for t in (tres.data or [])}
            except Exception:
                tmeta = {}
        stages = [
            {
                "template_id": s["template_id"],
                "title": (tmeta.get(s["template_id"]) or {}).get("title") or "",
                "token": (tmeta.get(s["template_id"]) or {}).get("public_link_token"),
                "done": s["template_id"] in done_tids,
            }
            for s in stage_rows
        ]
        out.append({"pipeline_id": pid, "name": name_map.get(pid) or "", "stages": stages})
    return out


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str, db: AsyncClient = Depends(get_db)):
    try:
        pres = await db.table("assessment_pipelines").select("*").eq("id", pipeline_id).execute()
    except Exception:
        raise HTTPException(404, "Pipeline not found")
    if not pres.data:
        raise HTTPException(404, "Pipeline not found")
    p = pres.data[0]
    stages = await _pipeline_stages(db, pipeline_id)
    try:
        eres = await (
            db.table("pipeline_enrollments")
            .select("candidate_email, candidate_name, current_stage, status")
            .eq("pipeline_id", pipeline_id)
            .execute()
        )
        enrollments = eres.data or []
    except Exception:
        enrollments = []
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p.get("description"),
        "is_active": p.get("is_active", True),
        "stages": stages,
        "enrollments": enrollments,
    }


@router.put("/pipelines/{pipeline_id}/stages")
async def set_pipeline_stages(pipeline_id: str, body: PipelineStagesBody, db: AsyncClient = Depends(get_db)):
    # Dedup while preserving order (PK is (pipeline_id, template_id))
    ordered: list[str] = []
    seen: set[str] = set()
    for tid in body.template_ids:
        if tid and tid not in seen:
            seen.add(tid)
            ordered.append(tid)
    try:
        await db.table("pipeline_stages").delete().eq("pipeline_id", pipeline_id).execute()
        rows = [{"pipeline_id": pipeline_id, "template_id": tid, "stage_order": i} for i, tid in enumerate(ordered)]
        if rows:
            await db.table("pipeline_stages").insert(rows).execute()
        return {"stages_set": len(rows)}
    except Exception:
        return {"stages_set": 0}


@router.post("/pipelines/{pipeline_id}/enroll")
async def enroll_in_pipeline(pipeline_id: str, body: PipelineEnrollBody, db: AsyncClient = Depends(get_db)):
    names = body.names or {}
    rows: list[dict] = []
    seen: set[str] = set()
    for e in body.emails:
        email = (e or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        rows.append({
            "pipeline_id": pipeline_id,
            "candidate_email": email,
            "candidate_name": names.get(e) or names.get(email),
            "current_stage": 0,
            "status": "active",
        })
    if not rows:
        return {"enrolled": 0}
    try:
        res = await db.table("pipeline_enrollments").insert(rows).execute()
        return {"enrolled": len(res.data or [])}
    except Exception:
        return {"enrolled": 0}


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str, db: AsyncClient = Depends(get_db)):
    try:
        await db.table("assessment_pipelines").delete().eq("id", pipeline_id).execute()
    except Exception:
        pass
    return {"message": "Pipeline deleted"}
