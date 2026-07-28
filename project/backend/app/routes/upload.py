from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
from supabase import AsyncClient
from openai import APIError

from app.db import get_db
from app.services.cv_parser import parse_cv, looks_like_cv
from app.services.task_scorer import score_task_submission
from app.services.camera_proctor import analyze_frame, invalidate_ref_cache

router = APIRouter()


@router.post("/cv")
async def upload_cv(
    file: UploadFile = File(...),
    session_id: str = "",
    db: AsyncClient = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    try:
        parsed = await parse_cv(pdf_bytes)
    except APIError:
        # Transient LLM/gateway failure (already retried in chat_completion).
        raise HTTPException(
            status_code=503,
            detail="The AI service is busy right now. Please upload your CV again in a moment.",
        )
    except Exception:
        raise HTTPException(
            status_code=422,
            detail="We couldn't read that file. Please upload your CV as a text-based PDF and try again.",
        )

    if not looks_like_cv(parsed):
        raise HTTPException(
            status_code=422,
            detail="That doesn't look like a CV. Please upload your actual résumé as a PDF.",
        )

    res = await db.table("cv_uploads").insert({
        "session_id": session_id or None,
        "file_name": file.filename,
        "parsed_json": parsed,
        "raw_text": parsed.get("raw_summary") or "",
    }).execute()

    cv_upload_id: str = res.data[0]["id"]

    if session_id:
        await db.table("candidate_sessions").update(
            {"cv_upload_id": cv_upload_id}
        ).eq("id", session_id).execute()

    return {
        "cv_upload_id": cv_upload_id,
        "parsed_summary": parsed.get("raw_summary") or "",
        "skills": parsed.get("skills") or [],
    }


@router.post("/task-submission/{session_id}")
async def submit_task(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncClient = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files accepted")

    zip_bytes = await file.read()

    q_res = (
        await db.table("generated_questions")
        .select("*")
        .eq("session_id", session_id)
        .eq("tool_type", "task")
        .order("question_number", desc=True)
        .limit(1)
        .execute()
    )
    if not q_res.data:
        raise HTTPException(status_code=404, detail="No task question found for this session")

    question = q_res.data[0]
    payload: dict = question.get("payload") or {}
    rubric: list = payload.get("rubric") or []
    task_brief: str = payload.get("brief") or ""

    cv_res = (
        await db.table("cv_uploads")
        .select("parsed_json")
        .eq("session_id", session_id)
        .execute()
    )
    cv_data: dict = (cv_res.data[0].get("parsed_json") or {}) if cv_res.data else {}
    cv_summary: str = cv_data.get("raw_summary") or ""

    result = await score_task_submission(zip_bytes, rubric, task_brief, cv_summary)
    return result


# ── Reference photo ────────────────────────────────────────────

class ReferencePhotoRequest(BaseModel):
    session_id: str
    user_id: str
    frame_base64: str


@router.post("/reference-photo")
async def upload_reference_photo(
    body: ReferencePhotoRequest,
    db: AsyncClient = Depends(get_db),
):
    """Store identity baseline photo on the user profile."""
    await db.table("user_profiles").upsert({
        "id": body.user_id,
        "reference_photo_base64": body.frame_base64,
    }).execute()
    # Invalidate module-level cache so the next frame analysis fetches the fresh photo
    invalidate_ref_cache(body.user_id)
    return {"ok": True}


# ── Camera frame analysis ──────────────────────────────────────

class FrameRequest(BaseModel):
    session_id: str
    user_id: str
    frame_base64: str


@router.post("/frame")
async def analyze_camera_frame(
    body: FrameRequest,
    db: AsyncClient = Depends(get_db),
):
    result = await analyze_frame(
        base64_image=body.frame_base64,
        session_id=body.session_id,
        user_id=body.user_id,
        db=db,
    )
    return result
