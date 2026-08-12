"""Evidence-quote matcher — four tiers, two hard gates, one escape hatch."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.schemas.voice import QuoteTier

_DISFLUENCY = re.compile(
    r"\b(um|uh|er|ah|hmm+|\[inaudible\]|\[unclear\])\b", re.IGNORECASE
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    text = _DISFLUENCY.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_quote(quote: str | None, turn_text: str) -> tuple[QuoteTier | None, float]:
    """Return (tier, ratio). tier None means rejected."""
    if quote is None:
        return "described", 1.0

    q = quote.strip()
    if not q:
        return None, 0.0

    # Gate 1 — minimum length before any tier
    norm_q = _normalize(q)
    if len(norm_q) < voice_settings.quote_min_chars:
        return None, 0.0

    turn = turn_text or ""
    if q in turn:
        return "exact", 1.0

    norm_turn = _normalize(turn)
    if norm_q and norm_q in norm_turn:
        return "normalized", 1.0

    # subsequence: content tokens in order, gaps ≤ 3
    q_toks = norm_q.split()
    t_toks = norm_turn.split()
    if q_toks and _subsequence(q_toks, t_toks, max_gap=3):
        return "subsequence", 1.0

    # fuzzy — scored against the BEST WINDOW, not the whole turn.
    ratio = _partial_ratio(norm_q, norm_turn)
    if (
        len(norm_q) >= voice_settings.quote_fuzzy_min_chars
        and ratio >= voice_settings.quote_fuzzy_ratio
    ):
        return "fuzzy", ratio
    return None, ratio


def _partial_ratio(needle: str, hay: str) -> float:
    """Similarity of `needle` to the best-matching WINDOW of `hay`.

    WHY NOT A PLAIN RATIO AGAINST THE WHOLE TURN

    `SequenceMatcher(needle, hay).ratio()` divides by the combined length, so it falls as
    the turn grows however good the local match is. A grader quoting one mis-transcribed
    sentence out of a two-minute answer scored around 0.70 against a 0.82 gate and was
    refused, while the same typos spread across a short answer passed — so whether real
    evidence counted depended on how long the candidate spoke.

    Anchoring on the matching blocks and scoring a needle-sized window around each is the
    standard partial-ratio construction. It compares like with like: how well does this
    quote match the part of the transcript it is actually about.

    The gates above are unchanged, so this loosens WHERE the comparison is made and not how
    similar a quote has to be.
    """
    if not needle or not hay:
        return 0.0
    if len(needle) >= len(hay):
        return SequenceMatcher(None, needle, hay).ratio()

    best = 0.0
    for block in SequenceMatcher(None, needle, hay).get_matching_blocks():
        start = max(0, block.b - block.a)
        window = hay[start : start + len(needle)]
        if window:
            best = max(best, SequenceMatcher(None, needle, window).ratio())
    return best


def _subsequence(needle: list[str], hay: list[str], max_gap: int) -> bool:
    i = 0
    gap = 0
    for tok in hay:
        if i >= len(needle):
            return True
        if tok == needle[i]:
            i += 1
            gap = 0
        else:
            if i > 0:
                gap += 1
                if gap > max_gap:
                    # reset search from next needle start opportunity
                    i = 0
                    gap = 0
                    if tok == needle[0]:
                        i = 1
    return i >= len(needle)
