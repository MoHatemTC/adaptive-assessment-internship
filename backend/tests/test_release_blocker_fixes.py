"""Regression coverage for blockers found by the C-shipped release audit.

WHAT MOVED, AND WHERE

Three of these used to assert against `backend/app/main.py`, which no longer exists — the
CAT API is `services/assessment-orchestrator`. The properties did not move with the file:
they are asserted in `services/tests/test_assessment_orchestrator_service.py`, under
`TestPropertiesCarriedOverFromTheReleaseAudit`, against the routes that now serve them.

What is left here is what is genuinely about the ENGINE, plus one that is about the whole
repository and was previously scoped to a single file.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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


def test_nothing_in_the_repository_disables_tls_verification() -> None:
    """Widened from one file to all of them.

    The original blocker was `verify=False` in the Streamlit helper's HTTP calls. That file
    is gone, and scoping the assertion to it would have retired a real finding by deleting
    the thing it happened to be found in. The rule was never about that file: a client that
    skips certificate verification talks to whatever answers, and every service in this
    system now makes outbound calls.
    """
    offenders: list[str] = []
    for source in REPOSITORY_ROOT.rglob("*.py"):
        parts = set(source.parts)
        if parts & {".venv", "__pycache__", "node_modules", "build", "dist"}:
            continue
        if source == Path(__file__):
            continue
        text = source.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.split("#")[0]
            if "verify=False" in stripped or "verify = False" in stripped:
                offenders.append(f"{source.relative_to(REPOSITORY_ROOT)}:{number}")
    assert not offenders, f"TLS verification is disabled at {offenders}"
