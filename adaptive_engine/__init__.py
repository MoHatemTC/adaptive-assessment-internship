"""adaptive_engine — the stateless boundary around the proven assessment engine.

The engine is a pure state transition:

    compiled assessment + previous adaptive state + optional response
                               |
                 new adaptive state + decision

Every measurement decision — per-competency grading rollup, person-fit checks, graph
propagation and coverage gates, the shipped stopping rules, the full report — is made by
``cat_engine``'s existing, battle-tested Orchestrator. This package contributes the
boundary a host backend needs: caller-supplied content, a JSON-safe state the host
persists, strict response validation, and host-side grading input for non-mcq
modalities. It holds no sessions, no stores, no registries, and does no network I/O.

The complete lifecycle:

    from adaptive_engine import (
        InitialCompetency,
        QuestionResponse,
        advance_assessment,
        compile_assessment,
        start_assessment,
    )

    compiled = compile_assessment(definition)

    decision = start_assessment(
        compiled,
        initial_competencies={"C1": InitialCompetency(level=3, confidence=0.8)},
    )
    save_state(decision.state)

    while decision.status == "question":
        response = QuestionResponse(
            question_id=decision.question.item.item_id,
            answer=get_answer(decision.question),
        )
        decision = advance_assessment(compiled, state=load_state(), response=response)
        save_state(decision.state)

    show_report(decision.report)
"""

from adaptive_engine.compilation import (
    CompiledAssessment,
    compile_assessment,
    content_hash_of,
)
from adaptive_engine.errors import (
    AdaptiveEngineError,
    AssessmentFinished,
    InvalidAnswer,
    InvalidDefinition,
    InvalidInitialCompetency,
    InvalidState,
    StaleResponse,
    StateMismatch,
)
from adaptive_engine.models import (
    AssessmentDefinition,
    BankItem,
    CompetencyDeclaration,
    CompetencyGraph,
    GraphDerivation,
    GraphPolicy,
    MeasurementPolicy,
)
from adaptive_engine.runtime import (
    AdaptiveDecision,
    GradedAnswer,
    InitialCompetency,
    QuestionResponse,
    advance_assessment,
    start_assessment,
)
from adaptive_engine.state import AdaptiveState
from cat_engine.contracts import AssessmentReportDTO, PresentedItemDTO, PresentingDTO

__version__ = "2.0.0"

__all__ = [
    "AdaptiveDecision",
    "AdaptiveEngineError",
    "AdaptiveState",
    "AssessmentDefinition",
    "AssessmentFinished",
    "AssessmentReportDTO",
    "BankItem",
    "CompetencyDeclaration",
    "CompetencyGraph",
    "CompiledAssessment",
    "GradedAnswer",
    "GraphDerivation",
    "GraphPolicy",
    "InitialCompetency",
    "InvalidAnswer",
    "InvalidDefinition",
    "InvalidInitialCompetency",
    "InvalidState",
    "MeasurementPolicy",
    "PresentedItemDTO",
    "PresentingDTO",
    "QuestionResponse",
    "StaleResponse",
    "StateMismatch",
    "__version__",
    "advance_assessment",
    "compile_assessment",
    "content_hash_of",
    "start_assessment",
]
