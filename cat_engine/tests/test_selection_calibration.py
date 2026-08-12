"""The two constants that decide the modality mix, and where they come from.

Selection ranks on information per minute weighted by expected evidence, so these two
numbers decide which modality wins a ranking — and therefore how long a session runs and
what it is made of. They shipped as opinions: `mcq: 75s` had no measurement behind it
anywhere, and the expected weights were read off the graders' discount rungs.

They are now measured where a session corpus allows and defaulted where it does not, with
the precedence explicit and the choice visible rather than inferred.
"""

from __future__ import annotations

import json

import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.orchestrator import selection_calibration as calibration


@pytest.fixture(autouse=True)
def clean_cache():
    calibration.reset_cache()
    yield
    calibration.reset_cache()


def _write(tmp_path, **payload):
    path = tmp_path / "selection_calibration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    calibration.load.cache_clear()
    return path


class TestPrecedence:
    def test_it_falls_back_to_the_documented_defaults(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", tmp_path / "absent.json")
        calibration.load.cache_clear()

        assert calibration.seconds_for("mcq") == 75.0
        assert calibration.expected_weight_for("code") == 0.85
        assert calibration.provenance()["code"] == "seconds=default, weight=default"

    def test_a_measured_value_replaces_the_default(self, monkeypatch, tmp_path) -> None:
        path = _write(
            tmp_path,
            observations={"mcq": 200, "code": 200},
            seconds_by_modality={"mcq": 61.4, "code": 402.0},
            expected_weight_by_modality={"code": 0.78},
        )
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", path)
        calibration.load.cache_clear()

        assert calibration.seconds_for("mcq") == 61.4
        assert calibration.expected_weight_for("code") == 0.78
        assert "measured (n=200)" in calibration.provenance()["code"]

    def test_an_operator_override_beats_a_measurement(self, monkeypatch, tmp_path) -> None:
        """Pinning a value is a deliberate act and must not be silently overruled."""
        path = _write(
            tmp_path,
            observations={"mcq": 200},
            seconds_by_modality={"mcq": 61.4},
        )
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", path)
        monkeypatch.setattr(
            settings, "orchestrator_item_seconds_by_modality", '{"mcq": 90}'
        )
        calibration.load.cache_clear()

        assert calibration.seconds_for("mcq") == 90.0
        assert calibration.provenance()["mcq"].startswith("seconds=override")

    def test_too_few_observations_leaves_the_default_standing(
        self, monkeypatch, tmp_path
    ) -> None:
        """A mean over five responses is noise wearing a decimal point."""
        path = _write(
            tmp_path,
            observations={"code": calibration.MINIMUM_OBSERVATIONS - 1},
            seconds_by_modality={"code": 12.0},
            expected_weight_by_modality={"code": 0.1},
        )
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", path)
        calibration.load.cache_clear()

        assert calibration.seconds_for("code") == 480.0
        assert calibration.expected_weight_for("code") == 0.85

    def test_a_corrupt_calibration_file_does_not_break_selection(
        self, monkeypatch, tmp_path
    ) -> None:
        path = tmp_path / "selection_calibration.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", path)
        calibration.load.cache_clear()

        assert calibration.seconds_for("mcq") == 75.0

    def test_multiple_choice_weight_is_exact_and_never_measured(
        self, monkeypatch, tmp_path
    ) -> None:
        """The grader sets it from the item's loading at confidence 1.0.

        There is no degradation path, so a mean over responses would only reproduce the
        loading distribution of whichever items happened to be administered.
        """
        path = _write(
            tmp_path,
            observations={"mcq": 500},
            expected_weight_by_modality={"mcq": 0.42},
        )
        monkeypatch.setattr(calibration, "CALIBRATION_PATH", path)
        calibration.load.cache_clear()

        assert calibration.expected_weight_for("mcq") == 1.0
        assert calibration.provenance()["mcq"].endswith("weight=exact")


class TestBankAuthoredTime:
    def test_an_authored_time_wins_over_the_modality_figure(self, aie_bank) -> None:
        """A bank that states an item's duration knows more than a per-modality mean."""
        item = next(i for i in aie_bank.all_items() if i.modality == "code")
        assert item.authored_seconds == item.estimated_time_seconds
        assert item.expected_seconds == item.authored_seconds

    def test_an_item_that_states_nothing_defers_rather_than_guessing(
        self, aie_bank
    ) -> None:
        """`authored_seconds` is None so a measured figure is not shadowed by a default."""
        item = next(i for i in aie_bank.all_items() if i.modality == "mcq")
        assert item.authored_seconds is None
        assert item.expected_seconds > 0


class TestRecording:
    @pytest.mark.asyncio
    async def test_a_session_records_what_each_item_actually_cost_and_delivered(
        self, stub_boundaries
    ) -> None:
        """Without this the constants can never stop being assertions."""
        import numpy as np

        from cat_engine.engine.services.code_adaptive.bank import JsonQuestionRepository
        from cat_engine.engine.services.code_adaptive.session import CodeAdaptiveSession
        from cat_engine.engine.services.orchestrator.grader import GraderAgent
        from cat_engine.tests.conftest import orchestrator_for
        from cat_engine.tests.test_orchestration_flow import answer_for

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        rng = np.random.default_rng(2)
        for _ in range(3):
            state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
            state = orchestrator.ensure_presenting(state)
            item, _ = orchestrator.next_item(state)
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        assert state.item_seconds, "no per-item timing recorded"
        assert state.realized_weight_by_item, "no realised evidence weight recorded"
        assert set(state.realized_weight_by_item) <= set(state.served_item_ids)
        assert all(0.0 <= w <= 1.0 for w in state.realized_weight_by_item.values())
