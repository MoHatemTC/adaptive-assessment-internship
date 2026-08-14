"""The layer runs with no web framework, no database, and none of the legacy machinery.

The measurement brain (`cat_engine.engine`) is a dependency by design now — reuse, not a
copy — so this no longer asserts "cat_engine is never imported". What it pins instead:
running a complete assessment touches no session store, no facade, no wiring cache, no
database driver and no web framework, and performs no network I/O (the run would hang or
fail on the placeholder endpoints if it tried).
"""

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
    "e2b",
    "e2b_code_interpreter",
    # The legacy stateful machinery must stay out of the stateless path entirely.
    "cat_engine.facade",
    "cat_engine.wiring",
    "cat_engine.stores",
    "cat_engine.ingest",
    "cat_engine.live",
)


def test_a_full_run_touches_no_infrastructure():
    program = textwrap.dedent(
        f"""
        import sys

        from adaptive_engine import (
            AssessmentDefinition, QuestionResponse,
            advance_assessment, compile_assessment, start_assessment,
        )

        items = [
            {{
                "item_id": f"q{{i}}", "modality": "mcq",
                "measures": [{{"variable": "c", "weight": 1.0}}],
                "cat": {{"a": 2.0, "b": float(i - 2), "c": 0.0}},
                "mcq": {{"stem": "?", "options": ["a", "b"], "answer_index": 0}},
            }}
            for i in range(5)
        ]
        compiled = compile_assessment(
            AssessmentDefinition(assessment_id="iso", version="v1", items=items)
        )
        decision = start_assessment(compiled, seed=1)
        while decision.status == "question":
            decision = advance_assessment(
                compiled, state=decision.state,
                response=QuestionResponse(
                    question_id=decision.question.item.item_id, answer=0
                ),
            )
        assert decision.report is not None

        roots = {FORBIDDEN_IMPORT_ROOTS!r}
        loaded = sorted(
            name for name in sys.modules
            if any(name == root or name.startswith(root + ".") for root in roots)
        )
        assert not loaded, f"infrastructure imported by the stateless path: {{loaded}}"
        """
    )
    completed = subprocess.run(  # noqa: S603 — our own interpreter, our own program
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
