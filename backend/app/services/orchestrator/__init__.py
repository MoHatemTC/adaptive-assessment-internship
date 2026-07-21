"""Cross-modality orchestration.

Currently the shared contract only. The orchestration policy — which modality assesses
which competency, in what order, and how two estimates on different scales combine into
one report — is pending architecture and is deliberately not guessed at here.
"""

from app.services.orchestrator.engine import AssessmentEngine, EngineRegistry, Modality

__all__ = ["AssessmentEngine", "EngineRegistry", "Modality"]
