"""The guard on model-rewritten question stems.

WHAT IT IS PROTECTING

Rephrasing lets a model restate a stem in plainer language. That is a real accessibility
win and a real hazard: a rewrite is text a model produced, administered to a candidate, and
scored against an answer key the model also saw. Four things can go wrong, and each has a
check here.

**An answer leak** is the one that matters most, because it does not fail — it inflates. A
stem that restates the correct option turns a discriminating item into a free mark, and the
posterior takes that as evidence of ability. Checked first and most strictly.

**A polarity flip** is worse than a wrong answer: "which is NOT true" rewritten as "which is
true" makes every previously-correct response wrong, and the item's calibration now describes
a different question.

**A dropped calibrated token** silently changes what is being measured. An item whose `a`
and `b` were estimated against `O(n log n)` is not the same item once that becomes "fast".

**A much longer rewrite** is a model that has started explaining rather than restating.

THE PROPERTY THAT MAKES ALL OF THIS SAFE

Every rejection returns the ORIGINAL stem. There is no path through this module that
administers unvalidated text, so the worst a bad rewrite costs is the tokens spent on it —
which is why the feature can ship at all.

Untested before this file, at 39% coverage.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.services.adaptive.rephrase import MAX_LENGTH_RATIO, check_rephrase

ORIGINAL = "Which statement about list comprehensions is not true in Python 3?"
OPTIONS = [
    "They create a new list rather than mutating the source",
    "They can include a conditional filter clause",
    "They leak the loop variable into the enclosing scope",
    "They are generally faster than an equivalent explicit for loop",
]
ANSWER = 2


def check(rewritten: str):
    return check_rephrase(ORIGINAL, rewritten, OPTIONS, ANSWER)


class TestTheInvariantEverythingElseRestsOn:
    """`stem` is always safe to administer, whatever else the outcome says."""

    @pytest.mark.parametrize(
        "rewritten",
        [
            "",
            "   ",
            ORIGINAL,
            "Which statement about list comprehensions is true in Python 3?",
            "They leak the loop variable into the enclosing scope — which is false?",
            "Which of these is wrong? " + "padding " * 40,
        ],
    )
    def test_a_rejected_rewrite_returns_the_original_stem(self, rewritten):
        result = check(rewritten)
        assert result.stem in (ORIGINAL, rewritten.strip())
        if not result.ok:
            assert result.stem == ORIGINAL, "a refused rewrite escaped to the candidate"

    def test_a_rejection_always_says_why(self):
        """An operator reading a log needs to know which guard fired; four different
        failures reported identically would be four different bugs to diagnose."""
        result = check("Which statement about list comprehensions is true in Python 3?")
        assert result.ok is False
        assert result.reason


class TestNoOpsAreAccepted:
    def test_an_empty_rewrite_is_a_pass_not_a_failure(self):
        """The model declining to rewrite is not an error — it is the common case."""
        assert check("").ok is True
        assert check("").stem == ORIGINAL

    def test_an_identical_rewrite_is_a_pass(self):
        assert check(ORIGINAL).ok is True


class TestTheAnswerLeak:
    def test_a_rewrite_restating_the_correct_option_is_refused(self):
        """The failure that inflates rather than breaks."""
        result = check(
            "Is it false that they leak the loop variable into the enclosing scope?"
        )
        assert result.ok is False
        assert "correct option" in result.reason

    def test_a_rewrite_restating_a_DISTRACTOR_is_also_refused(self):
        """Less obvious and still wrong: naming a wrong option in the stem removes it from
        consideration, so the item is now a three-way choice with calibration for four."""
        result = check(
            "They can include a conditional filter clause — which claim here is not true?"
        )
        assert result.ok is False
        assert "distractor" in result.reason

    def test_a_short_option_is_not_matched_as_a_leak(self):
        """The 12-character floor. Without it, an option like "None" would make any stem
        containing that word a leak, and nothing would ever pass."""
        short_options = ["Yes", "No", "Never", "Always"]
        result = check_rephrase(
            "Is it never correct to mutate a list while iterating it?",
            "Is it never right to change a list while looping over it?",
            short_options,
            2,
        )
        assert result.ok is True


class TestPolarity:
    def test_dropping_the_negation_is_refused(self):
        result = check("Which statement about list comprehensions is true in Python 3?")
        assert result.ok is False
        assert "polarity" in result.reason

    def test_adding_a_negation_is_refused_too(self):
        """Symmetry matters: the count must MATCH, not merely be non-zero."""
        result = check(
            "Which statement about list comprehensions is not never true in Python 3?"
        )
        assert result.ok is False
        assert "polarity" in result.reason

    def test_a_rewrite_keeping_the_negation_passes(self):
        result = check(
            "Which of these claims about list comprehensions is not correct in Python 3?"
        )
        assert result.ok is True
        assert result.stem != ORIGINAL


class TestCalibratedTokens:
    def test_dropping_a_protected_token_is_refused(self):
        """An item calibrated against `O(n log n)` measures something else once the stem
        says "fast" instead."""
        original = "Which sort achieves `O(n log n)` in the worst case?"
        result = check_rephrase(
            original, "Which sort is always fast in the worst case?", OPTIONS, ANSWER
        )
        assert result.ok is False
        assert "calibrated tokens" in result.reason

    def test_the_reason_names_the_tokens_that_went_missing(self):
        original = "Given `sorted()` and `reversed()`, which returns an iterator?"
        result = check_rephrase(
            original, "Which of those two returns an iterator?", OPTIONS, ANSWER
        )
        assert result.ok is False
        assert "sorted" in result.reason or "reversed" in result.reason

    def test_a_bare_numeral_is_protected_too(self):
        """`3` in "Python 3" is a calibrated token, so dropping the version is refused.

        The strictness is deliberate and stays: an item about Python 3 is not the same item
        once the version goes, and its difficulty was estimated against the version that was
        there. A rejection costs only the tokens spent on the rewrite, so erring this way is
        the right bias.
        """
        result = check("Which claim about list comprehensions is not correct?")
        assert result.ok is False
        assert "calibrated tokens" in result.reason

    def test_the_reason_quotes_the_phrase_rather_than_the_bare_token(self):
        """What made the numeral case confusing was the REPORT, not the check.

        "drops calibrated tokens: ['3']" says nothing about WHICH `3` in a stem that may
        contain several. The surrounding phrase makes the message actionable without
        loosening anything.
        """
        result = check("Which claim about list comprehensions is not correct?")
        assert "Python 3" in result.reason
        assert "['3']" not in result.reason

    def test_keeping_every_protected_token_passes(self):
        original = "Which sort achieves `O(n log n)` in the worst case?"
        result = check_rephrase(
            original,
            "Which sorting algorithm reaches `O(n log n)` even in the worst case?",
            OPTIONS,
            ANSWER,
        )
        assert result.ok is True


class TestLength:
    def test_a_much_longer_rewrite_is_refused(self):
        """A model that has started explaining rather than restating. The ratio is a proxy
        for that, and it is checked before the content rules because it is the cheapest."""
        padded = ORIGINAL + " " + ("and here is some further elaboration " * 5)
        assert len(padded) > len(ORIGINAL) * MAX_LENGTH_RATIO
        result = check(padded)
        assert result.ok is False
        assert "longer" in result.reason

    def test_a_slightly_longer_rewrite_is_fine(self):
        result = check(
            "Which one of these claims about list comprehensions is not correct in "
            "Python 3 here?"
        )
        assert len(result.stem) >= len(ORIGINAL)
        assert result.ok is True
