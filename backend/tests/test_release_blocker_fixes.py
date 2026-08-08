"""Regression coverage for blockers found by the C-shipped release audit."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app import main
from app.config.settings import settings


def test_session_seed_defaults_to_entropy_and_answer_seed_is_not_reused() -> None:
    assert main.CreateCatSessionRequest.model_fields["seed"].default is None
    assert main.CatAnswerRequest.model_fields["seed"].default is None


def test_http_session_owns_one_persistent_generator() -> None:
    session_id = "release-fix-rng-test"
    expected = np.random.default_rng(713)
    main._cat_rngs[session_id] = expected
    try:
        assert main._session_rng(session_id) is expected
        assert main._session_rng(session_id) is expected
    finally:
        main._cat_rngs.pop(session_id, None)


def test_reporting_calibration_is_enabled_by_default() -> None:
    assert settings.cat_interval_widening_enabled is True
    assert settings.interval_widening_for("C1") == 1.55
    assert settings.interval_widening_for("C3") == 1.50
    assert settings.interval_widening_for("C6") == 1.36


def test_band_decisions_are_not_certified_before_external_accuracy_passes() -> None:
    assert settings.cat_band_decisions_certified is False


def test_variable_report_defaults_to_provisional_decision_status() -> None:
    from app.schemas.orchestration import VariableReport

    report = VariableReport(
        variable="C1",
        theta_hat=0.0,
        standard_error=0.5,
        certainty_pct=80.0,
        level=3,
        band="Competent",
        observations=8,
        finalised=True,
        converged=True,
        stop_reason="band_probability",
    )

    assert report.decision_status == "provisional"


def test_live_helper_never_disables_tls_verification() -> None:
    source = (Path(__file__).resolve().parents[2] / "streamlit" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "verify=False" not in source
