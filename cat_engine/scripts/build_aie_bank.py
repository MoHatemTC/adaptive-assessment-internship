"""Normalise the authored AI Engineer bank into the unified schema.

Run from `backend/`:

    PYTHONPATH=. python scripts/build_aie_bank.py --source /path/to/ai_engineer_exam_bank.json
    PYTHONPATH=. python scripts/build_aie_bank.py --check

The source file is already `schema_version: 2` and structurally conformant, so most of it
is copied verbatim. Three things are not, and each is a correctness fix rather than a
formatting preference.

1. THE C6 TAXONOMY COLLISION — the one that matters.

   The 25 C6 code items were authored against a different numbering than the 100 C6 mcq
   and voice items. `C6.8` is "Prompt versioning & evaluation" for mcq/voice and "Decoding
   & sampling" for code; 24 of the 25 disagree this way. Nothing crashes — `loading()` and
   the coverage gate key on the variable ID — so a candidate's decoding answer is silently
   recorded as evidence about prompt versioning, and the report says something false.

   `C6_CODE_REMAP` fixes it by LABEL, not by index, and lives here in the diff rather than
   in the data so the judgement stays reviewable. It was derived by matching each code
   label against the mcq/voice taxonomy and CONFIRMED by the bank owner on 2026-08-03.
   `--strict` fails on any C6 code label absent from the table, so a new item cannot slip
   through unmapped.

2. LABEL DRIFT in C1 and C3 — cosmetic by comparison. The same concept is worded
   differently across modalities ("Persistence/auth" vs "Persistence & access control").
   The IDs agree, so nothing is mis-measured; `CANONICAL_SUBS` just makes one variable
   read as one thing.

3. `estimated_time_seconds` is hoisted from the modality payload to the envelope, because
   selection divides information by it and the engine may not open a payload.

Lossless: the authored `sub_competency` string is preserved as `source_sub_competency`
inside the payload, so nothing this script rewrites is destroyed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from cat_engine.engine.schemas.orchestration import BankItem

DATA = Path(__file__).resolve().parent.parent / "engine" / "data"
TARGET = DATA / "question_bank_AIE.json"

BANK_TITLE = "AI Engineer"

# The authoritative taxonomy: the labels the 250 mcq and voice items agree on.
CANONICAL_SUBS: dict[str, str] = {
    "C1.1": "Core Python & programming fundamentals",
    "C1.2": "Data structures & algorithms",
    "C1.3": "Code quality & engineering workflow",
    "C1.4": "Backend services & APIs",
    "C1.5": "Persistence & access control",
    "C1.6": "AI application integration",
    "C3.1": "Probability, linear algebra & optimisation",
    "C3.2": "Classical ML algorithms & ensembles",
    "C3.3": "Feature engineering & training data",
    "C3.4": "Baselines & algorithm selection",
    "C3.5": "Training, tuning & inference efficiency",
    "C3.6": "Bias, variance & regularisation",
    "C3.7": "Overfitting & generalisation",
    "C3.8": "Dataset splitting & cross-validation",
    "C3.9": "Metric selection & model comparison",
    "C3.10": "Error analysis & model calibration",
    "C3.11": "Robustness, fairness & interpretation",
    "C6.1": "LLM internals, embeddings & sampling",
    "C6.2": "Model families & capabilities",
    "C6.3": "Model sourcing & serving",
    "C6.4": "Model selection, routing & cost",
    "C6.5": "Instruction & few-shot prompting",
    "C6.6": "Structured output, chaining & tools",
    "C6.7": "Context assembly & token budgeting",
    "C6.8": "Prompt versioning & evaluation",
    "C6.9": "Trusted context & injection defence",
    "C6.10": "RAG design & document processing",
    "C6.11": "Vector stores & hybrid search",
    "C6.12": "Metadata & permission-aware filtering",
    "C6.13": "Query rewriting & reranking",
    "C6.14": "Grounding & citations",
    "C6.15": "Retrieval evaluation",
    "C6.16": "Multi-hop & graph RAG",
}

# C6 code label -> the variable that label actually belongs to in CANONICAL_SUBS.
# Confirmed by the bank owner 2026-08-03. 23 of the 25 C6 code items
# move; the two already labelled "Vector stores & hybrid search" were correct.
C6_CODE_REMAP: dict[str, str] = {
    "Prompt engineering & templating": "C6.5",
    "Context assembly & token budgeting": "C6.7",
    "Chunking & document splitting": "C6.10",
    "Embeddings & similarity": "C6.1",
    "Query transformation & multi-query retrieval": "C6.13",
    "Hybrid search & rank fusion": "C6.11",
    "Reranking, MMR & deduplication": "C6.13",
    "Decoding & sampling": "C6.1",
    "Tool / function calling": "C6.6",
    "Retrieval evaluation metrics": "C6.15",
    "Vector stores & hybrid search": "C6.11",
    "End-to-end retrieval pipeline": "C6.10",
    "Groundedness & faithfulness": "C6.14",
    "Citations & attribution": "C6.14",
    "Access control & metadata filtering": "C6.12",
}

# Items that genuinely evidence a competency in ANOTHER main, and how much.
#
# THE POINT OF THIS TABLE. Every item as authored measured exactly one sub-competency at
# weight 1.0, and every sub-competency belonged to exactly one main — so no response could
# ever update more than one main competency, and the shared-evidence half of the design
# (specification section 22) had nothing to act on. A candidate answering "why must LLM
# JSON output be validated" was credited under application engineering and not at all
# under the LLM track, which is the same question with a different label on it.
#
# The secondary weight is what the item is PARTLY about, and it is deliberately not
# normalised against the primary: `MeasuredVariable.weight` means "how much of this item
# is about this variable", and two such statements about one item are independent, not a
# partition. 0.5 for a question that squarely sits in both, 0.35 for a real but narrower
# overlap.
SHARED_LOADINGS: dict[str, tuple[str, float]] = {
    # Structured output / tool calling: an application concern AND the LLM track's.
    "C1-Q044": ("C6.6", 0.5),
    "C1-Q046": ("C6.6", 0.5),
    "C1-Q050": ("C6.6", 0.5),
    "C1-Q049": ("C6.6", 0.5),
    "code_c1_023": ("C6.6", 0.5),
    "voice_c1_022": ("C6.6", 0.5),
    # Context assembly and token budgeting.
    "C1-Q048": ("C6.7", 0.5),
    "code_c1_024": ("C6.7", 0.5),
    "code_c1_025": ("C6.7", 0.5),
    # Sampling parameters are LLM internals.
    "C1-Q043": ("C6.1", 0.35),
    # Serving a model is a backend-services problem.
    "C6-Q009": ("C1.4", 0.5),
    "voice_c6_004": ("C1.4", 0.35),
    "C1-Q033": ("C6.3", 0.35),
    "voice_c1_014": ("C6.3", 0.35),
    # Choosing a retrieval metric IS metric selection; evaluating RAG IS error analysis.
    "C6-Q045": ("C3.9", 0.5),
    "voice_c6_022": ("C3.9", 0.5),
    "code_c6_025": ("C3.9", 0.35),
    "voice_c6_023": ("C3.10", 0.5),
    "voice_c3_023": ("C6.15", 0.35),
}

ENVELOPE_KEYS = {
    "item_id",
    "modality",
    "status",
    "competency",
    "competency_id",
    "sub_competency",
    "measures",
    "cat",
}


def label_of(sub_competency: str) -> str:
    """"C6.8 · Decoding & sampling" -> "Decoding & sampling"."""
    _, _, tail = sub_competency.partition("·")
    return (tail or sub_competency).strip()


def convert(raw: dict) -> tuple[dict, list[str]]:
    """One authored item -> one unified envelope. Returns (item, problems)."""
    problems: list[str] = []
    item_id = str(raw["item_id"])
    modality = str(raw["modality"])
    authored_sub = str(raw.get("sub_competency", ""))

    measures = [
        {"variable": str(m["variable"]), "weight": float(m.get("weight", 1.0))}
        for m in raw.get("measures", [])
    ]
    if not measures:
        problems.append(f"{item_id}: measures no variable")
        return {}, problems

    # C6 code items: re-point at the variable their label actually describes.
    if str(raw.get("competency_id")) == "C6" and modality == "code":
        label = label_of(authored_sub)
        remapped = C6_CODE_REMAP.get(label)
        if remapped is None:
            problems.append(
                f"{item_id}: C6 code label {label!r} is not in C6_CODE_REMAP — "
                "add it (with a reviewer) before this item can be trusted"
            )
        elif remapped != measures[0]["variable"]:
            problems.append(
                f"{item_id}: remapped {measures[0]['variable']} -> {remapped} "
                f"({label!r})"
            )
            measures[0]["variable"] = remapped

    shared = SHARED_LOADINGS.get(item_id)
    if shared is not None:
        secondary, weight = shared
        if secondary not in CANONICAL_SUBS:
            problems.append(f"{item_id}: shared loading on unknown variable {secondary!r}")
        elif any(m["variable"] == secondary for m in measures):
            problems.append(f"{item_id}: already measures {secondary!r}")
        else:
            measures.append({"variable": secondary, "weight": weight})

    variable = measures[0]["variable"]
    canonical = CANONICAL_SUBS.get(variable)
    if canonical is None:
        problems.append(f"{item_id}: variable {variable!r} is not in CANONICAL_SUBS")
        canonical = label_of(authored_sub)

    payload = deepcopy(raw.get(modality) or {})
    if not payload:
        problems.append(f"{item_id}: modality {modality!r} has no payload")
        return {}, problems

    # Lossless: what the author wrote survives even where the envelope is rewritten.
    if label_of(authored_sub) != canonical:
        payload["source_sub_competency"] = authored_sub

    item = {
        "item_id": item_id,
        "modality": modality,
        "status": str(raw.get("status", "active")),
        "competency": str(raw.get("competency", "")),
        "sub_competency": f"{variable} · {canonical}",
        "measures": measures,
        "cat": {
            "a": float(raw["cat"]["a"]),
            "b": float(raw["cat"]["b"]),
            "c": float(raw["cat"].get("c", 0.0)),
        },
        modality: payload,
    }

    authored_seconds = payload.get("estimated_time_seconds")
    if authored_seconds is not None:
        item["estimated_time_seconds"] = float(authored_seconds)

    unexpected = set(raw) - ENVELOPE_KEYS - {modality}
    if unexpected:
        problems.append(f"{item_id}: unexpected top-level keys {sorted(unexpected)}")

    return item, problems


def build(source: Path) -> tuple[list[dict], list[str]]:
    """Return (items, problems). Problems are reported, never silently swallowed."""
    raw = json.loads(source.read_text(encoding="utf-8"))
    entries = raw["items"] if isinstance(raw, dict) else raw

    items: list[dict] = []
    problems: list[str] = []
    for entry in entries:
        try:
            item, item_problems = convert(entry)
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{entry.get('item_id', '?')}: {exc}")
            continue
        problems.extend(item_problems)
        if item:
            items.append(item)

    seen: set[str] = set()
    for item in items:
        if item["item_id"] in seen:
            problems.append(f"{item['item_id']}: duplicate item_id")
        seen.add(item["item_id"])

    # Validate here rather than at load: a bank error found while a candidate waits for a
    # question is a bank error found far too late.
    for item in items:
        try:
            BankItem.model_validate(item)
        except Exception as exc:  # noqa: BLE001 — every validation failure is reportable
            problems.append(f"{item['item_id']}: {exc}")

    return items, problems


def summarise(items: list[dict]) -> str:
    by_modality = Counter(i["modality"] for i in items)
    variables = sorted({m["variable"] for i in items for m in i["measures"]})
    mains = sorted({v.split(".")[0] for v in variables})
    cross = sum(
        1
        for i in items
        if len({m["variable"].split(".")[0] for m in i["measures"]}) > 1
    )
    return (
        f"{len(items)} items {dict(sorted(by_modality.items()))}\n"
        f"  {len(mains)} mains {mains}, {len(variables)} variables\n"
        f"  {cross} items evidence more than one main competency"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the unified AI Engineer bank.")
    parser.add_argument(
        "--source",
        type=Path,
        help="authored bank JSON; required unless --check",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed bank matches a fresh build from --source",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on any reported problem, including an unmapped C6 label",
    )
    args = parser.parse_args()

    if args.check and args.source is None:
        # --check without a source still verifies the committed file parses and validates.
        if not TARGET.exists():
            print(f"no bank at {TARGET}", file=sys.stderr)
            return 1
        current = json.loads(TARGET.read_text(encoding="utf-8"))
        failures = []
        for entry in current["items"]:
            try:
                BankItem.model_validate(entry)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{entry.get('item_id', '?')}: {exc}")
        for failure in failures:
            print(f"  !! {failure}", file=sys.stderr)
        print(f"committed bank validates: {summarise(current['items'])}")
        return 1 if failures else 0

    if args.source is None:
        parser.error("--source is required unless --check is used alone")

    items, problems = build(args.source)
    remaps = [p for p in problems if "remapped" in p]
    other = [p for p in problems if "remapped" not in p]
    for problem in other:
        print(f"  !! {problem}", file=sys.stderr)
    if remaps:
        print(f"  C6 remap applied to {len(remaps)} code items:", file=sys.stderr)
        for problem in remaps:
            print(f"     {problem}", file=sys.stderr)

    payload = {"schema_version": 2, "competency": BANK_TITLE, "items": items}

    if args.check:
        if not TARGET.exists():
            print(f"no bank at {TARGET} to check against", file=sys.stderr)
            return 1
        if json.loads(TARGET.read_text(encoding="utf-8")) != payload:
            print("committed bank differs from a fresh build", file=sys.stderr)
            return 1
        print(f"AI Engineer bank is current: {summarise(items)}")
        return 0

    TARGET.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {TARGET}")
    print(f"  {summarise(items)}")
    return 1 if (other or (args.strict and problems)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
