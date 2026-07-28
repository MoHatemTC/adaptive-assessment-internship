from pydantic import BaseModel
from typing import Any


class SkillScore(BaseModel):
    score: float      # 0-5
    evidence: list[str]  # key quotes / observations


class FinalReport(BaseModel):
    session_id: str
    candidate_name: str
    candidate_email: str
    track: str | None
    skill_scores: dict[str, SkillScore]   # thinking, soft, work, digital_ai, growth
    total_score: float
    placement: str       # "BEGINNER" | "PRO"
    feedback: str
    recommendations: list[str]
    integrity_status: str
    proctoring_flags: list[dict[str, Any]]


class ReportResponse(BaseModel):
    id: str
    session_id: str
    skill_scores: dict[str, Any]
    total_score: float
    placement: str
    feedback: str
    recommendations: list[str]
    integrity_status: str
    email_sent: bool
    created_at: str
