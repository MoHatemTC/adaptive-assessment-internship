"""Score one approach against seeded-defect ground truth.

WHY THIS EXISTS

For multiple-choice items, accuracy is measurable because a simulated candidate has a
known ability and a response model to generate answers from. Code has no such thing: a
submission is not drawn from a distribution, and "is this competency estimate right" has
no closed form.

So ground truth is CONSTRUCTED. Every seeded submission in `bank/seeded/` carries a
declared defect and, with it, which competencies it should read as weak, which should
still read as strong, which tests it must fail, and which misconception it displays. The
bank's own verification asserts those declarations match execution exactly. An approach is
then scored on whether it recovers what was seeded.

WHAT IS MEASURED

  separation     do the seeded-weak competencies score BELOW the seeded-strong ones?
                 The headline. An evaluator that cannot separate a boundary-condition
                 defect from correct boundary handling is not diagnosing anything,
                 whatever its scores look like.
  misconception  precision and recall against the seeded misconception code
  anchoring      violations where functional correctness exceeded what execution showed
  robustness     invalid or unusable model output, and fallbacks to objective evidence
  cost           tokens and wall clock per submission

Usage:
    python score_approach.py --approach B --out runs/
    python score_approach.py --approach A --limit 12     # A makes no LLM calls
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cat.config import settings
from cat.pipeline import evaluate_submission, load_bank

SEEDED_DIR = Path(__file__).resolve().parent / "bank" / "seeded"


def load_seeded() -> list[dict]:
    if not SEEDED_DIR.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SEEDED_DIR.glob("*.json"))]


def score_one(question: dict, seeded: dict, approach: str, rubric_id: str) -> dict:
    """Run one seeded submission and compare what came back with what was seeded."""
    started = time.time()
    result = evaluate_submission(question, seeded["code"], approach=approach, rubric_id=rubric_id)
    truth = seeded["ground_truth"]

    by_competency = {e.competency_id: e.score for e in result.competency_evidence}
    weak = [by_competency[c] for c in truth["weak_competencies"] if c in by_competency]
    strong = [by_competency[c] for c in truth["strong_competencies"] if c in by_competency]

    # The core question: did the seeded-weak competencies actually score lower? Reported
    # as a margin rather than a boolean so a near-miss is distinguishable from an
    # inversion — an evaluator that ranks them correctly but by 0.01 is not reliable.
    margin = None
    if weak and strong:
        margin = statistics.mean(strong) - statistics.mean(weak)

    found = set(result_misconceptions(result))
    expected = {truth["misconception_code"]} - {None}

    # The anchor that may never be violated: no scoring path may claim more functional
    # correctness than execution demonstrated.
    ratio = result.execution.passed_test_ratio
    functional = next(
        (c.score for c in result.criterion_scores if c.criterion_id == "functional_correctness"),
        None,
    )
    anchoring_violation = functional is not None and functional > ratio + 0.15

    failed_ids = {o.test_id for o in result.execution.test_results if not o.passed}
    return {
        "question_id": seeded["question_id"],
        "defect": seeded["defect"],
        "approach": approach,
        "rubric_id": rubric_id,
        "separation_margin": margin,
        "separated": None if margin is None else margin > 0,
        "misconception_expected": sorted(expected),
        "misconception_found": sorted(found),
        "misconception_hit": bool(expected & found),
        "misconception_false_positive": bool(found - expected),
        "tests_match_ground_truth": failed_ids == set(truth["expected_failing_test_ids"]),
        "anchoring_violation": anchoring_violation,
        "functional_score": functional,
        "passed_test_ratio": round(ratio, 4),
        "llm_available": result.llm.available,
        "flags": result.flags,
        "latency_ms": int((time.time() - started) * 1000),
        "input_tokens": result.llm.input_tokens,
        "output_tokens": result.llm.output_tokens,
    }


def result_misconceptions(result) -> list[str]:
    return sorted({c for e in result.competency_evidence for c in e.misconception_codes})


def summarise(approach: str, rubric_id: str, rows: list[dict], wall_seconds: float) -> dict:
    graded = [r for r in rows if r["separated"] is not None]
    margins = [r["separation_margin"] for r in graded]
    with_misconception = [r for r in rows if r["misconception_expected"]]

    hits = sum(1 for r in with_misconception if r["misconception_hit"])
    false_positives = sum(1 for r in rows if r["misconception_false_positive"])
    flagged = sum(1 for r in rows if r["flags"])

    return {
        "approach": approach,
        "rubric_id": rubric_id,
        "model": settings.litellm_model,
        "submissions": len(rows),
        "separation": {
            "gradable": len(graded),
            "correct": sum(1 for r in graded if r["separated"]),
            "rate": (sum(1 for r in graded if r["separated"]) / len(graded)) if graded else None,
            "mean_margin": round(statistics.mean(margins), 4) if margins else None,
            "worst_margin": round(min(margins), 4) if margins else None,
        },
        "misconception": {
            "expected_count": len(with_misconception),
            "recall": round(hits / len(with_misconception), 4) if with_misconception else None,
            "false_positives": false_positives,
        },
        "integrity": {
            "anchoring_violations": sum(1 for r in rows if r["anchoring_violation"]),
            "ground_truth_test_mismatches": sum(1 for r in rows if not r["tests_match_ground_truth"]),
            "submissions_with_flags": flagged,
            "llm_unavailable": sum(1 for r in rows if not r["llm_available"]),
        },
        "cost": {
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "tokens_per_submission": round(
                sum(r["input_tokens"] + r["output_tokens"] for r in rows) / max(len(rows), 1)
            ),
            "median_latency_ms": int(statistics.median([r["latency_ms"] for r in rows]))
            if rows
            else 0,
            "wall_clock_s": round(wall_seconds, 1),
        },
        "by_defect": {
            defect: {
                "n": len(items),
                "separated": sum(1 for r in items if r["separated"]),
                "misconception_hit": sum(1 for r in items if r["misconception_hit"]),
            }
            for defect, items in _group(rows).items()
        },
    }


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["defect"], []).append(row)
    return dict(sorted(grouped.items()))


def print_report(summary: dict) -> None:
    sep, mis, integ, cost = (
        summary["separation"], summary["misconception"], summary["integrity"], summary["cost"]
    )
    print(f"\n{'=' * 68}")
    print(f"APPROACH {summary['approach']} x RUBRIC {summary['rubric_id']}   "
          f"model={summary['model']}   n={summary['submissions']} submissions")
    print("=" * 68)
    print("\nCOMPETENCY SEPARATION (do seeded-weak competencies score below seeded-strong?)")
    if sep["rate"] is None:
        print("  no gradable submissions")
    else:
        print(f"  correct       {sep['correct']}/{sep['gradable']}  ({sep['rate']:.1%})")
        print(f"  mean margin   {sep['mean_margin']:+.4f}   worst {sep['worst_margin']:+.4f}")
        print("  (margin is strong-minus-weak; a positive but tiny margin is not reliable)")

    print("\nMISCONCEPTION DETECTION")
    if mis["recall"] is None:
        print("  no seeded misconceptions in this run")
    else:
        print(f"  recall           {mis['recall']:.1%} of {mis['expected_count']} seeded")
        print(f"  false positives  {mis['false_positives']}")

    print("\nINTEGRITY")
    print(f"  anchoring violations        {integ['anchoring_violations']}  "
          "(functional correctness above what execution showed)")
    print(f"  ground-truth test mismatch  {integ['ground_truth_test_mismatches']}  "
          "(non-zero means the BANK is wrong, not the approach)")
    print(f"  submissions with flags      {integ['submissions_with_flags']}")
    print(f"  llm unavailable             {integ['llm_unavailable']}")

    print("\nCOST")
    print(f"  {cost['input_tokens']:,} in / {cost['output_tokens']:,} out  "
          f"({cost['tokens_per_submission']:,} per submission)")
    print(f"  median latency {cost['median_latency_ms']:,}ms   "
          f"wall clock {cost['wall_clock_s']}s")

    print("\nBY DEFECT")
    print(f"  {'defect':22s} {'n':>3s} {'separated':>10s} {'misconception':>14s}")
    for defect, stats in summary["by_defect"].items():
        print(f"  {defect:22s} {stats['n']:3d} {stats['separated']:10d} "
              f"{stats['misconception_hit']:14d}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approach", choices=["A", "B", "C"], default=settings.code_cat_approach)
    parser.add_argument("--rubric", choices=["loose", "mid", "tight"],
                        default=settings.code_cat_rubric)
    parser.add_argument("--matrix", action="store_true",
                        help="run every approach x rubric cell and print the 3x3")
    parser.add_argument("--limit", type=int, default=0, help="cap submissions, for a quick check")
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent submissions; each holds its own sandbox")
    parser.add_argument("--out", default=None, help="directory to write <approach>.json into")
    args = parser.parse_args()

    bank = {q["question_id"]: q for q in load_bank()}
    seeded = load_seeded()
    if not seeded:
        print("No seeded submissions in bank/seeded — nothing to measure.", file=sys.stderr)
        return 1
    if args.limit:
        seeded = seeded[: args.limit]

    print(f"Approach   : {args.approach}")
    print(f"Model      : {settings.litellm_model}")
    print(f"Submissions: {len(seeded)} across {len({s['question_id'] for s in seeded})} questions")

    started = time.time()
    done = 0

    def run(entry: dict) -> dict | None:
        question = bank.get(entry["question_id"])
        if question is None:
            return None
        return score_one(question, entry, args.approach, args.rubric)

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(run, seeded):
            if row is None:
                continue
            rows.append(row)
            done += 1
            print(f"\r  {done}/{len(seeded)} ({time.time() - started:.0f}s)", end="", flush=True)
    print()

    summary = summarise(args.approach, args.rubric, rows, time.time() - started)
    print_report(summary)

    mismatches = summary["integrity"]["ground_truth_test_mismatches"]
    if mismatches:
        print(f"\n  !! {mismatches} submissions failed different tests than the bank declares.")
        print("     The bank's ground truth is wrong; fix it before trusting anything above.")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"approach-{args.approach}-{args.rubric}.json").write_text(
            json.dumps({"summary": summary, "rows": rows}, indent=2)
        )
        print(f"\nwrote {out / f'approach-{args.approach}-{args.rubric}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
