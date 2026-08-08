#!/usr/bin/env python3
"""Import the two externally authored AI-engineer banks used for human testing.

The source files intentionally remain outside the repository.  This script makes the
checked-in, deployable copies and performs the small compatibility migration required by
the orchestrator:

* synthesize CAT parameters from semantically related strata in the prior banks;
* convert the AIE-v3 code payload from public_tests/hidden_tests to the executable shape;
* add explicit duration defaults where the source omitted them; and
* build coverage-only graphs from the taxonomy each bank can actually measure.

It never edits or replaces an existing bank.  The source hash and every modelling
assumption are recorded in the generated bank so the import is reproducible and cannot be
mistaken for empirical calibration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from statistics import median
from typing import Any

from app.schemas.orchestration import DEFAULT_SECONDS_BY_MODALITY, BankItem
from app.services.competency_graph import load_competency_graph
from app.services.orchestrator.calibration import theta_to_mastery_difficulty

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "app" / "data"
DEFAULT_SOURCE_DIR = Path(
    "/media/nagah/01DD17766DC74AA0/2026-summer/SPRINTS/Banks_2026-08-05"
)

IMPORTS = (
    (
        "question_bank_AIE_v3.json",
        "question_bank_AIE_JR_v3.json",
        "competency_graph_AIE_JR_v3.json",
        "AIE-JR-V3",
    ),
    (
        "junior_ai_engineer_question_bank_600.json",
        "question_bank_JAI_2026_600.json",
        "competency_graph_JAI_2026_600.json",
        "JAI-600",
    ),
)

DIFFICULTY_LABELS = frozenset({"easy", "medium", "hard"})

DONOR_BANKS = {
    "DA": DATA / "question_bank.json",
    "PY": DATA / "question_bank_PY_20260803_083616.json",
    "AIE": DATA / "question_bank_AIE.json",
}

# Main codes are not globally semantic across these authoring projects.  This explicit
# crosswalk is safer than assuming C5 always means the same thing.  DA supplies the data-
# engineering precedent, PY and AIE/C1 supply software engineering, AIE/C3 supplies ML,
# and AIE/C6 supplies LLM/RAG.  A pooled modality+difficulty fallback exists only so a
# sparse preferred stratum cannot make an import impossible.
SEMANTIC_DONORS: dict[str, dict[str, tuple[tuple[str, str], ...]]] = {
    "AIE-JR-V3": {
        "C1": (("AIE", "C1"), ("PY", "PY")),
    },
    "JAI-600": {
        "C1": (("AIE", "C1"), ("PY", "PY")),
        "C2": (("DA", "DA"), ("AIE", "C3")),
        "C3": (("AIE", "C3"),),
        "C4": (("AIE", "C3"),),
        "C5": (("AIE", "C6"),),
        "C6": (("AIE", "C1"), ("AIE", "C6")),
    },
}


@dataclass(frozen=True)
class Donor:
    bank_id: str
    item_id: str
    main: str
    modality: str
    difficulty: str
    cat: dict[str, float]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _difficulty(item: dict[str, Any]) -> str:
    payload = item.get(item["modality"]) or {}
    label = str(item.get("difficulty") or payload.get("difficulty") or "medium").lower()
    if label not in DIFFICULTY_LABELS:
        raise ValueError(f"{item.get('item_id')}: unknown difficulty label {label!r}")
    return label


def _modality_family(modality: str) -> str:
    return "voice" if modality in {"open", "voice"} else modality


def _main(item: BankItem | dict[str, Any]) -> str:
    measures = item.measures if isinstance(item, BankItem) else item["measures"]
    if isinstance(item, BankItem):
        primary = max(measures, key=lambda measure: measure.weight).variable
    else:
        primary = max(measures, key=lambda measure: float(measure["weight"]))["variable"]
    return str(primary).split(".")[0]


def _donor_difficulty(item: BankItem) -> str | None:
    raw = item.payload.get("authored_difficulty_label", item.payload.get("difficulty"))
    if isinstance(raw, str):
        label = raw.lower()
        return label if label in DIFFICULTY_LABELS else None
    # DA predates authored labels. Its existing b is the only defensible way to place an
    # item into the three broad source strata; very-easy/hard donors from five-level banks
    # are deliberately excluded rather than collapsed into a more extreme three-level bin.
    if item.cat.b <= -0.65:
        return "easy"
    if item.cat.b >= 0.65:
        return "hard"
    return "medium"


@cache
def _donors() -> tuple[Donor, ...]:
    donors: list[Donor] = []
    for bank_id, path in DONOR_BANKS.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw["items"] if isinstance(raw, dict) else raw
        for entry in entries:
            item = BankItem.model_validate(entry)
            label = _donor_difficulty(item)
            if item.status != "active" or label is None:
                continue
            donors.append(
                Donor(
                    bank_id=bank_id,
                    item_id=item.item_id,
                    main=_main(item),
                    modality=_modality_family(item.modality),
                    difficulty=label,
                    cat=item.cat.model_dump(),
                )
            )
    return tuple(sorted(donors, key=lambda donor: (donor.bank_id, donor.item_id)))


def _donor_pool(
    deployment_id: str, main: str, modality: str, difficulty: str
) -> tuple[Donor, ...]:
    preferred = set(SEMANTIC_DONORS[deployment_id][main])
    pool = tuple(
        donor
        for donor in _donors()
        if (donor.bank_id, donor.main) in preferred
        and donor.modality == modality
        and donor.difficulty == difficulty
    )
    if pool:
        return pool
    fallback = tuple(
        donor
        for donor in _donors()
        if donor.modality == modality and donor.difficulty == difficulty
    )
    if not fallback:
        raise ValueError(
            f"no CAT donors for {deployment_id}/{main}/{modality}/{difficulty}"
        )
    return fallback


def _synthesize_cat(
    items: list[dict[str, Any]], deployment_id: str, labels: dict[str, str]
) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = (_main(item), _modality_family(item["modality"]), labels[item["item_id"]])
        groups[key].append(item)

    for (main, modality, difficulty), group in sorted(groups.items()):
        pool = _donor_pool(deployment_id, main, modality, difficulty)
        seed = f"{deployment_id}:{main}:{modality}:{difficulty}".encode()
        offset = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % len(pool)
        for index, item in enumerate(sorted(group, key=lambda row: row["item_id"])):
            donor = pool[(offset + index) % len(pool)]
            item["cat"] = dict(donor.cat)
            payload = item[item["modality"]]
            payload["difficulty"] = theta_to_mastery_difficulty(donor.cat["b"])
            payload["discrimination"] = donor.cat["a"]
            item["cat_synthesis"] = {
                "method": "semantic_modality_difficulty_donor_v1",
                "donor_bank_id": donor.bank_id,
                "donor_item_id": donor.item_id,
                "donor_main": donor.main,
                "donor_modality": donor.modality,
                "donor_difficulty": donor.difficulty,
            }


def _parameter_summary(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, float]]] = defaultdict(list)
    for item in items:
        label = item[item["modality"]]["authored_difficulty_label"]
        groups[f"{_modality_family(item['modality'])}/{label}"].append(item["cat"])
    return {
        key: {
            "items": len(values),
            "a_median": round(median(value["a"] for value in values), 4),
            "b_median": round(median(value["b"] for value in values), 4),
            "c_median": round(median(value["c"] for value in values), 4),
            "a_range": [min(value["a"] for value in values), max(value["a"] for value in values)],
            "b_range": [min(value["b"] for value in values), max(value["b"] for value in values)],
        }
        for key, values in sorted(groups.items())
    }


def _function_name(signature: str, item_id: str) -> str:
    match = re.search(r"\bdef\s+([A-Za-z_]\w*)\s*\(", signature)
    if not match:
        raise ValueError(f"{item_id}: cannot derive function name from {signature!r}")
    return match.group(1)


def _normalize_aie_v3_code(item: dict[str, Any]) -> None:
    payload = item["code"]
    signature = str(payload.pop("function_signature"))
    payload["function_name"] = _function_name(signature, item["item_id"])
    payload.setdefault("starter_code", f"{signature}\n    ...\n")

    tests = []
    for visibility, key in (("public", "public_tests"), ("hidden", "hidden_tests")):
        for authored in payload.pop(key, []):
            test = dict(authored)
            test["visibility"] = visibility
            test.setdefault("weight", 1.0)
            tests.append(test)
    if not tests:
        raise ValueError(f"{item['item_id']}: code item has no executable tests")
    payload["tests"] = tests

    # The code grader has four canonical scoring axes.  Preserve the richer source rubric
    # verbatim for audit, while presenting the executable grader with its supported axes.
    payload["source_rubric_criteria"] = payload["rubric_criteria"]
    payload["rubric_criteria"] = [
        {"criterion_id": "functional_correctness", "maximum_score": 8},
        {"criterion_id": "edge_case_handling", "maximum_score": 5},
        {"criterion_id": "algorithm_choice", "maximum_score": 4},
        {"criterion_id": "code_quality", "maximum_score": 3},
    ]


def _normalize_aie_v3_voice(item: dict[str, Any]) -> None:
    payload = item["voice"]
    source = payload.get("evaluation_criteria") or []
    if not source:
        raise ValueError(f"{item['item_id']}: voice item has no evaluation criteria")
    payload["rubric_criteria"] = [
        {
            "criterion_id": criterion["criterion_id"],
            "maximum_score": max(float(criterion.get("weight", 0.0)) * 20.0, 1.0),
            "weight": float(criterion.get("weight", 1.0 / len(source))),
            "descriptor": criterion.get("criterion", ""),
        }
        for criterion in source
    ]


def normalize_bank(source: Path, deployment_id: str) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise TypeError(f"{source}: expected an object containing an items list")
    bank = copy.deepcopy(raw)

    seen: set[str] = set()
    labels: dict[str, str] = {}
    for item in bank["items"]:
        item_id = str(item.get("item_id") or "")
        if not item_id or item_id in seen:
            raise ValueError(f"{source.name}: missing or duplicate item_id {item_id!r}")
        seen.add(item_id)
        item.setdefault("status", "active")
        label = _difficulty(item)
        labels[item_id] = label
        payload = item[item["modality"]]
        payload["authored_difficulty_label"] = label
        item.setdefault(
            "estimated_time_seconds", DEFAULT_SECONDS_BY_MODALITY[item["modality"]]
        )
        if source.name == "question_bank_AIE_v3.json" and item["modality"] == "code":
            _normalize_aie_v3_code(item)
        if source.name == "question_bank_AIE_v3.json" and item["modality"] == "voice":
            _normalize_aie_v3_voice(item)
        if item["modality"] == "code" and not any(
            test.get("visibility") == "hidden" for test in item["code"].get("tests", [])
        ):
            # A public-only suite is the answer key: the candidate can tune against every
            # assertion before submitting. Retain the authored item for review, but never
            # put it into an adaptive candidate pool until independent hidden tests exist.
            item["status"] = "inactive_missing_hidden_tests"

    _synthesize_cat(bank["items"], deployment_id, labels)
    for item in bank["items"]:
        BankItem.model_validate(item)

    bank["deployment_bank_id"] = deployment_id
    bank["import_metadata"] = {
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "item_count": len(bank["items"]),
        "calibration_status": (
            "SYNTHESIZED_FROM_PRIOR_BANK_PARAMETERS_NOT_ITEM_CALIBRATED"
        ),
        "cat_synthesis": {
            "method": "semantic_modality_difficulty_donor_v1",
            "assignment": (
                "deterministic round-robin over prior-bank items within the preferred "
                "semantic-main, modality, and authored-difficulty stratum"
            ),
            "semantic_donor_crosswalk": {
                main: [f"{bank_id}/{donor_main}" for bank_id, donor_main in donors]
                for main, donors in SEMANTIC_DONORS[deployment_id].items()
            },
            "donor_sources": {
                bank_id: {
                    "file": path.name,
                    "sha256": _sha256(path),
                }
                for bank_id, path in DONOR_BANKS.items()
            },
            "parameter_summary": _parameter_summary(bank["items"]),
        },
        "quarantined_items": {
            "missing_hidden_code_tests": sum(
                item.get("status") == "inactive_missing_hidden_tests"
                for item in bank["items"]
            )
        },
        "notes": [
            "CAT triples are synthesized from prior-bank donors, not calibrated for the new item wording.",
            "Every item records the exact donor bank and donor item used.",
            "Missing item durations use the orchestrator's documented modality default.",
            "The paired graph is coverage-only and contains no prerequisite edges.",
            "Code items without hidden tests are retained but inactive for human testing.",
        ],
    }
    return bank


def _scope_titles(bank: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    main_titles: dict[str, str] = {}
    sub_titles: dict[str, str] = {}
    for competency in (bank.get("scope") or {}).get("competencies", []):
        main = str(competency["competency_id"])
        main_titles[main] = str(competency.get("title") or main)
        for sub in competency.get("sub_competencies", []):
            code = str(sub["sub_competency_id"])
            sub_titles[code] = str(sub.get("title") or code)
    return main_titles, sub_titles


def coverage_graph(bank: dict[str, Any], deployment_id: str) -> dict[str, Any]:
    measured = sorted(
        {measure["variable"] for item in bank["items"] for measure in item["measures"]}
    )
    mains = sorted({variable.split(".")[0] for variable in measured})
    main_titles, sub_titles = _scope_titles(bank)

    if deployment_id == "AIE-JR-V3":
        # This source references the full AIE graph but contains only its C1.1-C1.3 slice.
        # Reuse those authoritative titles without importing unmeasurable coverage nodes.
        full = load_competency_graph(DATA / "competency_graph_AIE.json")
        for code in [*mains, *measured]:
            if code in full.nodes:
                title = full.nodes[code].title
                (main_titles if code in mains else sub_titles)[code] = title

    nodes = [
        {
            "competency_id": main,
            "title": main_titles.get(main, main),
            "node_type": "main",
            "critical": True,
            "context_specific": False,
            "main_competencies": [],
        }
        for main in mains
    ]
    for variable in measured:
        main = variable.split(".")[0]
        # Source item labels include the code prefix in the 600-bank; strip it for a
        # cleaner graph title if the scope block was absent.
        fallback = Counter(
            str(item.get("sub_competency") or variable)
            for item in bank["items"]
            if any(m["variable"] == variable for m in item["measures"])
        ).most_common(1)[0][0]
        nodes.append(
            {
                "competency_id": variable,
                "title": sub_titles.get(variable, fallback),
                "node_type": "sub_competency",
                "critical": True,
                "context_specific": False,
                "main_competencies": [main],
                "shared": False,
            }
        )

    edges = []
    for main in mains:
        children = [variable for variable in measured if variable.split(".")[0] == main]
        for child in children:
            edges.append(
                {
                    "from": child,
                    "to": main,
                    "relation": "CONTRIBUTES_TO",
                    "weight": round(1.0 / len(children), 6),
                }
            )
    return {
        "version": "1.0",
        "bank_id": deployment_id,
        "notes": (
            "Coverage-only projection of the imported bank's measurable taxonomy. "
            "It deliberately has no prerequisite edges and cannot create synthetic evidence."
        ),
        "policy": {
            "upward_inference": False,
            "descendant_blocking": False,
            "accepted_validation_statuses": ["validated"],
            "minimum_failures_to_block": 2,
        },
        "nodes": nodes,
        "edges": edges,
    }


def _render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--check", action="store_true", help="verify checked-in outputs without writing"
    )
    args = parser.parse_args()

    mismatches: list[str] = []
    for source_name, bank_name, graph_name, deployment_id in IMPORTS:
        source = args.source_dir / source_name
        bank = normalize_bank(source, deployment_id)
        graph = coverage_graph(bank, deployment_id)
        outputs = ((DATA / bank_name, _render(bank)), (DATA / graph_name, _render(graph)))
        for path, rendered in outputs:
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                    mismatches.append(str(path.relative_to(BACKEND)))
            else:
                path.write_text(rendered, encoding="utf-8")
        counts = Counter(item["modality"] for item in bank["items"])
        print(f"{deployment_id}: {len(bank['items'])} items {dict(sorted(counts.items()))}")

    if mismatches:
        print("generated outputs differ: " + ", ".join(mismatches))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
