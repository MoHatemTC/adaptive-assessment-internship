"""
Build the CAT-ready enriched bank from per-competency authored item files.

Two jobs the authoring step cannot do consistently:

1. IRT calibration. The engine falls back to DIFFICULTY_MAP/DISCRIMINATION_MAP
   when an item carries only labels, which collapses every item in a band onto
   one b value. A bank with five distinct b values leaves Fisher information
   spiky and flat in between. Here each item gets its own b, spread inside its
   band, so b coverage is continuous over [-2.4, +2.4].

2. Answer-position balance. Authored keys cluster (the source bank was 53/62 "B").
   Positions are reassigned from a balanced multiset under a fixed seed, so the
   key distribution is uniform and reproducible.

Option *lengths* are deliberately not touched here — length balance is an
authoring property, and rebalancing positions cannot create it. validate_bank.py
is what holds that line.

Usage: python build_enriched_bank.py <in_dir> <out.json>
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

SEED = 20260715

# b spread inside each difficulty band. Bands are contiguous and centred on the
# engine's DIFFICULTY_MAP anchors (-2, -1, 0, 1, 2) so label-only consumers and
# numeric consumers agree to within half a band.
B_BANDS = {
    "very_easy": (-2.4, -1.6),
    "easy": (-1.4, -0.6),
    "medium": (-0.4, 0.4),
    "hard": (0.6, 1.4),
    "very_hard": (1.6, 2.4),
}

# a spread inside each discrimination band, centred on the engine's
# DISCRIMINATION_MAP anchors (0.6, 1.0, 1.5).
A_BANDS = {
    "low": (0.50, 0.75),
    "medium": (0.85, 1.15),
    "high": (1.35, 1.80),
}

N_OPTIONS = 4
C_GUESS = 1.0 / N_OPTIONS  # 4-option MCQ; app.enrich_question recomputes this identically.

FIELD_ORDER = [
    "id",
    "competency",
    "sub_competency",
    "difficulty",
    "discrimination",
    "stem",
    "options",
    "answer_index",
    "a",
    "b",
    "c",
    "rationale",
    "misconceptions",
]


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n == 1:
        return [(lo + hi) / 2]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


DIFFICULTY_ORDER = ["very_easy", "easy", "medium", "hard", "very_hard"]


def _interleave(counts: dict[str, int]) -> list[str]:
    """A maximally spread sequence containing exactly `counts` of each label.

    Each label's items are placed at evenly spaced fractional positions, so any
    contiguous slice of the result carries roughly the global mix.
    """
    placed: list[tuple[float, str]] = []
    for label, n in sorted(counts.items()):
        for i in range(n):
            placed.append(((i + 0.5) / n, label))
    placed.sort()
    return [label for _, label in placed]


def rebalance_discrimination(items: list[dict]) -> None:
    """Redistribute discrimination labels so `a` is independent of `b`.

    The bank's own design rule says `a` must be independent of `b`, and the reason is
    measurement rather than tidiness: pinning high `a` to high `b` leaves the sharpest
    items where only strong candidates ever reach them, so weak candidates are measured
    with blunt items exactly where 3PL information is already scarce (c=0.25 means
    P(correct) -> 0.25 as ability falls, and information genuinely vanishes). The authored
    labels violated it -- mean_a ran 0.90 at very_easy to 1.46 at very_hard,
    corr(difficulty, a) = +0.50 -- so the ladder's weak end was doubly starved.

    Why redistributing labels is legitimate and not fabrication: `a` here is *assigned
    from a label*, never calibrated from response data. The incoming labels are an
    authoring judgement about how sharply an item separates, carrying no more empirical
    authority than these do. Both are fictions; this one is the fiction the design rule
    asks for and the one that measures better. The honest fix is a pilot calibration --
    see the README.

    This preserves the exact multiset of labels per competency (so the bank still has the
    same 20 low / 50 medium / 50 high overall) and only changes *which* items carry them,
    dealing a maximally spread sequence across difficulty-ordered items so each band gets
    the same mix. Deterministic: no RNG, ties broken by id.
    """
    by_comp: dict[str, list[dict]] = defaultdict(list)
    for q in items:
        by_comp[q["competency"]].append(q)

    for _comp, group in sorted(by_comp.items()):
        counts: dict[str, int] = defaultdict(int)
        for q in group:
            counts[q["discrimination"]] += 1
        sequence = _interleave(dict(counts))
        group.sort(key=lambda q: (DIFFICULTY_ORDER.index(q["difficulty"]), q["id"]))
        for q, label in zip(group, sequence):
            q["discrimination"] = label


def assign_irt(items: list[dict]) -> None:
    """Give every item its own (a, b, c), spread within its label's band.

    `a` is fixed by the discrimination label, so it goes first. `b` then has one
    degree of freedom worth spending deliberately: which item in a band sits at
    which point inside it. Information is scarcest at the ladder's extremes (a
    candidate at theta=-2.5 has only the very_easy band to draw on), so within
    each band the sharpest item is placed at the band's outer edge rather than
    wherever its id happens to sort. This changes no labels — only which of two
    equally-very_easy items is called b=-2.4 vs b=-1.6.
    """
    by_a: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for q in items:
        by_a[(q["competency"], q["discrimination"])].append(q)

    for (_comp, discrimination), group in by_a.items():
        lo, hi = A_BANDS[discrimination]
        # Order by difficulty, then deal the spread alternately from both ends of the
        # band. Sorting by id and assigning the spread in order left `a` trending with
        # difficulty *inside* each label group by accident — balancing the labels across
        # the ladder still left corr(difficulty, a) at +0.23, because which of ten "high"
        # items got a=1.35 vs a=1.80 was decided by id. Zig-zagging makes consecutive
        # difficulty-ordered items alternate sharp/blunt, so each band's mean `a` lands on
        # the group mean and the correlation goes to ~0.
        group.sort(key=lambda q: (DIFFICULTY_ORDER.index(q["difficulty"]), q["id"]))
        spread = _linspace(lo, hi, len(group))
        zigzag: list[float] = []
        i, j = 0, len(spread) - 1
        while i <= j:
            zigzag.append(spread[i])
            i += 1
            if i <= j:
                zigzag.append(spread[j])
                j -= 1
        for q, a in zip(group, zigzag):
            q["a"] = round(a, 3)

    by_b: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for q in items:
        by_b[(q["competency"], q["difficulty"])].append(q)

    for (_comp, difficulty), group in by_b.items():
        lo, hi = B_BANDS[difficulty]
        slots = _linspace(lo, hi, len(group))
        # Outermost slot first, paired with the highest-a item available.
        slots.sort(key=lambda b: -abs(b))
        group.sort(key=lambda q: (-q["a"], q["id"]))
        for q, b in zip(group, slots):
            q["b"] = round(b, 3)

    for q in items:
        q["c"] = C_GUESS


def rebalance_answer_positions(items: list[dict], seed: int = SEED) -> None:
    """Move each key to a position drawn from a balanced, seeded multiset.

    Swapping the key with whatever sits at the target index leaves the option
    *set* untouched, so this cannot affect length-based cueing either way.
    """
    rng = random.Random(seed)
    by_comp: dict[str, list[dict]] = defaultdict(list)
    for q in items:
        by_comp[q["competency"]].append(q)

    for comp in sorted(by_comp):
        group = sorted(by_comp[comp], key=lambda q: q["id"])
        reps = len(group) // N_OPTIONS + 1
        targets = ([0, 1, 2, 3] * reps)[: len(group)]
        rng.shuffle(targets)
        for q, t in zip(group, targets):
            src = q["answer_index"]
            if src == t:
                continue
            opts, misc = q["options"], q.get("misconceptions")
            opts[src], opts[t] = opts[t], opts[src]
            if misc:
                misc[src], misc[t] = misc[t], misc[src]
            q["answer_index"] = t


def normalize(q: dict) -> dict:
    return {k: q[k] for k in FIELD_ORDER if k in q}


def main() -> int:
    in_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    items: list[dict] = []
    for path in sorted(in_dir.glob("T*.json")):
        chunk = json.loads(path.read_text())
        print(f"  loaded {path.name}: {len(chunk)} items")
        items.extend(chunk)

    ids = [q["id"] for q in items]
    if len(set(ids)) != len(ids):
        dupes = {i for i in ids if ids.count(i) > 1}
        raise SystemExit(f"duplicate ids across competencies: {sorted(dupes)}")

    rebalance_discrimination(items)
    assign_irt(items)
    rebalance_answer_positions(items)
    items.sort(key=lambda q: q["id"])

    out_path.write_text(json.dumps([normalize(q) for q in items], indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(items)} items -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
