import json
from fastapi import BackgroundTasks
from supabase import AsyncClient
from langgraph.graph import StateGraph, END

from app.agent.state import AgentState
from app.agent.nodes.blueprint import generate_blueprint
from app.agent.nodes.question_select import select_next_question
from app.agent.nodes.grader import grade_answer
from app.agent.nodes.judge import judge_grading
from app.agent.nodes.reporter import generate_report
from app.agent.tools.mcq_tool import build_mcq_payload, extract_mcq_answer
from app.agent.tools.diagram_tool import build_diagram_payload, extract_diagram_answer
from app.agent.tools.voice_tool import build_voice_payload, extract_voice_answer
from app.agent.tools.camera_tool import build_camera_payload, extract_camera_answer
from app.agent.tools.e2b_tool import build_coding_payload, extract_coding_answer, run_in_sandbox
from app.services.email import send_report_email
from app.config.settings import settings


TOOL_BUILDERS = {
    "mcq": build_mcq_payload,
    "diagram": build_diagram_payload,
    "voice": lambda q: build_voice_payload(q),
    "camera": lambda q: build_camera_payload(q),
    "coding": build_coding_payload,
}

TOOL_EXTRACTORS = {
    "mcq": extract_mcq_answer,
    "diagram": extract_diagram_answer,
    "voice": extract_voice_answer,
    "camera": extract_camera_answer,
    "coding": extract_coding_answer,
}


async def run_turn(
    session: dict,
    user_message: str,
    tool_result: dict | None,
    db: AsyncClient,
    background_tasks: BackgroundTasks,
) -> dict:
    """Single turn: load state, advance the graph, persist state, return response."""

    # ── Load state from session ──────────────────────────
    state: AgentState = {
        "session_id": session["id"],
        "candidate_name": session["candidate_name"],
        "candidate_email": session["candidate_email"],
        "track": session["track"],
        "blueprint": session.get("agent_state", {}).get("blueprint", {}),
        "messages": session.get("agent_state", {}).get("messages", []),
        "current_tool": session.get("agent_state", {}).get("current_tool"),
        "tool_payload": session.get("agent_state", {}).get("tool_payload"),
        "pending_answer": None,
        "asked_question_ids": session.get("asked_question_ids") or [],
        "ability_estimates": session.get("ability_estimates") or {},
        "answers": session.get("agent_state", {}).get("answers", []),
        "proctoring_flags": session.get("agent_state", {}).get("proctoring_flags", []),
        "session_complete": False,
        "error": None,
    }

    # Append user message
    state["messages"].append({"role": "user", "content": user_message})

    # ── Step 1: Generate blueprint if first turn ─────────
    if not state["blueprint"] or not state["blueprint"].get("tool_sequence"):
        tpl_res = await db.table("assessment_templates").select("*").eq("id", session["template_id"]).execute()
        tpl = tpl_res.data[0] if tpl_res.data else {}
        state["blueprint"] = {
            "template_id": tpl.get("id", ""),
            "admin_prompt": tpl.get("admin_prompt", ""),
            "enabled_tools": tpl.get("tool_config", {}),
        }
        state["blueprint"] = await generate_blueprint(state)

        # Persist blueprint
        bp_res = await db.table("blueprints").insert({
            "template_id": session["template_id"],
            "content": state["blueprint"],
        }).execute()

        # Update session with blueprint id
        await db.table("candidate_sessions").update({
            "blueprint_id": bp_res.data[0]["id"],
            "status": "in_progress",
            "started_at": "now()",
        }).eq("id", session["id"]).execute()

    # ── Step 2: Handle incoming tool result ──────────────
    current_tool = state.get("current_tool")
    if tool_result and current_tool:
        extractor = TOOL_EXTRACTORS.get(current_tool)
        if extractor:
            # For coding: run E2B first if not done
            if current_tool == "coding" and "test_results" not in tool_result:
                answer_key = tool_result.get("answer_key", {})
                test_cases = answer_key.get("test_cases", [])
                tool_result["test_results"] = await run_in_sandbox(
                    tool_result.get("code", ""), test_cases
                )
            state["pending_answer"] = extractor(tool_result)

        # Grade the answer
        grading = await grade_answer(state, db)
        await judge_grading(
            grading,
            state["pending_answer"].get("question_body", ""),
            state["pending_answer"].get("answer_text", ""),
        )

        # Update ability estimate for the skill
        skill = grading["skill"]
        prev = state["ability_estimates"].get(skill, 0.0)
        state["ability_estimates"][skill] = round((prev + grading["score"]) / 2, 2)
        state["answers"].append(grading)
        state["asked_question_ids"].append(grading["question_id"])

        # Persist answer
        await db.table("candidate_answers").insert({
            "session_id": session["id"],
            "question_id": grading["question_id"],
            "answer_text": state["pending_answer"].get("answer_text"),
            "answer_data": state["pending_answer"].get("answer_data"),
            "score": grading["score"],
            "skill": grading["skill"],
            "skill_scores": grading["skill_scores"],
            "grading_rationale": grading["rationale"],
        }).execute()

        state["current_tool"] = None
        state["tool_payload"] = None

    # ── Step 3: Pick next question or end ────────────────
    blueprint = state["blueprint"]
    tool_sequence = blueprint.get("tool_sequence", [])
    answered_count = len(state["answers"])

    if answered_count >= len(tool_sequence):
        # ── Assessment complete → generate report ────────
        state["session_complete"] = True
        report = await generate_report(state)

        # Persist report
        report_res = await db.table("final_reports").insert({
            "session_id": session["id"],
            "skill_scores": {k: {"score": v.score, "evidence": v.evidence} for k, v in report.skill_scores.items()},
            "total_score": report.total_score,
            "placement": report.placement,
            "feedback": report.feedback,
            "recommendations": report.recommendations,
            "integrity_status": report.integrity_status,
        }).execute()

        await db.table("candidate_sessions").update({
            "status": "completed",
            "completed_at": "now()",
        }).eq("id", session["id"]).execute()

        # Send email in background
        background_tasks.add_task(
            send_report_email,
            candidate_name=report.candidate_name,
            candidate_email=report.candidate_email,
            admin_email=settings.report_from_email,
            placement=report.placement,
            total_score=report.total_score,
            skill_scores={k: {"score": v.score} for k, v in report.skill_scores.items()},
            feedback=report.feedback,
            recommendations=report.recommendations,
            session_id=session["id"],
            base_url=settings.base_url,
        )

        agent_msg = {
            "role": "agent",
            "content": f"Well done, {state['candidate_name']}! Your assessment is complete. "
                       f"You've been placed as **{report.placement}** with a total score of {report.total_score:.1f}/25. "
                       f"Your full report is ready.",
            "tool_type": None,
            "tool_payload": None,
        }

    else:
        # ── Select and present next question ─────────────
        next_tool_type = tool_sequence[answered_count]
        question = await select_next_question(state, db)

        if not question:
            agent_msg = {
                "role": "agent",
                "content": "I'm moving to the next section of your assessment.",
                "tool_type": None,
                "tool_payload": None,
            }
        else:
            builder = TOOL_BUILDERS.get(next_tool_type)
            payload = builder(question) if builder else {}

            state["current_tool"] = next_tool_type
            state["tool_payload"] = payload

            intro = {
                "mcq": "Please select the best answer for the following question:",
                "diagram": "Look at the image below and answer the question:",
                "voice": "Now we'll move to a voice interview. Click the microphone when ready:",
                "camera": "This section requires your camera. Please ensure it is on:",
                "coding": "Time for a coding challenge. Write your solution below:",
            }.get(next_tool_type, "Please answer the following:")

            agent_msg = {
                "role": "agent",
                "content": intro,
                "tool_type": next_tool_type,
                "tool_payload": payload,
            }

    state["messages"].append(agent_msg)

    # ── Persist updated state ────────────────────────────
    await db.table("candidate_sessions").update({
        "agent_state": {
            "blueprint": state["blueprint"],
            "messages": state["messages"][-20:],  # keep last 20
            "current_tool": state.get("current_tool"),
            "tool_payload": state.get("tool_payload"),
            "answers": state["answers"],
            "proctoring_flags": state["proctoring_flags"],
        },
        "ability_estimates": state["ability_estimates"],
        "asked_question_ids": state["asked_question_ids"],
        "updated_at": "now()",
    }).eq("id", session["id"]).execute()

    return {
        "session_id": session["id"],
        "message": agent_msg,
        "session_complete": state["session_complete"],
    }
