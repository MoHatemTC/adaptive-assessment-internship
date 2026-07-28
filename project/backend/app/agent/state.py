from typing import Any, TypedDict


class AgentState(TypedDict):
    session_id: str
    candidate_name: str
    candidate_email: str
    track: str | None
    blueprint: dict[str, Any]           # generated assessment plan
    messages: list[dict[str, Any]]      # full conversation history
    current_tool: str | None            # active tool type
    tool_payload: dict[str, Any] | None # data sent to frontend tool
    pending_answer: dict[str, Any] | None  # answer received from frontend
    asked_question_ids: list[str]       # Qdrant IDs already used
    ability_estimates: dict[str, float] # per-skill theta (0-5)
    answers: list[dict[str, Any]]       # graded answers so far
    proctoring_flags: list[dict[str, Any]]
    session_complete: bool
    error: str | None
