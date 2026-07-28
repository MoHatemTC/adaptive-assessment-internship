from pydantic import BaseModel
from typing import Any


class Blueprint(BaseModel):
    template_id: str
    track: str
    total_questions: int
    tool_sequence: list[str]           # ["mcq", "mcq", "voice", "coding", ...]
    skill_weights: dict[str, float]    # {"thinking": 0.3, "soft": 0.2, ...}
    difficulty_start: str              # "easy" | "medium" | "hard"
    voice_time_limit_minutes: int = 10
    camera_time_limit_minutes: int = 10
    coding_time_limit_minutes: int = 30
    enabled_tools: dict[str, bool]


class GradingResult(BaseModel):
    question_id: str
    skill: str
    skill_scores: dict[str, float]    # scores per dimension from this answer
    score: float                       # 0-5
    rationale: str
    judge_approved: bool


class BlueprintGenerateRequest(BaseModel):
    template_id: str
    admin_prompt: str
    tool_config: dict[str, bool]
    track: str | None = None
