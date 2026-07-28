"""Lightweight language checks for spoken open answers (English-only policy).

Used by the Live room (nudge interviewer) and the open grader (score clamp).
Not a full language ID model — enough to catch CJK/Devanagari and common
romanized-Japanese ASR dumps that the Live model otherwise treats as a finished answer.
"""

from __future__ import annotations

import re

# Scripts that are never English assessment evidence.
_NON_LATIN = re.compile(
    r"[\u0400-\u04FF\u0600-\u06FF\u0900-\u097F"
    r"\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF\uFF66-\uFF9D]"
)

# Common romanized Japanese / filler that appears when ASR latinizes 日本語.
_ROMAJI_MARKERS = re.compile(
    r"\b("
    r"etto|ano|desu|masu|arimasu|watashi|watashiwa|kore|sore|are|"
    r"nani|doushite|chotto|dakara|demo|kara|node|deshita|shimashita|"
    r"hai|iie|onegaishimasu|sumimasen|wakarimasen|tappuru|imyutaburu|"
    r"chan|totte|towaku|bushin|suzai|teresu|dinti"
    r")\b",
    re.IGNORECASE,
)

_COMMON_EN = frozenset(
    """
    the a an is are was were be been being to of in for on with as by at from
    or and but if then that this these those it its i you we they he she not no
    yes do does did have has had can could would should will just so than when
    what which who how why where there here my your our their about into over
    after before because while also only same other into list tuple error raise
    first then because so result remains change assign
    """.split()
)

_WORD = re.compile(r"[A-Za-z']+")


def looks_non_english(text: str) -> bool:
    """True when a candidate turn is substantially not English."""
    raw = (text or "").strip()
    if len(raw) < 12:
        return False
    if _NON_LATIN.search(raw):
        return True

    romaji_hits = len(_ROMAJI_MARKERS.findall(raw))
    if romaji_hits >= 3:
        return True

    tokens = _WORD.findall(raw.lower())
    if len(tokens) < 16:
        # Short latin turns: rely on romaji / script only.
        return romaji_hits >= 2

    en = sum(1 for t in tokens if t in _COMMON_EN)
    ratio = en / len(tokens)
    # Fluent English usually keeps a healthy function-word rate. Romanized
    # Japanese ASR dumps are long but sparse in English function words.
    if ratio < 0.12 and romaji_hits >= 1:
        return True
    if ratio < 0.08 and len(tokens) >= 40:
        return True
    return False


def candidate_turns_non_english(turns: list[dict] | list) -> list[str]:
    """Return texts of candidate turns flagged as non-English."""
    flagged: list[str] = []
    for turn in turns or []:
        if isinstance(turn, dict):
            role = turn.get("role")
            text = turn.get("text") or ""
        else:
            role = getattr(turn, "role", None)
            text = getattr(turn, "text", "") or ""
        if role != "candidate":
            continue
        if looks_non_english(str(text)):
            flagged.append(str(text))
    return flagged
