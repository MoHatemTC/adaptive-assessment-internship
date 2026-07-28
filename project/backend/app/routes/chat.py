from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from supabase import AsyncClient
from openai import AsyncOpenAI

from app.db import get_db
from app.schemas.session import ChatTurnRequest, ChatTurnResponse
from app.agent.orchestrator import run_turn, _finalize_session
from app.services.grading import grade_code_llm
from app.config.settings import settings

router = APIRouter()


class RunCodeRequest(BaseModel):
    code: str
    challenge_id: str

class SubmitRequest(BaseModel):
    session_id: str

class VoiceProbeRequest(BaseModel):
    session_id: str
    question: str
    answer: str
    turn_number: int


@router.post("/submit")
async def submit_assessment(
    body: SubmitRequest,
    background_tasks: BackgroundTasks,
    db: AsyncClient = Depends(get_db),
):
    """Candidate-initiated early submission — finalize and generate report immediately."""
    res = await db.table("candidate_sessions").select("*").eq("id", body.session_id).execute()
    if not res.data:
        raise HTTPException(404, "Session not found")

    session = res.data[0]
    if session["status"] in ("completed", "flagged"):
        raise HTTPException(400, f"Session is already {session['status']}")

    state = session.get("agent_state") or {}
    answered_count = state.get("answered_count") or 0
    total_slots    = len((state.get("plan") or {}).get("slots") or [])

    try:
        agent_msg = await _finalize_session(
            session=session,
            answered_count=answered_count,
            total_slots=total_slots,
            elapsed_minutes=0,
            time_limit=session.get("time_limit_minutes") or 60,
            reason="submitted",
            db=db,
            background_tasks=background_tasks,
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(500, f"Finalization error: {str(e)}")

    return {
        "session_id":      body.session_id,
        "message":         agent_msg,
        "session_complete": True,
    }


@router.post("/voice-probe")
async def voice_probe(body: VoiceProbeRequest, db: AsyncClient = Depends(get_db)):
    """Generate a follow-up probe question for a live voice interview turn."""
    prompt = f"""You are conducting a live voice interview. Generate ONE short, natural follow-up question.

ORIGINAL QUESTION: {body.question}

CANDIDATE'S ANSWER (turn {body.turn_number}): {body.answer[:800]}

Rules:
1. If the answer is shallow or vague, ask for a specific example or elaboration
2. If it is good, probe a specific aspect the candidate mentioned that is interesting
3. Keep it short — one sentence, conversational, spoken aloud to a human
4. Do NOT repeat the original question or rephrase it
5. Sound like a human interviewer — curious and direct, not formal

Return ONLY the follow-up question text, nothing else."""

    llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
    res = await llm.chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=80,
    )
    follow_up = res.choices[0].message.content.strip()
    return {"follow_up": follow_up}


@router.post("/run-code")
async def run_code(body: RunCodeRequest, db: AsyncClient = Depends(get_db)):
    """Evaluate candidate code against the stored challenge using LLM."""
    res = await db.table("coding_challenges").select(
        "body, test_cases, expected_approach, constraints"
    ).eq("id", body.challenge_id).execute()
    if not res.data:
        raise HTTPException(404, "Challenge not found")
    result = await grade_code_llm(res.data[0], body.code)
    return result


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(
    body: ChatTurnRequest,
    background_tasks: BackgroundTasks,
    db: AsyncClient = Depends(get_db),
):
    # Load session
    res = await db.table("candidate_sessions").select("*").eq("id", body.session_id).execute()
    if not res.data:
        raise HTTPException(404, "Session not found")

    session = res.data[0]

    if session["status"] in ("completed", "flagged"):
        raise HTTPException(400, f"Session is already {session['status']}")

    # Run one LangGraph turn
    try:
        result = await run_turn(
            session=session,
            user_message=body.message,
            tool_result=body.tool_result,
            db=db,
            background_tasks=background_tasks,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Agent error: {str(e)}")

    return result
