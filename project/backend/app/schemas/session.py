from pydantic import BaseModel
from typing import Any


class SessionStartRequest(BaseModel):
    token: str
    candidate_name: str
    candidate_email: str
    cv_upload_id: str | None = None  # optional when the admin sets cv_required=false
    track: str | None = None
    consent_camera: bool
    consent_voice: bool
    consent_data: bool
    intake_answers: dict[str, Any] = {}  # pre-assessment intake form answers


class SessionStateResponse(BaseModel):
    session_id: str
    status: str
    integrity_status: str
    current_step: str | None = None
    progress: dict[str, Any] = {}


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str
    tool_result: dict[str, Any] | None = None  # answer from a tool widget


class SetRetroData(BaseModel):
    text: str
    completed_type: str
    scores: list[float]


class AgentMessage(BaseModel):
    role: str   # "agent" | "user"
    content: str
    tool_type: str | None = None
    tool_payload: dict[str, Any] | None = None
    set_retro: SetRetroData | None = None  # stack-boundary retro, rendered as StackFeedbackCard


class ChatTurnResponse(BaseModel):
    session_id: str
    message: AgentMessage
    session_complete: bool = False
