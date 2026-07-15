"""Validate LLM stem rephrasings before they reach the examinee.

Rephrasing is in tension with IRT. Item parameters are calibrated for specific
wording: `b` says how hard *this stem* is. Rewrite the stem and the calibration no
longer strictly describes what was administered. We keep the feature because
adapting register to the examinee is the point of these branches, but "preserve the
meaning" in a system prompt is a request, not a guarantee — an LLM can drop the one
identifier the question turns on, or restate the correct option inside the stem.

So the rephrase is treated as untrusted output and checked before use. Anything that
fails falls back to the original stem, which is always safe: it is the wording the
parameters were calibrated on.

Three failure modes, in order of how much damage they do:

1. ANSWER LEAK — the rewrite pulls in wording unique to the correct option, turning a
   4-way discrimination into a giveaway. Detected differentially: leaking toward the
   key is only meaningful relative to how much the rewrite echoes the distractors.
2. TECHNICAL DRIFT — an identifier, literal, or number the item hinges on is gone
   (`sorted(nums)` -> "the sorting function"). The item now tests something else.
3. LENGTH BLOWUP — a rewrite far longer/shorter than the original is not a rephrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens an item can turn on: dotted/underscored identifiers, calls, numbers with
# separators or decimals, tuple/shape literals like (3, 1). Deliberately broad --
# a false "must keep this" only costs a fallback to the original stem.
_CODE_TOKEN = re.compile(
    r"`[^`]+`"                       # backticked span
    r"|\b\w+\([^)]*\)"               # call: sorted(nums), f()
    r"|\b__\w+__\b"                  # dunder
    r"|\b[A-Za-z_]\w*(?:[._]\w+)+\b" # dotted/underscored: df.shape, top_k
    r"|\(\s*\d+\s*(?:,\s*\d+\s*)+\)" # shape/tuple literal: (3, 4)
    r"|\[[^\]]*\]"                   # list literal: [1, 2, 3]
    r"|\b\d[\d_,.]*\b"               # numbers: 100, 1_000_000, 0.25, 50M
)

_STOPWORDS = frozenset("""
a an the is are was were be been being do does did doing have has had having
and or but if then than that this these those it its of to in on at by for with
from as not no yes you your they them their we our i me my he she his her
which what who whom whose when where why how all any both each few more most
other some such only own same so too very can will just should now would could
may might must shall about into over under again further once here there
""".split())

MIN_LEN_RATIO = 0.5
MAX_LEN_RATIO = 2.0
# Fraction of the key's unique wording that must show up before we call it a leak.
LEAK_ABS = 0.5
# ...and by how much it must out-echo the best distractor. Paraphrasing a stem
# naturally drags in some option vocabulary; only a lopsided pull toward the key
# is evidence of leaking rather than of ordinary restatement.
LEAK_MARGIN = 0.3


@dataclass(frozen=True)
class RephraseCheck:
    ok: bool
    stem: str          # what to actually show — the rephrase if ok, else the original
    reason: str        # empty when ok; why it was rejected otherwise
    code: str = ""     # machine-readable: leak | drift | length | empty


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def code_tokens(text: str) -> set[str]:
    """Normalised technical tokens — the parts of a stem that carry the question."""
    return {m.group(0).strip("` ").lower() for m in _CODE_TOKEN.finditer(text)}


def _leak_score(option: str, others: list[str], stem: str, rephrase: str) -> float:
    """How much of what makes this option *unique* has surfaced in the rephrase.

    Words already in the original stem, or shared with other options, carry no signal:
    only wording unique to this option can give it away.
    """
    unique = _words(option) - _words(stem)
    for other in others:
        unique -= _words(other)
    if not unique:
        return 0.0
    return len(unique & _words(rephrase)) / len(unique)


def check_rephrase(original_stem: str, rephrased: str, options: list[str], answer_index: int) -> RephraseCheck:
    """Decide whether a rephrased stem is safe to administer."""
    rephrased = (rephrased or "").strip()
    original_stem = (original_stem or "").strip()

    if not rephrased or rephrased == original_stem:
        # Not an error: the prompt explicitly allows returning the stem unchanged.
        return RephraseCheck(True, original_stem, "")

    # Checked worst-first, not cheapest-first. Every branch here rejects, so ordering
    # cannot change safety -- but it decides which reason gets reported, and "leaks
    # the key" is what an author needs to see. Length is the weakest signal and would
    # otherwise mask the diagnostic ones (a leaky rewrite is usually also longer).
    key = options[answer_index]
    distractors = [o for i, o in enumerate(options) if i != answer_index]

    if len(key) > 12 and key.lower().strip(" .") in rephrased.lower():
        return RephraseCheck(False, original_stem, "restates the correct option verbatim", "leak")

    key_leak = _leak_score(key, distractors, original_stem, rephrased)
    worst_distractor = max(
        (_leak_score(d, [key] + [x for x in distractors if x is not d], original_stem, rephrased)
         for d in distractors),
        default=0.0,
    )
    if key_leak >= LEAK_ABS and key_leak - worst_distractor >= LEAK_MARGIN:
        return RephraseCheck(
            False, original_stem,
            f"leaks the key: {key_leak:.0%} of the correct option's unique wording appears "
            f"in the rephrase vs {worst_distractor:.0%} for the best distractor",
            "leak",
        )

    missing = code_tokens(original_stem) - code_tokens(rephrased)
    if missing:
        return RephraseCheck(
            False, original_stem,
            f"dropped technical token(s): {', '.join(sorted(missing)[:4])}",
            "drift",
        )

    ratio = len(rephrased) / max(len(original_stem), 1)
    if not MIN_LEN_RATIO <= ratio <= MAX_LEN_RATIO:
        return RephraseCheck(
            False, original_stem,
            f"length ratio {ratio:.2f} outside [{MIN_LEN_RATIO}, {MAX_LEN_RATIO}]",
            "length",
        )

    return RephraseCheck(True, rephrased, "")
