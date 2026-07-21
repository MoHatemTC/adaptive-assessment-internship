"""Fuse the MCQ and code banks into one multi-modality bank.

Run from `backend/`:

    PYTHONPATH=. python scripts/build_unified_bank.py

Idempotent and lossless: every field either lands in the envelope or is preserved verbatim
in the modality payload, and `--check` re-derives the output to confirm nothing drifted.

THE ONE THING THAT IS NOT A COPY

MCQ items already carry calibrated a/b/c on theta and pass through untouched. Code
questions carry an authored difficulty on the mastery scale, which
`orchestrator.calibration` maps onto theta. That mapping is provisional — see that module —
and it is the only transformation in this script.

Both source banks already use the same `competency` and `sub_competency` strings, so the
taxonomy needs no migration. The variable a code question measures is its own
`competency_id`; for an MCQ item it is the code prefix of `sub_competency`
("T1.1 · Core Python…" -> "T1.1"), which is what makes the two banks join.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.schemas.orchestration import BankItem
from app.services.orchestrator.calibration import code_cat_parameters

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
MCQ_BANK = DATA / "item_bank.json"
CODE_BANK = DATA / "code_bank.json"
UNIFIED = DATA / "question_bank.json"


def variable_from_sub_competency(sub_competency: str) -> str:
    """"T1.1 · Core Python & data structures" -> "T1.1".

    The join key between the two banks. Falls back to the whole string when there is no
    separator, so an unprefixed taxonomy still produces a usable — if uglier — variable
    rather than an empty one.
    """
    head = sub_competency.split("·")[0].strip()
    return head or sub_competency.strip()


def convert_mcq(raw: dict) -> dict:
    """MCQ item -> unified envelope. a/b/c pass through: they are already calibrated."""
    variable = variable_from_sub_competency(raw.get("sub_competency", ""))
    payload = {
        k: v for k, v in raw.items()
        if k not in {"id", "competency", "sub_competency", "a", "b", "c"}
    }
    return {
        "item_id": raw["id"],
        "modality": "mcq",
        "status": raw.get("status", "active"),
        "competency": raw.get("competency", ""),
        "sub_competency": raw.get("sub_competency", ""),
        "measures": [{"variable": variable, "weight": 1.0}],
        "cat": {"a": raw["a"], "b": raw["b"], "c": raw["c"]},
        "mcq": payload,
    }


def convert_code(raw: dict) -> dict:
    """Code question -> unified envelope, with difficulty mapped onto theta.

    `measures` comes from the question's own competency weights, so one submission
    correctly evidences every competency the question loads on — which is the main
    structural difference from an MCQ item and the reason `measures` is a list.
    """
    measures = [
        {"variable": c["competency_id"], "weight": float(c["weight"])}
        for c in raw.get("competencies", [])
        if float(c.get("weight", 0)) > 0
    ]
    payload = {
        k: v for k, v in raw.items()
        if k not in {"question_id", "competency", "sub_competency", "competencies",
                     "difficulty", "discrimination", "status"}
    }
    return {
        "item_id": raw["question_id"],
        "modality": "code",
        "status": raw.get("status", "active"),
        "competency": raw.get("competency", ""),
        "sub_competency": raw.get("sub_competency", ""),
        "measures": measures,
        "cat": code_cat_parameters(raw["difficulty"], raw.get("discrimination", 1.0)),
        "code": payload,
    }


def _entries(path: Path, key: str) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw[key] if isinstance(raw, dict) else raw


def build() -> tuple[list[dict], list[str]]:
    """Return (items, problems). Problems are reported, never raised silently."""
    items: list[dict] = []
    problems: list[str] = []

    for raw in _entries(MCQ_BANK, "items"):
        try:
            items.append(convert_mcq(raw))
        except (KeyError, ValueError) as exc:
            problems.append(f"mcq {raw.get('id', '?')}: {exc}")

    for raw in _entries(CODE_BANK, "questions"):
        try:
            converted = convert_code(raw)
            if not converted["measures"]:
                problems.append(f"code {raw.get('question_id', '?')}: measures no variable")
                continue
            items.append(converted)
        except (KeyError, ValueError) as exc:
            problems.append(f"code {raw.get('question_id', '?')}: {exc}")

    # Validate through the schema here rather than at load: a bank error found while a
    # candidate waits for a question is a bank error found far too late.
    for item in items:
        try:
            BankItem.model_validate(item)
        except Exception as exc:  # noqa: BLE001 — every validation failure is reportable
            problems.append(f"{item.get('item_id', '?')}: {exc}")

    return items, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed bank matches what this script produces")
    args = parser.parse_args()

    items, problems = build()
    for problem in problems:
        print(f"  !! {problem}", file=sys.stderr)

    by_modality: dict[str, int] = {}
    for item in items:
        by_modality[item["modality"]] = by_modality.get(item["modality"], 0) + 1
    variables = sorted({m["variable"] for i in items for m in i["measures"]})

    payload = {"schema_version": 2, "items": items}

    if args.check:
        if not UNIFIED.exists():
            print("no unified bank to check against", file=sys.stderr)
            return 1
        current = json.loads(UNIFIED.read_text(encoding="utf-8"))
        if current != payload:
            print("committed bank differs from a fresh build", file=sys.stderr)
            return 1
        print(f"unified bank is current: {len(items)} items {by_modality}")
        return 0

    UNIFIED.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {UNIFIED.relative_to(Path.cwd()) if UNIFIED.is_relative_to(Path.cwd()) else UNIFIED}")
    print(f"  {len(items)} items {by_modality}")
    print(f"  {len(variables)} variables: {', '.join(variables[:12])}"
          + (" ..." if len(variables) > 12 else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
