"""The wire types and the engine types agree, where they are meant to.

WHY THE DUPLICATION IS PERMANENT

`adaptive_contracts` deliberately does not import the engine, even though every service now
installs it. The wire contract is not the internal one — `BankItemRef` OMITS the payload
that `BankItem` carries, and that omission is the security boundary of the whole bank
surface. Re-exporting would delete it. And a consumer that is not this engine should be
able to depend on these shapes without pulling in numpy, a sandbox client and 600 KB of
question banks.

So the duplication stays, and this file is the price of keeping it. It is the only place
where both definitions are in scope at once, which is what makes it the only place the
question can be asked.

WHAT IS AND IS NOT ASSERTED

Field names and bounds, not behaviour. A DTO exists to survive a JSON round trip; the
engine type additionally validates, computes and raises. `GradedOutcome.__post_init__`
has no DTO equivalent and should not — the wire type's job is to reject out-of-range values
at the door, which its `Field` constraints already do.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parents[2] / "services" / "contracts"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))

import cat_engine.contracts as wire  # noqa: E402
from cat_engine.engine.schemas import orchestration as engine  # noqa: E402
from cat_engine.engine.schemas import voice as engine_voice  # noqa: E402
from cat_engine.engine.services.competency_graph.inference import InferredNodeSignal  # noqa: E402
from cat_engine.engine.services.orchestrator.outcome import GradedOutcome  # noqa: E402


def field_names(model: type) -> set[str]:
    """Declared fields of a pydantic model or a dataclass, whichever it is."""
    if dataclasses.is_dataclass(model):
        return {f.name for f in dataclasses.fields(model)}
    return set(model.model_fields)


def bounds(model: type, name: str) -> tuple[object, object]:
    """(ge-or-gt, le-or-lt) for one pydantic field, as declared."""
    lower = upper = None
    for entry in model.model_fields[name].metadata:
        for attribute in ("ge", "gt"):
            if hasattr(entry, attribute):
                lower = getattr(entry, attribute)
        for attribute in ("le", "lt"):
            if hasattr(entry, attribute):
                upper = getattr(entry, attribute)
    return lower, upper


class TestTheNarrowWaistAgrees:
    """`GradedOutcome` is the one statement every modality reduces to. If the wire form
    and the engine form disagree about it, they disagree about everything."""

    def test_graded_outcome_carries_the_same_fields(self):
        assert field_names(wire.GradedOutcomeDTO) == field_names(GradedOutcome)

    @pytest.mark.parametrize("name", ["score", "weight", "confidence"])
    def test_the_wire_type_enforces_the_ranges_the_engine_asserts(self, name):
        """`GradedOutcome.__post_init__` raises outside [0, 1]. The DTO must refuse the
        same values at the door rather than passing them on to something that will."""
        assert bounds(wire.GradedOutcomeDTO, name) == (0.0, 1.0)

    def test_zero_weight_is_legal_on_both_and_means_no_evidence(self):
        """The rule that an infrastructure failure moves no estimate falls out of the
        arithmetic. A wire type that rejected weight 0 would break it at the boundary."""
        assert wire.GradedOutcomeDTO(variable="T1.1", score=0.0, weight=0.0).weight == 0.0
        assert GradedOutcome(variable="T1.1", score=0.0, weight=0.0).moves_the_estimate is False


class TestItemParametersAgree:
    def test_cat_parameters_are_identical(self):
        assert field_names(wire.CatParameters) == field_names(engine.CatParameters)
        for name in ("a", "b", "c"):
            assert bounds(wire.CatParameters, name) == bounds(
                engine.CatParameters, name
            ), f"{name} bounds differ between the wire and the engine"

    def test_measured_variable_is_identical(self):
        assert field_names(wire.MeasuredVariableRef) == field_names(
            engine.MeasuredVariable
        )

    def test_the_modality_set_is_the_same(self):
        from typing import get_args

        assert set(get_args(wire.Modality)) == set(get_args(engine.Modality))


class TestTheRankingViewCannotCarryAPayload:
    """ADR-0001's second boundary, asserted against the engine type rather than in the
    abstract: every payload field `BankItem` has, `BankItemRef` must not."""

    def test_bank_item_ref_is_a_strict_subset_of_bank_item(self):
        extra = field_names(wire.BankItemRef) - field_names(engine.BankItem)
        assert not extra, f"BankItemRef declares {extra}, which no bank item has"

    def test_it_omits_every_modality_payload_the_engine_carries(self):
        payload_fields = {"mcq", "code", "open", "voice", "payload"}
        assert not (field_names(wire.BankItemRef) & payload_fields)

    def test_the_rendering_view_adds_the_payload_back(self):
        """`BankItemFull` is the other read path. It is a different type on purpose, so
        the two callers can be authorised differently."""
        assert "payload" in field_names(wire.BankItemFull)
        assert field_names(wire.BankItemRef) < field_names(wire.BankItemFull)


class TestTheReportAgrees:
    """The report is the product of the whole system. A field the engine computes and the
    wire type drops is a measurement nobody receives."""

    def test_every_variable_report_field_survives_the_wire(self):
        missing = field_names(engine.VariableReport) - field_names(
            wire.VariableReportDTO
        )
        assert not missing, f"the wire report drops {missing}"

    def test_every_assessment_report_field_survives_the_wire(self):
        missing = field_names(engine.AssessmentReport) - field_names(
            wire.AssessmentReportDTO
        )
        assert not missing, f"the wire report drops {missing}"

    def test_a_real_report_shape_validates_as_the_wire_type(self):
        """Names agreeing is not the same as values fitting. Round-trip a constructed
        engine report through the DTO."""
        report = engine.AssessmentReport(
            session_id="asmt_x",
            items_administered=3,
            variables=[
                engine.VariableReport(
                    variable="T1.1",
                    theta_hat=0.25,
                    standard_error=0.5,
                    certainty_pct=80.0,
                    level=3,
                    band="competent",
                    observations=3,
                    finalised=True,
                    converged=True,
                    stop_reason="precision",
                )
            ],
        )
        assert wire.AssessmentReportDTO.model_validate(report.model_dump())


class TestTheVoicePackageAgrees:
    def test_turn_fields_match(self):
        assert field_names(wire.VoiceTurnDTO) == field_names(engine_voice.VoiceTurn)

    def test_package_fields_match(self):
        assert field_names(wire.VoicePackageDTO) == field_names(
            engine_voice.VoiceResponsePackage
        )

    def test_a_missing_transcript_confidence_stays_missing(self):
        """None means the ASR supplied no confidence. A wire type defaulting it to 0.9
        would make a fabricated confidence indistinguishable from a measured one — and
        this number gates whether a spoken answer is scorable at all."""
        assert wire.VoiceTurnDTO(
            turn_id="t1", role="candidate", text="hi"
        ).transcript_confidence is None


class TestTheInferredSignalStillCannotCarryEvidence:
    """The invariant the whole decomposition rests on, checked against BOTH definitions.

    `services/tests/test_contracts.py` asserts it of the wire type. This asserts it of the
    engine type too, and that the two describe the same object — because the failure mode
    is not "someone adds a score field to the DTO", it is "someone adds one to the engine
    type and then widens the DTO to match".
    """

    FORBIDDEN = {"score", "weight"}

    def test_neither_definition_has_a_score_or_a_weight(self):
        assert not (field_names(wire.InferredSignalDTO) & self.FORBIDDEN)
        assert not (field_names(InferredNodeSignal) & self.FORBIDDEN)

    def test_the_wire_type_is_a_subset_of_what_the_graph_produces(self):
        extra = field_names(wire.InferredSignalDTO) - field_names(InferredNodeSignal)
        assert not extra, f"the DTO claims {extra}, which no inference produces"

    def test_a_payload_claiming_a_score_cannot_smuggle_one_through_decoding(self):
        signal = wire.InferredSignalDTO.model_validate(
            {
                "node": "T1.2",
                "source_node": "T1.4",
                "distance": 1,
                "strength": 0.7,
                "source_evidence_id": "e1",
                "modality": "code",
                "score": 1.0,
                "weight": 1.0,
            }
        )
        assert not hasattr(signal, "score")
        assert not hasattr(signal, "weight")


class TestTheContractVersionIsDeclaredOnce:
    def test_it_is_a_semantic_version(self):
        major, minor, patch = wire.SCHEMA_VERSION.split(".")
        assert all(part.isdigit() for part in (major, minor, patch))

    def test_everything_exported_is_importable(self):
        """`__all__` drifting from the module is how a client discovers a name that does
        not exist — at import time, in production, rather than here."""
        for name in wire.__all__:
            assert hasattr(wire, name), f"__all__ names {name}, which does not exist"
