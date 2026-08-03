"""Replace the two guessed selection constants with measurements.

Run from `backend/`:

    PYTHONPATH=. python scripts/calibrate_selection_constants.py --sessions dumps/
    PYTHONPATH=. python scripts/calibrate_selection_constants.py --sessions dumps/ --write

Selection ranks on information per minute, weighted by the evidence a response is expected
to carry. Both terms shipped as opinions, and between them they decide which modality wins
a ranking — so they decide how long a session runs and what it is made of:

    seconds   `mcq: 75` had no measurement behind it anywhere. No bank carries a time for
              a multiple-choice item, and it multiplies most of the items in a session.
    E[w]      `code: 0.85`, `voice: 0.65` were read off the graders' discount rungs.

`AssessmentState` now records `item_seconds` and `realized_weight_by_item` per response,
so a real corpus answers both.

WHY MEDIAN FOR TIME AND MEAN FOR WEIGHT

Time has a long right tail — a candidate who walks away mid-item records twenty minutes on
a question that takes two. The median describes what an item costs; the mean describes the
interruptions. Evidence weight has no such tail (it is bounded in [0, 1]) and the quantity
selection needs is genuinely the expectation, because that is what it is predicting.

Multiple-choice weight is not measured: the grader sets it from the item's own loading at
confidence 1.0 with no degradation path, so a mean over responses would only reproduce the
loading distribution of whichever items happened to be administered.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from app.services.orchestrator import registry
from app.services.orchestrator.selection_calibration import (
    CALIBRATION_PATH,
    DEFAULT_EXPECTED_WEIGHT,
    MINIMUM_OBSERVATIONS,
)
from app.services.orchestrator.session_dump import SOURCE_LIVE, read_sessions
from app.schemas.orchestration import DEFAULT_SECONDS_BY_MODALITY

# A response nobody was present for. Above this, the record describes an interruption
# rather than an item, and it is excluded from the timing sample rather than trusted.
MAX_PLAUSIBLE_SECONDS = 3600.0
MIN_PLAUSIBLE_SECONDS = 2.0


def collect(records: list[dict]) -> tuple[dict[str, list[float]], dict[str, list[float]], int, int]:
    """Per-modality seconds and realised weights across the corpus."""
    seconds: dict[str, list[float]] = defaultdict(list)
    weights: dict[str, list[float]] = defaultdict(list)
    live = skipped = 0

    banks: dict[str, dict[str, str]] = {}

    for record in records:
        if record.get("source") != SOURCE_LIVE:
            skipped += 1
            continue
        live += 1
        bank_id = str(record.get("bank_id") or "")
        if bank_id not in banks:
            try:
                banks[bank_id] = {
                    item.item_id: item.modality
                    for item in registry.get_bank(bank_id).all_items()
                }
            except (KeyError, OSError, ValueError):
                banks[bank_id] = {}
        modality_of = banks[bank_id]

        state = record.get("state") or {}
        for item_id, value in (state.get("item_seconds") or {}).items():
            modality = modality_of.get(item_id)
            if modality is None:
                continue
            elapsed = float(value)
            if MIN_PLAUSIBLE_SECONDS <= elapsed <= MAX_PLAUSIBLE_SECONDS:
                seconds[modality].append(elapsed)
        for item_id, value in (state.get("realized_weight_by_item") or {}).items():
            modality = modality_of.get(item_id)
            if modality is not None:
                weights[modality].append(float(value))

    return dict(seconds), dict(weights), live, skipped


def summarise(
    seconds: dict[str, list[float]], weights: dict[str, list[float]]
) -> tuple[dict, dict, dict]:
    measured_seconds: dict[str, float] = {}
    measured_weights: dict[str, float] = {}
    counts: dict[str, int] = {}

    for modality in sorted(set(seconds) | set(weights)):
        timing = seconds.get(modality, [])
        weighting = weights.get(modality, [])
        counts[modality] = min(len(timing), len(weighting)) or max(len(timing), len(weighting))
        if timing:
            measured_seconds[modality] = round(statistics.median(timing), 1)
        if weighting and modality != "mcq":
            measured_weights[modality] = round(statistics.fmean(weighting), 4)

    return measured_seconds, measured_weights, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions", type=Path, required=True, help="a .jsonl dump or a directory of them"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"write {CALIBRATION_PATH.name}; without it, report only",
    )
    args = parser.parse_args()

    if not args.sessions.exists():
        print(f"no session corpus at {args.sessions}", file=sys.stderr)
        return 1

    records = read_sessions(args.sessions)
    seconds, weights, live, skipped = collect(records)
    measured_seconds, measured_weights, counts = summarise(seconds, weights)

    print(f"corpus: {len(records)} records ({live} live, {skipped} not live)\n")
    print(f"{'modality':<9} {'n':>5} {'seconds':>18} {'E[w]':>18}")
    for modality in sorted(set(DEFAULT_SECONDS_BY_MODALITY) | set(counts)):
        n = counts.get(modality, 0)
        enough = n >= MINIMUM_OBSERVATIONS
        default_s = DEFAULT_SECONDS_BY_MODALITY.get(modality)
        default_w = DEFAULT_EXPECTED_WEIGHT.get(modality)
        shown_s = measured_seconds.get(modality)
        shown_w = measured_weights.get(modality)
        seconds_text = (
            f"{shown_s:.1f} (was {default_s:.0f})"
            if shown_s is not None and enough
            else f"— keep {default_s:.0f}"
        )
        if modality == "mcq":
            weight_text = "exact 1.00"
        elif shown_w is not None and enough:
            weight_text = f"{shown_w:.3f} (was {default_w:.2f})"
        else:
            weight_text = f"— keep {default_w:.2f}"
        print(f"{modality:<9} {n:>5} {seconds_text:>18} {weight_text:>18}")

    below = sorted(m for m, n in counts.items() if n < MINIMUM_OBSERVATIONS)
    if below:
        print(
            f"\n{', '.join(below)} are below the {MINIMUM_OBSERVATIONS}-observation floor; "
            "their documented defaults stand."
        )

    payload = {
        "generated": date.today().isoformat(),
        "corpus": str(args.sessions),
        "sessions": live,
        "minimum_observations": MINIMUM_OBSERVATIONS,
        "observations": counts,
        "seconds_by_modality": measured_seconds,
        "expected_weight_by_modality": measured_weights,
    }

    if not args.write:
        print(f"\n--write would replace {CALIBRATION_PATH}")
        return 0

    if live == 0:
        print(
            "\nrefusing to write: no live sessions in the corpus. Simulated responses "
            "carry the weights the simulator gave them and take no wall clock at all.",
            file=sys.stderr,
        )
        return 1

    CALIBRATION_PATH.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {CALIBRATION_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
