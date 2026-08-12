"""Guard for model-rewritten question stems.

Rephrasing is off by default (`cat_rephrasing_enabled`), because a stem's difficulty is
calibrated against its exact wording and a rewrite is therefore not the item the
parameters describe.

What this guard can and cannot do is worth being precise about, since the distinction
decides how much the feature can be trusted: it checks SURFACE fidelity — answer leaks,
polarity flips, dropped identifiers, length blow-ups. It cannot check that difficulty was
preserved. A rewrite can pass every check here and still be a materially easier question.
That is why the feature is opt-in rather than merely guarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words whose insertion or removal inverts what the question asks. Grading uses the
# original answer key, so a flipped stem marks a correct answer wrong — the single most
# damaging thing a rewrite can do, and invisible unless checked explicitly.
NEGATION_TOKENS = ("not", "never", "except", "false", "incorrect", "cannot", "none")

# Anything that must survive verbatim: identifiers, calls, dotted paths, numbers.
TOKEN_PATTERN = re.compile(
    r"[A-Za-z_][\w.]*\(\)|[A-Za-z_]\w*\.\w+|\b\d[\d_.]*\b|`[^`]+`"
)

MAX_LENGTH_RATIO = 1.5


@dataclass(frozen=True)
class RephraseCheck:
    """Outcome of checking a rewrite. `stem` is always safe to administer."""

    stem: str
    ok: bool
    reason: str = ""


def _negation_count(text: str) -> int:
    words = re.findall(r"[a-z]+", text.lower())
    return sum(words.count(token) for token in NEGATION_TOKENS)


def _protected_tokens(text: str) -> set[str]:
    return {m.strip("`") for m in TOKEN_PATTERN.findall(text)}


def check_rephrase(
    original: str, rewritten: str, options: list[str], answer_index: int
) -> RephraseCheck:
    """Validate a rewrite, falling back to the original stem whenever anything is off.

    Every rejection returns the ORIGINAL stem, so a bad rewrite costs nothing but the
    tokens spent on it. There is no path here that administers unvalidated text.
    """
    candidate = (rewritten or "").strip()
    if not candidate or candidate == original.strip():
        return RephraseCheck(original, True)

    if len(candidate) > len(original) * MAX_LENGTH_RATIO:
        return RephraseCheck(
            original, False, "rewrite is substantially longer than the original"
        )

    # An answer leak is the failure that silently inflates a candidate's score, so the
    # correct option is checked first and most strictly.
    correct = options[answer_index].strip().lower()
    lowered = candidate.lower()
    if len(correct) > 12 and correct in lowered:
        return RephraseCheck(original, False, "rewrite restates the correct option")
    for index, option in enumerate(options):
        text = option.strip().lower()
        if index != answer_index and len(text) > 12 and text in lowered:
            return RephraseCheck(original, False, "rewrite restates a distractor")

    if _negation_count(candidate) != _negation_count(original):
        return RephraseCheck(
            original, False, "rewrite changes the polarity of the question"
        )

    missing = _protected_tokens(original) - _protected_tokens(candidate)
    if missing:
        return RephraseCheck(
            original, False, f"rewrite drops calibrated tokens: {sorted(missing)[:3]}"
        )

    return RephraseCheck(candidate, True)
