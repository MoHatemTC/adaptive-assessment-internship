"""Run the full approach x rubric factorial and print the 3x3.

Two factors, crossed:

    approach   A objective-only | B test-anchored | C LLM-led
    rubric     loose | mid | tight

Crossed rather than paired because pairing each approach with its "natural" rubric
confounds the two: if C+tight beat A+loose you could not say whether the approach or the
rubric did it. The full grid separates them, and shows interaction — whether a tight
rubric substitutes for code-level anchoring, or whether it cannot.

BUILT-IN VALIDITY CHECK. The rubric only ever reaches the model, and approach A never
calls it, so A's three cells must come out identical. If they do not, the difference is
run-to-run noise and every rubric effect elsewhere in the grid should be read against that
noise floor rather than as signal. The runner checks this and says so.

Usage:
    python run_matrix.py --out runs/                 # all 9 cells, 60 submissions each
    python run_matrix.py --limit 12 --out runs/      # quick pilot
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cat.pipeline import load_bank
from score_approach import load_seeded, score_one, summarise

APPROACHES = ["A", "B", "C"]
RUBRICS = ["loose", "mid", "tight"]


def run_cell(bank: dict, seeded: list[dict], approach: str, rubric: str, workers: int) -> dict:
    started = time.time()

    def run(entry: dict) -> dict | None:
        question = bank.get(entry["question_id"])
        return score_one(question, entry, approach, rubric) if question else None

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(run, seeded):
            if row is not None:
                rows.append(row)
    return {"summary": summarise(approach, rubric, rows, time.time() - started), "rows": rows}


def _fmt(value, spec: str = ".0%") -> str:
    return "—" if value is None else format(value, spec)


def print_matrix(cells: dict[tuple[str, str], dict]) -> None:
    def grid(title: str, get, spec: str = ".0%") -> None:
        print(f"\n{title}")
        print(f"  {'':10s} " + "".join(f"{r:>12s}" for r in RUBRICS))
        for approach in APPROACHES:
            row = "".join(
                f"{_fmt(get(cells[(approach, r)]['summary']), spec):>12s}" for r in RUBRICS
            )
            print(f"  approach {approach} " + row)

    grid("SEPARATION — seeded-weak competencies scored below seeded-strong",
         lambda s: s["separation"]["rate"])
    grid("  mean margin (strong minus weak; larger is a more decisive diagnosis)",
         lambda s: s["separation"]["mean_margin"], "+.4f")
    grid("MISCONCEPTION RECALL — seeded defect correctly named",
         lambda s: s["misconception"]["recall"])
    grid("  false positives (count, lower is better)",
         lambda s: s["misconception"]["false_positives"], "d")
    grid("ANCHORING VIOLATIONS — functional correctness claimed above execution",
         lambda s: s["integrity"]["anchoring_violations"], "d")
    grid("TOKENS PER SUBMISSION",
         lambda s: s["cost"]["tokens_per_submission"], ",d")

    # The control arm. Stated explicitly rather than left for a reader to notice.
    a_cells = [cells[("A", r)]["summary"] for r in RUBRICS]
    rates = {s["separation"]["rate"] for s in a_cells}
    margins = {s["separation"]["mean_margin"] for s in a_cells}
    print("\nCONTROL — approach A never calls the model, so its three cells must match")
    if len(rates) == 1 and len(margins) == 1:
        print("  ✓ identical across rubrics, as required. Rubric effects elsewhere are real.")
    else:
        print(f"  !! A varies across rubrics: rates {sorted(rates)}, margins {sorted(margins)}")
        print("     The pipeline is leaking the rubric into the deterministic path, OR the")
        print("     run is noisy. Either way, read every rubric effect below against this.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=0, help="cap submissions per cell")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="runs")
    args = parser.parse_args()

    bank = {q["question_id"]: q for q in load_bank()}
    seeded = load_seeded()
    if args.limit:
        # Stratified by defect so a capped run still covers every failure mode rather
        # than whichever ones sort first.
        by_defect: dict[str, list[dict]] = {}
        for entry in seeded:
            by_defect.setdefault(entry["defect"], []).append(entry)
        per = max(1, args.limit // max(len(by_defect), 1))
        seeded = [e for items in by_defect.values() for e in items[:per]]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(APPROACHES) * len(RUBRICS)} cells x {len(seeded)} submissions "
          f"= {len(APPROACHES) * len(RUBRICS) * len(seeded)} runs\n")

    cells: dict[tuple[str, str], dict] = {}
    started = time.time()
    for approach in APPROACHES:
        for rubric in RUBRICS:
            label = f"{approach} x {rubric}"
            print(f"  running {label:14s} …", end="", flush=True)
            cell = run_cell(bank, seeded, approach, rubric, args.workers)
            cells[(approach, rubric)] = cell
            summary = cell["summary"]
            print(f" separation {_fmt(summary['separation']['rate'])}  "
                  f"recall {_fmt(summary['misconception']['recall'])}  "
                  f"({summary['cost']['wall_clock_s']:.0f}s)")
            (out / f"cell-{approach}-{rubric}.json").write_text(json.dumps(cell, indent=2))

    print_matrix(cells)
    print(f"\ntotal wall clock {time.time() - started:.0f}s · cells written to {out}/")

    mismatches = sum(
        c["summary"]["integrity"]["ground_truth_test_mismatches"] for c in cells.values()
    )
    if mismatches:
        print(f"\n!! {mismatches} ground-truth mismatches — the BANK is wrong, not an "
              "approach. Fix it before reading anything above.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
