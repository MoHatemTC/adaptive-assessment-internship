"""Validating what the rubric grader sends back.

WHAT THIS IS FOR

The grader is a model. It is handed a rubric and a transcript and asked to return criterion
scores. Everything downstream — the projection onto competencies, the evidence strength, the
posterior update — treats that reply as data. This is the only thing standing between a
malformed or dishonest reply and a candidate's estimate.

TWO KINDS OF REJECTION, AND THE DIFFERENCE IS THE POINT

**Fatal** stops everything: the reply is about a different item, a different rubric, or a
different rubric version. There is nothing to salvage, because the scores describe a
question this candidate was not asked.

**Degraded** drops one criterion and keeps the rest. A model that hallucinated one criterion
id, or cited a quote that is not in the transcript, has still said something usable about the
others — and discarding a whole grading because one line was wrong would throw away real
evidence over a partial failure.

Every rejection appends a FLAG. A silent drop and a recorded drop look identical in the
score and completely different in an appeal.

WHAT A DEGRADED REPLY MUST NEVER DO

Contribute the criterion it failed on. That is what makes "keep the rest" safe: the
surviving evidence is exactly the evidence that passed every check.

Untested before this file, at 54% coverage.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.schemas.voice import VoiceResponsePackage
from cat_engine.engine.services.voice.validation import validate

ITEM = "open_q1"
ANSWER = (
    "I would profile the query first, add an index on the join column, and measure "
    "again before changing anything else rather than guessing at the cause."
)

RUBRIC = {
    "rubric_id": "rub_1",
    "version": "1.0",
    "criteria": [
        {"criterion_id": "accuracy", "competency_id": "C1", "maximum_score": 8},
        {"criterion_id": "depth", "competency_id": "C1", "maximum_score": 4},
    ],
}


def package() -> VoiceResponsePackage:
    return VoiceResponsePackage.from_text(ITEM, ANSWER)


def criterion(**over) -> dict:
    base = {
        "criterion_id": "accuracy",
        "competency_id": "C1",
        "raw_score": 6,
        "maximum_score": 8,
        "confidence": 0.9,
        "prompt_dependency": "independent",
        "quote": "add an index on the join column",
    }
    base.update(over)
    return base


def reply(*criteria, **over) -> dict:
    base = {
        "item_id": ITEM,
        "rubric_id": "rub_1",
        "rubric_version": "1.0",
        "criterion_evidence": list(criteria) or [criterion()],
    }
    base.update(over)
    return base


def run(r):
    return validate(r, ITEM, RUBRIC, package())


class TestAWellFormedReplyIsAccepted:
    def test_it_keeps_every_criterion(self):
        result = run(
            reply(criterion(), criterion(criterion_id="depth", maximum_score=4, raw_score=3))
        )
        assert len(result.criterion_evidence) == 2
        assert not result.flags

    def test_the_quote_tier_is_recorded(self):
        """Downstream weighting reads it: a fuzzy match is weaker evidence than an exact
        one, and that distinction has to survive validation."""
        result = run(reply())
        assert result.criterion_evidence[0].quote_tier in (
            "exact",
            "normalized",
            "subsequence",
            "fuzzy",
            "described",
        )


class TestFatalRejections:
    """The reply describes a question this candidate was not asked. Nothing is salvaged."""

    @pytest.mark.parametrize(
        "field,value,flag",
        [
            ("item_id", "some_other_item", "QUESTION_ID_MISMATCH"),
            ("rubric_id", "rub_999", "RUBRIC_ID_MISMATCH"),
            ("rubric_version", "2.0", "RUBRIC_VERSION_MISMATCH"),
        ],
    )
    def test_a_mismatched_identity_discards_everything(self, field, value, flag):
        result = run(reply(**{field: value}))
        assert flag in result.flags
        assert not result.criterion_evidence, "a mismatched reply contributed evidence"


class TestDegradedRejectionsDropOneAndKeepTheRest:
    """Each of these is a partial failure. The surviving criterion must still count."""

    def _one_bad_one_good(self, bad):
        good = criterion(criterion_id="depth", maximum_score=4, raw_score=3)
        return run(reply(bad, good))

    def test_an_unknown_criterion_id_is_dropped(self):
        result = self._one_bad_one_good(criterion(criterion_id="invented"))
        assert "UNKNOWN_CRITERION" in result.flags
        assert [e.criterion_id for e in result.criterion_evidence] == ["depth"]

    def test_a_competency_the_rubric_does_not_agree_with_is_dropped(self):
        """A model reassigning a criterion to another competency would move evidence onto
        an ability it was never about."""
        result = self._one_bad_one_good(criterion(competency_id="C9"))
        assert "CRITERION_COMPETENCY_MISMATCH" in result.flags
        assert [e.criterion_id for e in result.criterion_evidence] == ["depth"]

    def test_a_score_above_its_maximum_is_dropped(self):
        result = self._one_bad_one_good(criterion(raw_score=99))
        assert "SCORE_OUT_OF_RANGE" in result.flags
        assert [e.criterion_id for e in result.criterion_evidence] == ["depth"]

    def test_a_negative_score_is_dropped(self):
        result = self._one_bad_one_good(criterion(raw_score=-1))
        assert "SCORE_OUT_OF_RANGE" in result.flags

    def test_a_rescaled_maximum_is_dropped(self):
        """A model returning "7 out of 10" for a criterion worth 8 has scored a different
        rubric. Rescaling it here would invent a number nobody produced."""
        result = self._one_bad_one_good(criterion(maximum_score=10))
        assert "MAXIMUM_SCORE_MISMATCH" in result.flags

    def test_an_unparseable_score_is_dropped(self):
        result = self._one_bad_one_good(criterion(raw_score="quite good"))
        assert "UNPARSEABLE_SCORE" in result.flags

    def test_a_confidence_outside_zero_to_one_is_dropped(self):
        result = self._one_bad_one_good(criterion(confidence=1.4))
        assert "CONFIDENCE_OUT_OF_RANGE" in result.flags

    def test_a_duplicate_criterion_is_counted_once(self):
        """Two entries for one criterion would double its weight in the projection."""
        result = run(reply(criterion(), criterion()))
        assert "DUPLICATE_CRITERION" in result.flags
        assert len(result.criterion_evidence) == 1


class TestEvidenceMustComeFromTheCANDIDATE:
    def test_a_quote_that_is_not_in_the_transcript_is_dropped(self):
        """The failure the quote matcher exists for, seen from the layer that acts on it."""
        result = run(
            reply(
                criterion(quote="the candidate described monad transformers at length"),
                criterion(criterion_id="depth", maximum_score=4, raw_score=3),
            )
        )
        assert "QUOTE_NOT_IN_TURN" in result.flags
        assert [e.criterion_id for e in result.criterion_evidence] == ["depth"]

    def test_an_unknown_turn_id_falls_back_when_there_is_only_one_turn(self):
        """Deliberate, and worth knowing: with a single-turn package — every typed answer,
        and most spoken ones — a wrong `quote_turn_id` resolves to the only turn there is
        rather than being refused.

        That is safe because the quote itself is still matched against that turn's text, so
        a model cannot smuggle evidence in by naming a turn; the worst it can do is name the
        turn it was always going to be checked against. Refusing instead would throw away a
        correct grading over a field the model had no real choice about.
        """
        result = run(reply(criterion(quote_turn_id="t_nonexistent")))
        assert result.criterion_evidence, "the fallback stopped working"
        assert result.criterion_evidence[0].quote_turn_id == "t0"
        assert "UNKNOWN_TURN" not in result.flags

    def test_but_the_quote_is_still_checked_against_that_turn(self):
        """Which is what makes the fallback safe rather than a hole."""
        result = run(
            reply(
                criterion(quote_turn_id="t_nonexistent", quote="something never said"),
                criterion(criterion_id="depth", maximum_score=4, raw_score=3),
            )
        )
        assert "QUOTE_NOT_IN_TURN" in result.flags
        assert [e.criterion_id for e in result.criterion_evidence] == ["depth"]

    def test_an_unknown_prompt_dependency_degrades_rather_than_dropping(self):
        """The one case that keeps the criterion: the score is still usable, so it is kept
        at the conservative dependency rather than thrown away."""
        result = run(reply(criterion(prompt_dependency="invented_value")))
        assert "UNKNOWN_PROMPT_DEPENDENCY" in result.flags
        assert len(result.criterion_evidence) == 1
        assert result.criterion_evidence[0].prompt_dependency == "probe_supported"


class TestMalformedRepliesDoNotCrash:
    """Every one of these is something a model has actually returned somewhere."""

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"item_id": ITEM, "rubric_id": "rub_1", "rubric_version": "1.0"},
            reply(**{"criterion_evidence": []}),
            reply(**{"criterion_evidence": None}),
        ],
    )
    def test_an_empty_or_missing_evidence_list_returns_no_evidence(self, body):
        result = validate(body, ITEM, RUBRIC, package())
        assert result.criterion_evidence == []

    def test_the_alternate_key_name_is_accepted(self):
        """Models return `criteria` about as often as `criterion_evidence`. Accepting both
        costs one `or` and saves a whole grading."""
        body = {
            "item_id": ITEM,
            "rubric_id": "rub_1",
            "rubric_version": "1.0",
            "criteria": [criterion()],
        }
        assert len(validate(body, ITEM, RUBRIC, package()).criterion_evidence) == 1
