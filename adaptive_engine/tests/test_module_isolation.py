"""The engine is only adaptive logic: it imports and runs with no infrastructure at all."""

from __future__ import annotations

import subprocess
import sys
import textwrap

FORBIDDEN_IMPORT_ROOTS = (
    "fastapi",
    "flask",
    "django",
    "starlette",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "httpx",
    "openai",
    "requests",
    "cat_engine",  # the legacy stateful module must not be a hidden dependency
)


def test_the_engine_imports_and_runs_without_a_database_or_web_framework():
    """Run a complete assessment in a fresh interpreter, then assert nothing
    infrastructure-shaped was ever imported."""
    program = textwrap.dedent(
        f"""
        import sys

        from adaptive_engine import (
            AssessmentDefinition, Competency, Measurement, Question, QuestionResponse,
            advance_assessment, compile_assessment, start_assessment,
        )

        definition = AssessmentDefinition(
            assessment_id="iso", version="v1",
            competencies=[Competency(competency_id="c")],
            questions=[
                Question(
                    question_id=f"q{{i}}", prompt="?", options=["a", "b"],
                    answer_index=0, measures=[Measurement(competency_id="c")],
                    difficulty=float(i - 2), discrimination=2.0,
                )
                for i in range(5)
            ],
        )
        compiled = compile_assessment(definition)
        decision = start_assessment(compiled, seed=1)
        while decision.status == "question":
            decision = advance_assessment(
                compiled, state=decision.state,
                response=QuestionResponse(
                    question_id=decision.question.question_id, answer=0
                ),
            )
        assert decision.report is not None

        roots = {FORBIDDEN_IMPORT_ROOTS!r}
        loaded = sorted(
            name for name in sys.modules
            if any(name == root or name.startswith(root + ".") for root in roots)
        )
        assert not loaded, f"infrastructure imported by the engine: {{loaded}}"
        """
    )
    completed = subprocess.run(  # noqa: S603 — our own interpreter, our own program
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
