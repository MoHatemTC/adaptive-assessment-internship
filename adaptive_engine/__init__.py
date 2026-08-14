"""adaptive_engine — a stateless adaptive assessment engine.

The engine is a pure state transition:

    assessment definition + previous adaptive state + optional response
                               |
                 new adaptive state + decision

It owns adaptive assessment logic only: definition validation, compilation, belief
initialization, question selection, evidence application, convergence, and the final
report. The host backend owns everything else — users, sessions, persistence,
assignments, transport, and external grading. There is no database, no HTTP layer, no
session store and no registry in this package, and importing it starts nothing.

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
        initial_competencies={"python": InitialCompetency(level=3, confidence=0.8)},
    )
    save_state(decision.state)

    while decision.status == "question":
        response = QuestionResponse(
            question_id=decision.question.question_id,
            answer=get_answer(decision.question),
        )
        decision = advance_assessment(compiled, state=load_state(), response=response)
        save_state(decision.state)

    show_report(decision.report)
"""

from adaptive_engine.compilation import CompiledAssessment, compile_assessment
from adaptive_engine.convergence import StopReason
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
from adaptive_engine.evidence import Evidence, GradedAnswer
from adaptive_engine.models import (
    AssessmentDefinition,
    AssessmentGraph,
    AssessmentPolicy,
    Competency,
    GraphEdge,
    Measurement,
    Question,
)
from adaptive_engine.report import AssessmentReport, CompetencyReport
from adaptive_engine.runtime import (
    AdaptiveDecision,
    InitialCompetency,
    PresentedQuestion,
    QuestionResponse,
    advance_assessment,
    present_question,
    start_assessment,
)
from adaptive_engine.state import AdaptiveState, CompetencyState, GraphNodeEvidence

__version__ = "1.0.0"

__all__ = [
    "AdaptiveDecision",
    "AdaptiveEngineError",
    "AdaptiveState",
    "AssessmentDefinition",
    "AssessmentFinished",
    "AssessmentGraph",
    "AssessmentPolicy",
    "AssessmentReport",
    "Competency",
    "CompetencyReport",
    "CompetencyState",
    "CompiledAssessment",
    "Evidence",
    "GradedAnswer",
    "GraphEdge",
    "GraphNodeEvidence",
    "InitialCompetency",
    "InvalidAnswer",
    "InvalidDefinition",
    "InvalidInitialCompetency",
    "InvalidState",
    "Measurement",
    "PresentedQuestion",
    "Question",
    "QuestionResponse",
    "StaleResponse",
    "StateMismatch",
    "StopReason",
    "__version__",
    "advance_assessment",
    "compile_assessment",
    "present_question",
    "start_assessment",
]
