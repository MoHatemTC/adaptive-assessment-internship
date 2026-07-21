"""The shared contract, and the differences it must not hide."""

from __future__ import annotations

import pytest

from app.services.adaptive import AdaptiveSession, JsonItemRepository
from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
from app.services.orchestrator import AssessmentEngine, EngineRegistry, Modality


@pytest.fixture
def registry() -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(Modality.MCQ, AdaptiveSession(JsonItemRepository()))
    registry.register(Modality.CODE, CodeAdaptiveSession(JsonQuestionRepository()))
    return registry


def test_both_engines_satisfy_the_shared_contract(registry):
    for modality in registry.available():
        assert isinstance(registry.get(modality), AssessmentEngine)


def test_both_modalities_are_registered(registry):
    assert registry.available() == [Modality.CODE, Modality.MCQ]
    assert Modality.MCQ in registry


def test_an_unregistered_modality_fails_loudly(registry):
    empty = EngineRegistry()
    with pytest.raises(KeyError, match="no engine registered"):
        empty.get(Modality.MCQ)


def test_registering_twice_is_refused(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Modality.MCQ, object())


def test_the_two_engines_measure_on_different_scales(registry):
    """The reason a combined report cannot simply average them.

    MCQ estimates theta on [-4, 4] and stops at SE 0.65; code estimates mastery on [0, 1]
    and stops at 0.15. Neither target is meaningful on the other scale, so an orchestrator
    that treats the two estimates as one number will report confident nonsense.
    """
    from app.config.settings import settings

    assert settings.cat_se_target > settings.code_se_target
    report = registry.get(Modality.CODE).summarise(
        registry.get(Modality.CODE).begin("T1.1", self_rating=3), _no_stop()
    )
    assert 0.0 <= report.mastery <= 1.0


def test_an_unmeasured_competency_holds_no_state(registry):
    """No evidence, no entry. A prior is not a measurement, and an orchestrator must not
    be able to read one as though it were."""
    state = registry.get(Modality.CODE).begin("T1.1")
    assert state.competencies == {}


def _no_stop():
    from app.schemas.code_adaptive import StopDecision

    return StopDecision(should_stop=False)
