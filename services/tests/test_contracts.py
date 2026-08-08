"""The wire types. These are the reviewable artefact, so they are tested hardest.

`adaptive_contracts` is the only code here that every service depends on and the only code
whose meaning survives the migration. A bug in a 501 stub costs nothing; a bug in an
envelope is a bug in every service that will ever speak it.

The most important test in this file is `TestInferredSignalCannotCarryEvidence`. The whole
decomposition rests on one rule — only a directly observed response may move a posterior —
and the contract is what enforces it. If `InferredSignalDTO` ever grows a `score`, a graph
service can hand the orchestrator something it will mistake for evidence, and the failure
lands on the standard error, which is what the assessment stops on.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from adaptive_contracts import SCHEMA_VERSION
from adaptive_contracts.envelopes import (
    BankItemRef,
    CatParameters,
    GradedOutcomeDTO,
    GradedResponseDTO,
    InferredSignalDTO,
    MeasuredVariableRef,
)


def _cat(**overrides) -> dict:
    return {"a": 1.2, "b": 0.0, "c": 0.2, **overrides}


class TestSchemaVersion:
    def test_it_is_semver(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", SCHEMA_VERSION), SCHEMA_VERSION

    def test_it_is_exported_from_the_package_root(self):
        """Services import it from the root; moving it would break every /health."""
        import adaptive_contracts

        assert adaptive_contracts.SCHEMA_VERSION == SCHEMA_VERSION


class TestCatParameters:
    """Item parameters on theta. A service returning items without these is not a bank."""

    def test_a_valid_item_round_trips(self):
        assert CatParameters(**_cat()).model_dump() == {"a": 1.2, "b": 0.0, "c": 0.2}

    @pytest.mark.parametrize("a", [0.0, -0.5, 3.01])
    def test_discrimination_outside_zero_to_three_is_refused(self, a):
        with pytest.raises(ValidationError):
            CatParameters(**_cat(a=a))

    @pytest.mark.parametrize("b", [-4.001, 4.001])
    def test_difficulty_outside_the_ability_scale_is_refused(self, b):
        with pytest.raises(ValidationError):
            CatParameters(**_cat(b=b))

    def test_a_guessing_floor_of_one_is_refused(self):
        """c = 1 means the item is answered correctly by everyone; it carries no
        information and would make Fisher information undefined."""
        with pytest.raises(ValidationError):
            CatParameters(**_cat(c=1.0))

    def test_the_scale_boundaries_themselves_are_accepted(self):
        """Exclusive vs inclusive bounds, pinned: a is (0, 3], b is [-4, 4], c is [0, 1)."""
        assert CatParameters(a=3.0, b=-4.0, c=0.0).a == 3.0
        assert CatParameters(a=0.01, b=4.0, c=0.999).b == 4.0

    def test_guessing_defaults_to_zero_so_an_open_item_needs_no_c(self):
        assert CatParameters(a=1.0, b=0.0).c == 0.0


class TestBankItemRef:
    """What SELECTION needs. Never the payload — that split is an authorisation boundary."""

    def test_it_carries_no_payload_field(self):
        """The orchestrator ranks items; it must not be able to read a question.

        If a stem, options or answer ever appear here, one caller's authorisation silently
        becomes both callers' authorisation.
        """
        forbidden = {"stem", "options", "answer", "answer_index", "payload", "prompt", "body"}
        assert forbidden.isdisjoint(BankItemRef.model_fields)

    def test_an_item_measuring_nothing_is_refused(self):
        with pytest.raises(ValidationError):
            BankItemRef(item_id="i1", modality="mcq", measures=[], cat=CatParameters(**_cat()))

    def test_an_unknown_modality_is_refused(self):
        with pytest.raises(ValidationError):
            BankItemRef(
                item_id="i1", modality="telepathy",
                measures=[MeasuredVariableRef(variable="C1.1")], cat=CatParameters(**_cat()),
            )

    def test_optional_estimated_time_must_be_positive_when_given(self):
        with pytest.raises(ValidationError):
            BankItemRef(
                item_id="i1", modality="code",
                measures=[MeasuredVariableRef(variable="C1.1")],
                cat=CatParameters(**_cat()), estimated_time_seconds=0.0,
            )


class TestGradedOutcome:
    """The narrow waist: one graded statement about one variable, from any modality."""

    def test_weight_zero_is_legal_and_means_no_evidence(self):
        """A submission that failed to compile scores 0 but demonstrates very little.

        Weight is separate from score precisely so that case can be expressed. Refusing
        weight 0 would force the caller to either drop the response or punish the
        competency at full strength for a typo.
        """
        outcome = GradedOutcomeDTO(variable="C1.1", score=0.0, weight=0.0)
        assert outcome.weight == 0.0

    @pytest.mark.parametrize("field", ["score", "weight", "confidence"])
    @pytest.mark.parametrize("value", [-0.01, 1.01])
    def test_the_unit_interval_is_enforced(self, field, value):
        with pytest.raises(ValidationError):
            GradedOutcomeDTO(**{"variable": "C1.1", "score": 0.5, field: value})

    def test_modality_may_be_empty_for_an_outcome_with_no_single_source(self):
        assert GradedOutcomeDTO(variable="C1.1", score=0.5, modality="").modality == ""

    def test_a_response_defaults_to_no_outcomes_rather_than_failing(self):
        """An ungradable response is a real event, not a malformed message."""
        response = GradedResponseDTO(item_id="i1", modality="voice")
        assert response.outcomes == [] and response.flags == []


class TestInferredSignalCannotCarryEvidence:
    """THE structural invariant. Everything else in the decomposition depends on it.

    Propagated evidence is a deduction FROM a response that is already in the likelihood.
    Multiplying it in again counts one answer twice; the damage lands on the standard
    error, which is what the assessment stops on.

    The rule is enforced by the type having no such fields, not by a reviewer noticing —
    which is what makes it safe to run the graph as a separate service with its own store.
    The worst a compromised or buggy graph service can do is change which question is
    asked next.
    """

    @pytest.mark.parametrize("field", ["score", "weight"])
    def test_the_field_does_not_exist(self, field):
        assert field not in InferredSignalDTO.model_fields

    @pytest.mark.parametrize("field", ["score", "weight"])
    def test_a_payload_claiming_one_cannot_smuggle_it_through(self, field):
        """A hostile or buggy sender may put `score` on the wire. It must not survive
        decoding — if it did, the orchestrator could read it off the object."""
        signal = InferredSignalDTO.model_validate({
            "node": "C6.4", "source_node": "C6.5", "distance": 1, "strength": 0.4,
            "source_evidence_id": "ev-1", "modality": "code", field: 0.99,
        })
        assert not hasattr(signal, field)
        assert field not in signal.model_dump()

    def test_it_is_not_assignable_to_a_graded_outcome(self):
        """The two types must not be structurally interchangeable.

        `GradedOutcomeDTO` requires `variable` and `score`; a signal supplies neither, so
        handing one where the other is expected fails at the boundary rather than
        producing a plausible zero.
        """
        signal = InferredSignalDTO(
            node="C6.4", source_node="C6.5", distance=1, strength=0.4,
            source_evidence_id="ev-1", modality="code",
        )
        with pytest.raises(ValidationError):
            GradedOutcomeDTO.model_validate(signal.model_dump())

    def test_it_records_where_the_deduction_came_from(self):
        """Provenance is what makes an inferred claim contestable by the candidate.

        Without source_node, distance and source_evidence_id, a report can say a
        competency was inferred but not from which answer — and an appeal is impossible.
        """
        required = {"node", "source_node", "distance", "strength", "source_evidence_id"}
        assert required.issubset(InferredSignalDTO.model_fields)
