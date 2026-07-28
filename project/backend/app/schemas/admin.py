from pydantic import BaseModel
from typing import Any
from datetime import datetime


SUPPORTED_LANGUAGES = [
    "python", "javascript", "typescript", "java", "cpp", "csharp",
    "go", "rust", "sql", "kotlin", "swift", "ruby", "php", "r",
]

class ToolConfig(BaseModel):
    mcq: bool = True
    mcq_count: int | None = None          # None → Planner proposes
    voice: bool = True
    voice_count: int | None = None        # None → Planner proposes
    coding: bool = True
    coding_count: int | None = None       # None → Planner proposes
    coding_language: str = "python"
    task: bool = False
    visualization: bool = True
    visualization_count: int | None = None  # None → Planner proposes


class TaskConfig(BaseModel):
    brief: str = ""
    deliverables: list[str] = []
    rubric: list[dict[str, Any]] = []
    time_limit_minutes: int = 30


class TemplateCreate(BaseModel):
    title: str
    description: str | None = None
    track: str | None = None
    admin_prompt: str | None = None
    time_limit_minutes: int = 60
    assessment_type: str = "track"          # legacy single mode (kept for back-compat)
    modes: list[str] = []                   # P1: ordered multi-mode (career/technical/behavioural/…)
    adaptivity_level: str = "high"          # P1: low | medium | high
    intake_config: dict[str, Any] = {}      # P1: {cv_required: bool, questions: [...]}
    tool_config: dict[str, Any] = {}        # competencies live here as [{name, tag}]
    task_config: dict[str, Any] | None = None
    type_configs: dict[str, Any] = {}       # P4: per-type overrides {type: {adaptivity, question_set_id, language, length, competencies}}


class TemplateUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    track: str | None = None
    admin_prompt: str | None = None
    time_limit_minutes: int | None = None
    assessment_type: str | None = None
    modes: list[str] | None = None
    adaptivity_level: str | None = None
    intake_config: dict[str, Any] | None = None
    tool_config: dict[str, Any] | None = None
    task_config: dict[str, Any] | None = None
    type_configs: dict[str, Any] | None = None


class StepResponse(BaseModel):
    id: str
    template_id: str
    position: int
    step_type: str
    config: dict[str, Any]
    gate_threshold: float | None


class TemplateResponse(BaseModel):
    id: str
    title: str
    description: str | None
    track: str | None
    admin_prompt: str | None
    time_limit_minutes: int = 60
    assessment_type: str = "track"
    modes: list[str] = []
    adaptivity_level: str = "high"
    intake_config: dict[str, Any] = {}
    tool_config: dict[str, Any]
    task_config: dict[str, Any] | None = None
    type_configs: dict[str, Any] = {}
    is_published: bool
    public_link_token: str | None
    steps: list[StepResponse] = []
    created_at: str
    updated_at: str


class StepCreate(BaseModel):
    position: int
    step_type: str
    config: dict[str, Any] = {}
    gate_threshold: float | None = None


class QuestionCreate(BaseModel):
    question_type: str
    body: str
    skill: str
    difficulty: str
    track: str | None = None
    options: list[dict[str, Any]] | None = None
    answer_key: dict[str, Any] | None = None
    rubric: dict[str, Any] | None = None
    image_url: str | None = None


class QuestionResponse(BaseModel):
    id: str
    question_type: str
    body: str
    skill: str
    difficulty: str
    track: str | None
    options: list[dict[str, Any]] | None
    answer_key: dict[str, Any] | None
    image_url: str | None
    is_active: bool
    created_at: str


class PublishResponse(BaseModel):
    public_link_token: str
    public_url: str


class MessageResponse(BaseModel):
    message: str
