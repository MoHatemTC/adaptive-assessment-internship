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


def begin(app):
    """Start a session. By label, because setup is no longer the only screen with buttons."""
    next(b for b in app.button if b.label == "Begin assessment").click().run()
    return app


def answer_everything(app, limit: int = 30) -> int:
    """Answer questions until the session ends. Returns how many were answered.

    Widgets are found by LABEL, not by index. AppTest keeps elements from earlier screens
    in its tree, so `app.radio[0]` is whichever radio was rendered first anywhere in the
    session — once setup grew a track selector and per-competency confidence radios, that
    stopped being the answer control and the helper started driving the setup screen.
    """
    answered = 0
    for _ in range(limit):
        if app.exception or any("Assessment complete" in t.value for t in app.title):
            break
        answer = [r for r in app.radio if r.label == "Your answer"]
        solution = [t for t in app.text_area if t.label == "Your solution"]
        if answer:
            answer[0].set_value(answer[0].options[0])
        elif solution:
            solution[0].set_value("def f():\n    return 1\n")
        submit = [b for b in app.button if b.label.startswith("Submit")]
        if not submit:
            break
        submit[0].click().run()
        answered += 1
    return answered


class TestSetupScreen:
    def test_it_asks_for_track_competency_level_and_confidence(self, app):
        """Everything a candidate is asked before anything is administered."""
        assert app.radio, "no track selector"
        assert app.multiselect, "no sub-competency selector"
        assert app.slider, "no self-rating"
        assert any(b.label == "Begin assessment" for b in app.button)

    def test_the_track_choice_offers_all_five(self, app):
        """T1..T5. A candidate picks a track first; everything else is scoped to it."""
        options = app.radio[0].options
        assert len(options) == 5
        assert all(any(code in option for option in options) for code in
                   ("T1", "T2", "T3", "T4", "T5"))

    def test_it_defaults_to_competencies_carrying_both_modalities(self, app):
        """The mixed ones are what a tester most needs to exercise."""
        assert app.multiselect[0].value

    def test_choosing_a_track_scopes_the_sub_competencies_to_it(self, app):
        """The point of the track choice. Picking MLOps must not offer only T1.*"""
        target = next(o for o in app.radio[0].options if o.startswith("T5"))
        app.radio[0].set_value(target).run()
        assert not app.exception
        offered = app.multiselect[0].options
        assert any(v.startswith("T5.") for v in offered)
        assert app.multiselect[0].value, "nothing selected by default for this track"
        assert all(v.startswith("T5.") for v in app.multiselect[0].value)

    def test_engine_controls_are_not_mixed_into_the_candidate_flow(self, app):
        """The picking-agent switch decides how a candidate's own questions are chosen.

        It belongs to a tester, so it sits behind a labelled expander rather than beside
        the self-rating, where it reads as something the examinee is being asked.
        """
        labels = [e.label for e in app.expander]
        assert any("Tester options" in label for label in labels)


class TestAssessmentScreen:
    def test_beginning_a_session_presents_a_question_and_the_panels(self, app):
        begin(app)
        assert not app.exception
        # Queue, Mathematics, Trajectory, Engine log, Diagnostics
        assert len(app.tabs) == 5

    def test_a_whole_session_runs_to_completion_without_raising(self, app):
        begin(app)
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


class TestTrialRuns:
    """A candidate may run the example cases before committing to a submission."""

    @staticmethod
    def _reach_a_code_question(app, limit: int = 12):
        for _ in range(limit):
            if [t for t in app.text_area if t.label == "Your solution"]:
                return True
            answer = [r for r in app.radio if r.label == "Your answer"]
            if answer:
                answer[0].set_value(answer[0].options[0])
            submit = [b for b in app.button if b.label.startswith("Submit")]
            if not submit:
                return False
            submit[0].click().run()
        return False

    def test_a_code_question_offers_a_run_that_is_not_a_submission(self, app):
        """Two distinct buttons. A candidate must never be unsure which one they pressed —
        one changes their measured ability and the other cannot."""
        begin(app)
        if not self._reach_a_code_question(app):
            pytest.skip("no code question was administered in this session")

        labels = [b.label for b in app.button]
        assert any(label.startswith("Run example cases") for label in labels)
        assert any(label.startswith("Submit") for label in labels)

    def test_running_the_examples_records_no_answer(self, app):
        """The property the whole feature depends on: a trial moves no estimate."""
        begin(app)
        if not self._reach_a_code_question(app):
            pytest.skip("no code question was administered in this session")

        before = app.session_state["run"].items_administered
        run = next(b for b in app.button if b.label.startswith("Run example cases"))
        run.click().run()

        assert not app.exception, app.exception
        assert app.session_state["run"].items_administered == before


class TestArrowSerialisation:
    """Mixed-type columns fail Arrow and force a coerced render on every rerun."""

    def test_every_rendered_table_serialises_cleanly(self, app):
        import pyarrow as pa

        begin(app)
        answer_everything(app, limit=6)
        assert not app.exception

        for element in app.dataframe:
            frame = element.value
            if frame is None or not hasattr(frame, "columns"):
                continue
            pa.Table.from_pandas(frame)  # raises ArrowInvalid on a mixed column


class TestDeploymentRequirements:
    """Streamlit Cloud installs ONE requirements file: the one beside the entry point.

    It never reads backend/requirements.txt, so the UI's file has to carry the engine's
    dependencies too. A version listing only streamlit and pandas deployed an app that
    died on `No module named 'pydantic'` before rendering anything — these tests exist so
    that fails here instead of in production.
    """

    @staticmethod
    def _constraints(path: Path) -> dict[str, str]:
        constraints: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            for operator in (">=", "==", "~=", ">", "<"):
                if operator in line:
                    name, _, spec = line.partition(operator)
                    constraints[name.strip().lower()] = operator + spec.strip()
                    break
            else:
                constraints[line.strip().lower()] = ""
        return constraints

    def test_the_ui_requirements_carry_every_runtime_engine_dependency(self):
        root = APP.parent.parent
        backend = self._constraints(root / "backend" / "requirements.txt")
        ui = self._constraints(root / "streamlit" / "requirements.txt")

        # Test-only packages are not needed to serve the app.
        runtime = {k: v for k, v in backend.items() if not k.startswith("pytest")}
        missing = sorted(set(runtime) - set(ui))
        assert not missing, (
            f"streamlit/requirements.txt is missing {missing} — Streamlit Cloud installs "
            "only that file, so the deployed app would fail on import"
        )

    def test_shared_constraints_agree_between_the_two_files(self):
        """Otherwise the deployed app runs against different versions than the tests did."""
        root = APP.parent.parent
        backend = self._constraints(root / "backend" / "requirements.txt")
        ui = self._constraints(root / "streamlit" / "requirements.txt")

        disagreements = {
            name: (backend[name], ui[name])
            for name in set(backend) & set(ui)
            if backend[name] != ui[name]
        }
        assert not disagreements, f"constraints differ: {disagreements}"
