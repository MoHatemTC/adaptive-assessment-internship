"""The evidence-quote matcher: four tiers, two hard gates, one escape hatch.

WHY THIS MATTERS MORE THAN ITS SIZE SUGGESTS

A rubric grader is a model, and a model asked to justify a criterion score will produce a
quote. This decides whether that quote is really in the transcript. A matcher that is too
strict throws away correct gradings because the candidate said "doesn't" and the model wrote
"does not"; one that is too loose lets a model invent evidence and have it counted.

The four tiers are the ladder between those failures, and each rung is a deliberate
loosening: exact, then normalised, then in-order tokens with gaps, then fuzzy above a length
and ratio floor. The two gates — minimum length, and a longer minimum before fuzzy is
allowed at all — are what stop the bottom rung matching anything.

`None` is the escape hatch, and it means something different from an empty string: the model
is DESCRIBING rather than quoting, which is legitimate, whereas an empty quote is a model
that produced the field and had nothing to put in it.

WHAT WAS UNTESTED

All of it. 24% covered before this file, in a module that gates whether a model's claim about
a candidate counts as evidence.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.services.voice.quotes import _normalize, match_quote

TURN = (
    "Um, so I would profile the query first, uh, then add an index on the join column, "
    "and measure again before changing anything else."
)


class TestTheEscapeHatchAndTheEmptyCase:
    def test_none_means_described_rather_than_quoted(self):
        """A grader may justify without quoting. That is not a failed match."""
        tier, ratio = match_quote(None, TURN)
        assert tier == "described"
        assert ratio == 1.0

    @pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
    def test_an_empty_quote_is_rejected_not_described(self, empty):
        """Different from None on purpose: the model produced the field and put nothing in
        it, which is a malformed reply rather than a deliberate choice."""
        assert match_quote(empty, TURN) == (None, 0.0)


class TestGateOneMinimumLength:
    def test_a_quote_below_the_floor_is_rejected_however_well_it_matches(self):
        """The floor exists because a two-character quote matches almost any transcript.
        Note it is rejected even though it appears VERBATIM."""
        short = "the"
        assert len(short) < voice_settings.quote_min_chars
        assert short in TURN
        assert match_quote(short, TURN) == (None, 0.0)

    def test_the_floor_is_measured_after_normalisation(self):
        """Punctuation and disfluency do not count toward length. A quote that is only
        punctuation is nothing, however long it looks."""
        assert _normalize("... !!! ???") == ""
        assert match_quote("... !!! ???", TURN) == (None, 0.0)


class TestTheFourTiers:
    def test_exact_is_a_verbatim_substring(self):
        tier, ratio = match_quote("add an index on the join column", TURN)
        assert tier == "exact"
        assert ratio == 1.0

    def test_normalised_survives_case_punctuation_and_disfluency(self):
        """The tier that stops a correct grading being thrown away over a comma. `um` and
        `uh` are transcription artefacts, not something the candidate chose to say."""
        tier, ratio = match_quote("Profile the query first, THEN add an index!", TURN)
        assert tier == "normalized"
        assert ratio == 1.0

    def test_subsequence_allows_gaps_between_content_words(self):
        """A model paraphrasing by dropping filler still points at real evidence."""
        tier, ratio = match_quote("profile the query add an index", TURN)
        assert tier == "subsequence"
        assert ratio == 1.0

    def test_a_subsequence_with_too_large_a_gap_is_not_one(self):
        """`max_gap=3`. Without a bound, any two words in a long transcript are a
        subsequence, and the tier would accept everything."""
        tier, _ = match_quote("profile anything", TURN)
        assert tier != "subsequence"

    def test_word_order_is_load_bearing(self):
        """Reversed content words are not evidence for the same claim."""
        tier, _ = match_quote("index an add first query the profile", TURN)
        assert tier in (None, "fuzzy")


class TestGateTwoFuzzyIsTheLastResort:
    def test_fuzzy_catches_a_quote_the_transcriber_spelled_differently(self):
        """Four typos across the turn — the case fuzzy exists for."""
        typoed = (
            "i would proflie the query first then add an index on the join column "
            "and measure agian"
        )
        assert len(_normalize(typoed)) >= voice_settings.quote_fuzzy_min_chars
        tier, ratio = match_quote(typoed, TURN)
        assert tier == "fuzzy"
        assert ratio >= voice_settings.quote_fuzzy_ratio

    def test_fuzzy_is_scored_against_the_WHOLE_turn_not_the_best_window(self):
        """A characteristic worth knowing before trusting the tier.

        `SequenceMatcher` runs the quote against the entire normalised turn, so the ratio
        falls as the turn grows — a typo'd quote of HALF a long answer scores near 0.7 and
        is refused, while the same typos across the whole answer pass. Fuzzy therefore
        rescues mis-transcribed FULL quotes, not mis-transcribed fragments.

        That is a conservative failure — evidence refused rather than invented — which is
        the right direction for this gate, but it means a grader quoting one sentence out
        of a long answer needs one of the three exact tiers to match.
        """
        half_of_it = "i would profile the qeury first then add an idnex on the join colmun"
        assert len(_normalize(half_of_it)) >= voice_settings.quote_fuzzy_min_chars
        tier, ratio = match_quote(half_of_it, TURN)
        assert tier is None, "a fragment reached fuzzy; the ratio is no longer whole-turn"
        assert 0.6 < ratio < voice_settings.quote_fuzzy_ratio

    def test_a_short_quote_never_reaches_fuzzy(self):
        """The second gate. Short strings hit high ratios against anything, so fuzzy is
        refused below a length that makes the ratio mean something."""
        short = "x" * (voice_settings.quote_min_chars + 1)
        assert len(short) < voice_settings.quote_fuzzy_min_chars
        tier, _ = match_quote(short, TURN)
        assert tier is None

    def test_an_invented_quote_is_rejected_and_the_ratio_is_reported(self):
        """The failure this whole module exists to prevent: a model claiming the candidate
        said something they did not. The ratio comes back so a caller can log HOW far off
        it was rather than only that it failed."""
        tier, ratio = match_quote(
            "the candidate explained monad transformers in some considerable detail", TURN
        )
        assert tier is None
        assert 0.0 <= ratio < voice_settings.quote_fuzzy_ratio


class TestNormalisation:
    def test_it_is_idempotent(self):
        once = _normalize(TURN)
        assert _normalize(once) == once

    def test_unicode_forms_that_look_identical_compare_equal(self):
        """NFKC. A transcript and a model reply can disagree on encoding for characters
        that render the same, and that must not be the reason evidence is refused."""
        assert _normalize("ﬁle") == _normalize("file")
        assert _normalize("café") == _normalize("café")

    def test_an_empty_transcript_rejects_rather_than_raising(self):
        """A candidate whose audio was lost has an empty turn. That must be a refusal, not
        a crash in the grader."""
        tier, ratio = match_quote("profile the query first", "")
        assert tier is None
        assert ratio == 0.0
