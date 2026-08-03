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

from app.config.settings import settings
from app.config.voice_settings import voice_settings
from app.services.code_adaptive import session as code_session
from app.services.voice import evaluator as voice_evaluator


async def _stub_open_evaluation(item, package, *, use_llm: bool = True):
    """Grade an open answer with the deterministic heuristic, never the model."""
    from app.schemas.voice import GradedVoiceResponse

    rubric = voice_evaluator._rubric_from_payload(item)
    return GradedVoiceResponse(
        package=package,
        evaluation=voice_evaluator._heuristic_from_rubric(
            item.item_id, rubric, package, flags=["STUBBED"]
        ),
        rubric=rubric,
    )
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
def app(stub_boundaries, monkeypatch):
    """The tester on the DA bank.

    Pinned, and not because the UI cannot serve a multi-competency bank — it can; see
    `TestMultiCompetencyBank`. AppTest keeps widget nodes from screens that stopped
    rendering, and a setup screen with one slider per chosen competency leaves more than
    one such node behind, at which point the harness raises while re-submitting widget
    state. Pinning keeps these tests about the FLOW, which is what they were written for.
    """
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(settings, "active_bank", "DA")
    # The DA bank contains open items. Live voice needs a gateway and a microphone, so the
    # typed fallback — the same grading path, a different way of collecting the words — is
    # what makes an offline end-to-end run possible at all.
    monkeypatch.setattr(settings, "litellm_api_key", "")
    monkeypatch.setattr(voice_settings, "allow_text_fallback", True)
    monkeypatch.setattr(
        voice_evaluator, "evaluate", _stub_open_evaluation, raising=True
    )
    return AppTest.from_file(str(APP), default_timeout=180).run()


def _pin_stale_widgets(app) -> None:
    """Keep the setup screen's widget state readable after setup stops rendering.

    AppTest holds a node for every widget it has ever seen and re-submits their state on
    each `run()`. Streamlit, meanwhile, garbage-collects state belonging to widgets that
    stopped rendering — so once setup is replaced the harness walks nodes whose keys are
    gone and raises. That is a limitation of the harness, not of the app: a browser never
    re-reads a widget that is not on screen.

    The pinned value is arbitrary. Nothing reads it: everything the setup screen collected
    was copied into plain session keys at Begin, which is the whole reason the widget keys
    are free to disappear.
    """
    for widget in [*app.multiselect, *app.selectbox, *app.radio, *app.slider]:
        key = getattr(widget, "key", None)
        if not key:
            continue
        try:
            widget.value
            continue
        except (KeyError, ValueError):
            pass
        options = list(getattr(widget, "options", ()) or ())
        if widget in list(app.multiselect):
            app.session_state[key] = options[:1]
        elif options:
            app.session_state[key] = options[0]
        else:
            app.session_state[key] = getattr(widget, "min", 1)


def begin(app):
    """Start a session. By label, because setup is no longer the only screen with buttons."""
    next(b for b in app.button if b.label == "Begin assessment").click().run()
    # Setup will never render again; pin its widget state before the next run walks it.
    _pin_stale_widgets(app)
    return app


def answer_everything(app, limit: int = 30) -> int:
    """Answer questions until the session ends. Returns how many were answered.

    Widgets are found by LABEL, not by index. AppTest keeps elements from earlier screens
    in its tree, so `app.radio[0]` is whichever radio was rendered first anywhere in the
    session — once setup grew a track selector and per-competency confidence radios, that
    stopped being the answer control and the helper started driving the setup screen.
    """
    _pin_stale_widgets(app)
    answered = 0
    for _ in range(limit):
        if app.exception or any("Assessment complete" in t.value for t in app.title):
            break
        answer = [r for r in app.radio if r.label == "Your answer"]
        solution = [t for t in app.text_area if t.label == "Your solution"]
        written = [t for t in app.text_area if t.label == "Written answer (fallback)"]
        if answer:
            answer[0].set_value(answer[0].options[0])
        elif solution:
            solution[0].set_value("def f():\n    return 1\n")
        elif written:
            written[0].set_value(
                "I would inspect the schema and the null counts first, confirm the "
                "dtypes match what the join expects, and check the index for "
                "duplicates before trusting any aggregate computed from it."
            )
        submit = [b for b in app.button if b.label.startswith("Submit")]
        if not submit:
            break
        submit[0].click().run()
        answered += 1
    return answered


class TestSetupScreen:
    def test_it_asks_for_main_competencies_level_and_confidence(self, app):
        """Everything a candidate is asked before anything is administered."""
        assert app.multiselect, "no main-competency selector"
        assert app.slider, "no self-rating"
        assert any(b.label == "Begin assessment" for b in app.button)

    def test_the_competency_choice_offers_every_main_the_bank_can_assess(self, app):
        """Whatever the bank measures is offered — no hardcoded competency list.

        This used to assert ten C-codes, from a bank that no longer ships. A selector that
        silently omits a main a candidate could have been assessed on is the bug worth
        catching, and it is bank-independent.
        """
        from app.services.orchestrator import registry

        options = "".join(app.multiselect[0].options)
        expected = registry.get_bank("DA").variables()
        assert expected
        assert all(code in options for code in expected)

    def test_it_defaults_to_at_least_one_main_competency(self, app):
        assert app.multiselect[0].value

    def test_engine_controls_are_not_mixed_into_the_candidate_flow(self, app):
        """The picking-agent switch belongs to a tester, not the candidate flow."""
        labels = [e.label for e in app.expander]
        assert any("Tester options" in label for label in labels)


class TestAssessmentScreen:
    def test_beginning_a_session_presents_a_question_and_the_panels(self, app):
        begin(app)
        assert not app.exception
        labels = [tab.label for tab in app.tabs]
        assert labels == [
            "Queue",
            "Mathematics",
            "DAG",
            "Trajectory",
            "Engine log",
            "Tracebook",
            "Diagnostics",
        ]

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


class TestMultiCompetencyBank:
    """The default bank has three mains. Setup must offer all three and start cleanly.

    Deliberately stops after Begin: driving a long session through AppTest trips its own
    stale-widget handling once setup renders more than one per-competency slider, which
    says nothing about the UI (see the `app` fixture).
    """

    def test_setup_offers_every_main_and_begins_without_raising(self, stub_boundaries):
        from streamlit.testing.v1 import AppTest

        from app.services.orchestrator import registry

        app = AppTest.from_file(str(APP), default_timeout=180).run()
        assert not app.exception

        options = "".join(app.multiselect[0].options)
        for code in registry.get_bank("AIE").variables():
            assert code in options, f"{code} missing from the competency selector"

        next(b for b in app.button if b.label == "Begin assessment").click().run()
        assert not app.exception
        # The session screen, with a question presented against a three-main bank.
        assert [tab.label for tab in app.tabs][:3] == ["Queue", "Mathematics", "DAG"]
