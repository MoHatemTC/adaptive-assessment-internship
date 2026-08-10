"""Cross-modality orchestration.

Public surface:

    Orchestrator        the loop: seed, fill queue, choose variable, grade, update, finalise
    GraderAgent         routes a response to the grader its modality requires
    JsonUnifiedBank     the multi-modality bank behind a repository seam
    GradedOutcome       what every modality reduces to
    PropagationPort     where one response's outcomes reach the competency graph
"""

from app.services.orchestrator.bank import JsonUnifiedBank, UnifiedBankRepository
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.outcome import GradedOutcome, graded_posterior_update
from app.services.orchestrator.propagation_port import (
    InProcessPropagation,
    PropagationPort,
    PropagationResult,
)
from app.services.orchestrator.queue import CandidateQueue

__all__ = [
    "CandidateQueue",
    "GradedOutcome",
    "GraderAgent",
    "InProcessPropagation",
    "JsonUnifiedBank",
    "Orchestrator",
    "PropagationPort",
    "PropagationResult",
    "UnifiedBankRepository",
    "graded_posterior_update",
]
