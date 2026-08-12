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

    # fuzzy
    ratio = SequenceMatcher(None, norm_q, norm_turn).ratio() if norm_turn else 0.0
    if (
        len(norm_q) >= voice_settings.quote_fuzzy_min_chars
        and ratio >= voice_settings.quote_fuzzy_ratio
    ):
        return "fuzzy", ratio
    return None, ratio


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
