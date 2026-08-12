#!/usr/bin/env python3
"""Rebuild backend bank + per-item rubrics from the open bank / global rubric.

Usage:
  python scripts/build_open_bank.py \\
    --bank /path/to/ai_ml_engineer_open_bank.json \\
    --rubric /path/to/ai_ml_engineer_open_grading_rubric.json
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "engine" / "data"
OUT_BANK = DATA / "question_bank.json"
OUT_GLOBAL = DATA / "open_grading_rubric.json"
OUT_RUBRICS = DATA / "voice_rubrics"

# Keep a ≥ 1.8 so information stays competitive if Stage-B mixes with MCQ.
# Author values below the floor are lifted; values already in range pass through.
A_FLOOR, A_CEILING = 1.8, 2.3
# Rubric floor for constructed response under fractional likelihood (engine design).
# The source bank ships c=0.0; we set OPEN_RUBRIC_FLOOR so low scores do not
# push theta upward near the floor. Documented in calibration.py.
OPEN_RUBRIC_FLOOR = 0.15


def _weights_from_global(global_rubric: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in global_rubric.get("scoring_frame", {}).get("criteria", []):
        pct = float(row.get("weight_pct", 0.0))
        out[row["criterion_id"]] = pct / 100.0
    return out


def _adapt_item(raw: dict, weight_by_crit: dict[str, float]) -> tuple[dict, dict]:
    item = json.loads(json.dumps(raw))  # deep copy
    open_p = item["open"]
    primary = item["measures"][0]["variable"] if item.get("measures") else ""

    # Streamlit / evaluator expect `prompt`
    if "question" in open_p and "prompt" not in open_p:
        open_p["prompt"] = open_p["question"]
    open_p.setdefault("question_type", "voice_open_ended")
    open_p.setdefault("title", item["item_id"])
    open_p.setdefault("maximum_probes", 2)
    open_p.setdefault("allowed_probe_types", ["clarify", "elaborate"])
    open_p.setdefault("maximum_answer_seconds", max(int(open_p.get("estimated_time_seconds", 240)), 180))

    # CAT: keep author a/b; clamp a into [1.8, 2.3]; apply rubric floor for c.
    a = float(item["cat"]["a"])
    a = min(max(a, A_FLOOR), A_CEILING)
    item["cat"] = {
        "a": round(a, 4),
        "b": round(float(item["cat"]["b"]), 4),
        "c": OPEN_RUBRIC_FLOOR,
    }

    rubric_id = f"rubric_{item['item_id']}"
    open_p["rubric_id"] = rubric_id

    criteria = []
    for crit in open_p.get("rubric_criteria") or []:
        cid = crit["criterion_id"]
        criteria.append(
            {
                "criterion_id": cid,
                "competency_id": primary,
                "weight": round(weight_by_crit.get(cid, 0.25), 4),
                "required": cid != "clarity_and_communication",
                "maximum_score": float(crit.get("maximum_score", 5)),
                "descriptor": crit.get("descriptor", ""),
            }
        )

    # Max-normalise measures from criterion weights (primary already 1.0 typically)
    peak = max((c["weight"] for c in criteria), default=1.0) or 1.0
    # Keep bank measures as authored (usually single competency @ 1.0)
    if not item.get("measures"):
        item["measures"] = [{"variable": primary, "weight": 1.0}]

    rubric = {
        "rubric_id": rubric_id,
        "version": "1.0",
        "item_id": item["item_id"],
        "competency": primary.split(".")[0] if primary else item.get("competency", ""),
        "criteria": criteria,
        "expected_answer_points": open_p.get("expected_answer_points") or [],
        "common_pitfalls": open_p.get("common_pitfalls") or [],
        "reference_answer": open_p.get("reference_answer") or "",
        "global_rubric": "open_grading_rubric.json",
    }
    return item, rubric


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    args = parser.parse_args()

    bank = json.loads(args.bank.read_text(encoding="utf-8"))
    global_rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    weight_by_crit = _weights_from_global(global_rubric)

    items_out: list[dict] = []
    rubrics: list[tuple[str, dict]] = []
    for raw in bank["items"]:
        item, rubric = _adapt_item(raw, weight_by_crit)
        items_out.append(item)
        rubrics.append((rubric["rubric_id"], rubric))

    # Stable C1..C10 order
    def sort_key(it: dict) -> tuple:
        var = it["measures"][0]["variable"]
        main, _, sub = var.partition(".")
        n = int(main[1:]) if main.startswith("C") and main[1:].isdigit() else 99
        return (n, sub, it["item_id"])

    items_out.sort(key=sort_key)

    OUT_BANK.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": bank.get("schema_version", 2),
        "modality": "open",
        "source_bank": args.bank.name,
        "source_rubric": args.rubric.name,
        "adaptation": {
            "a_floor": A_FLOOR,
            "a_ceiling": A_CEILING,
            "c": OPEN_RUBRIC_FLOOR,
            "prompt_alias": "open.question copied to open.prompt",
        },
        "items": items_out,
    }
    OUT_BANK.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_GLOBAL.write_text(
        json.dumps(global_rubric, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if OUT_RUBRICS.exists():
        shutil.rmtree(OUT_RUBRICS)
    OUT_RUBRICS.mkdir(parents=True, exist_ok=True)
    for rid, rubric in rubrics:
        (OUT_RUBRICS / f"{rid}.json").write_text(
            json.dumps(rubric, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    a_vals = [i["cat"]["a"] for i in items_out]
    print(
        f"Wrote {len(items_out)} items → {OUT_BANK}\n"
        f"Wrote global rubric → {OUT_GLOBAL}\n"
        f"Wrote {len(rubrics)} per-item rubrics → {OUT_RUBRICS}\n"
        f"a range [{min(a_vals)}, {max(a_vals)}]  c={OPEN_RUBRIC_FLOOR}"
    )


if __name__ == "__main__":
    main()
