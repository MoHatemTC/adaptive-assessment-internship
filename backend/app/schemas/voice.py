"""Typed boundary of the voice / open-ended assessment half."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

OutcomeStatus = Literal[
    "complete", "truncated", "unscorable", "infrastructure_error"
]
PromptDependency = Literal["independent", "probe_supported", "probe_dependent"]
QuoteTier = Literal["exact", "normalized", "subsequence", "fuzzy", "described"]


class VoiceTurn(BaseModel):
    turn_id: str
    role: Literal["candidate", "interviewer"]
    text: str
    # None = ASR provider did not supply a confidence; do not invent 0.9/0.95.
    transcript_confidence: float | None = None
    truncated: bool = False


class VoiceResponsePackage(BaseModel):
    """Frozen transport output before grading. No audio bytes — transcript only."""

    item_id: str
    outcome_status: OutcomeStatus = "complete"
    reason_code: str = ""
    turns: list[VoiceTurn] = Field(default_factory=list)
    total_speech_seconds: float = 0.0
    # None when no turn carried a measured ASR confidence.
    mean_transcript_confidence: float | None = None
    word_count: int = 0
    explicit_decline: bool = False
    cut_off: bool = False
    live_text: str = ""
    final_text: str | None = None

    @property
    def transcript(self) -> str:
        return (self.final_text or self.live_text or self._join_candidate()).strip()

    def _join_candidate(self) -> str:
        return "\n".join(t.text for t in self.turns if t.role == "candidate")


class CriterionEvidence(BaseModel):
    criterion_id: str
    competency_id: str
    raw_score: float
    maximum_score: float
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    prompt_dependency: PromptDependency = "independent"
    quote: str | None = None
    quote_turn_id: str | None = None
    quote_tier: QuoteTier | None = None
    description: str = ""


class VoiceEvaluation(BaseModel):
    item_id: str
    rubric_id: str
    rubric_version: str = "1.0"
    criterion_evidence: list[CriterionEvidence] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    degraded: bool = False
    evaluation_confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    overall_rationale: str = ""
    raw_reply: dict[str, Any] = Field(default_factory=dict)


class VoiceCompetencyEvidence(BaseModel):
    competency_id: str
    score: float
    confidence: float
    evidence_strength: float
    coverage: float
    source_item_id: str = ""


class GradedVoiceResponse(BaseModel):
    """What GraderAgent._grade_open receives — package + evaluation, no I/O left."""

    package: VoiceResponsePackage
    evaluation: VoiceEvaluation
    rubric: dict[str, Any] = Field(default_factory=dict)
