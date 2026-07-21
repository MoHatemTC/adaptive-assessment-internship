"""The tester harness, driven end to end through Streamlit's own test runner.

A UI that raises halfway through a session is worse than no UI: the tester loses the run
and, with it, whatever they were trying to observe. So the whole flow is exercised —
setup, answering both modalities, and the final report — with the sandbox and model
stubbed.

Two of these are regression tests for bugs the harness itself found:

    a module named app.py in the UI directory shadowed the backend's `app` PACKAGE, since
    Streamlit puts the script's directory on sys.path — every engine import failed;

    dataframes mixing ints with an em-dash placeholder in one column fail Arrow
    serialisation, so Streamlit silently falls back to a coerced render on every rerun.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.code_adaptive import session as code_session
from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from app.services.code_adaptive.llm_evaluator import LLMEvaluation

APP = Path(__file__).resolve().parent.parent.parent / "streamlit" / "main.py"

pytest.importorskip("streamlit.testing.v1", reason="streamlit is a UI-only dependency")


@pytest.fixture
def stub_boundaries(monkeypatch):
    """Every submission passes; no model is ever called."""

    def fake_run(code, tests, function_name):
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=len(tests),
            total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        )

    monkeypatch.setattr(code_session, "run_submission", fake_run)
    monkeypatch.setattr(
        code_session, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False)
    )


@pytest.fixture
def app(stub_boundaries):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(APP), default_timeout=180).run()


def answer_everything(app, limit: int = 30) -> int:
    """Answer questions until the session ends. Returns how many were answered."""
    answered = 0
    for _ in range(limit):
        if app.exception or any("Assessment complete" in t.value for t in app.title):
            break
        if app.radio:
            app.radio[0].set_value(app.radio[0].options[0])
        elif app.text_area:
            app.text_area[0].set_value("def f():\n    return 1\n")
        submit = [b for b in app.button if b.label.startswith("Submit")]
        if not submit:
            break
        submit[0].click().run()
        answered += 1
    return answered


class TestSetupScreen:
    def test_it_asks_for_competency_level_and_confidence(self, app):
        """The three things a candidate is asked before anything is administered."""
        assert app.multiselect, "no competency selector"
        assert app.slider, "no self-rating"
        assert app.radio, "no confidence control"
        assert any(b.label == "Begin assessment" for b in app.button)

    def test_it_defaults_to_competencies_carrying_both_modalities(self, app):
        """The mixed ones are what a tester most needs to exercise."""
        assert app.multiselect[0].value


class TestAssessmentScreen:
    def test_beginning_a_session_presents_a_question_and_the_panels(self, app):
        app.button[0].click().run()
        assert not app.exception
        # Queue, Mathematics, Trajectory, Engine log, Diagnostics
        assert len(app.tabs) == 5

    def test_a_whole_session_runs_to_completion_without_raising(self, app):
        app.button[0].click().run()
        answered = answer_everything(app)
        assert not app.exception, app.exception
        assert answered > 0
        assert any("Assessment complete" in t.value for t in app.title)

    def test_the_engine_import_is_not_shadowed_by_the_ui_module(self, app):
        """`streamlit/main.py`, not app.py: Streamlit puts the script's directory on
        sys.path, so an app.py there shadows the backend's `app` package and every engine
        import fails with "app is not a package"."""
        assert APP.name == "main.py"
        assert not (APP.parent / "app.py").exists()
        assert not app.exception


class TestArrowSerialisation:
    """Mixed-type columns fail Arrow and force a coerced render on every rerun."""

    def test_every_rendered_table_serialises_cleanly(self, app):
        import pyarrow as pa

        app.button[0].click().run()
        answer_everything(app, limit=6)
        assert not app.exception

        for element in app.dataframe:
            frame = element.value
            if frame is None or not hasattr(frame, "columns"):
                continue
            pa.Table.from_pandas(frame)  # raises ArrowInvalid on a mixed column
