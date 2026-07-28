"""Bank expansion helper — only fills gaps; never clones when coverage is already good."""

from __future__ import annotations

import copy
import re
from collections import Counter

# Full IRT difficulty ladder used by the tuned engine.
DIFFICULTIES = ("very_easy", "easy", "medium", "hard", "very_hard")
CORE_DIFFICULTIES = ("easy", "medium", "hard")
DISCRIMINATIONS = ("low", "medium", "high")
TARGET_PER_COMPETENCY = 18
TARGET_PER_DIFFICULTY = 3


def _stem_for_difficulty(stem: str, difficulty: str) -> str:
    tag = {
        "very_easy": "[Intro]",
        "easy": "[Foundational]",
        "medium": "[Applied]",
        "hard": "[Advanced]",
        "very_hard": "[Expert]",
    }.get(difficulty, "")
    if stem.startswith("["):
        return re.sub(r"^\[[^\]]+\]\s*", f"{tag} ", stem)
    return f"{tag} {stem}"


def pool_is_adequate(items: list[dict]) -> bool:
    """True when cloning would not help — prefer curated banks like enriched_bank.json."""
    if len(items) < TARGET_PER_COMPETENCY:
        return False
    c = Counter(q.get("difficulty", "medium") for q in items)
    # Need core three at minimum; ideally the extended ladder too.
    if any(c.get(d, 0) < TARGET_PER_DIFFICULTY for d in CORE_DIFFICULTIES):
        return False
    # Prefer having at least one extreme on each side when possible.
    has_low = c.get("very_easy", 0) + c.get("easy", 0) >= TARGET_PER_DIFFICULTY
    has_high = c.get("very_hard", 0) + c.get("hard", 0) >= TARGET_PER_DIFFICULTY
    return has_low and has_high


def synthesize_competency_pool(
    items: list[dict],
    competency: str,
    enrich_fn,
    *,
    target_total: int = TARGET_PER_COMPETENCY,
    target_per_difficulty: int = TARGET_PER_DIFFICULTY,
) -> tuple[list[dict], list[str]]:
    pool: list[dict] = [dict(q) for q in items]
    logs: list[str] = []

    if pool_is_adequate(pool):
        logs.append(
            f"{competency}: adequate ({len(pool)} items) — skipping clone synthesis"
        )
        return pool, logs

    synth_counter = 0
    existing_ids = {q["id"] for q in pool}
    # Prefer filling the extended ladder when short.
    fill_targets = DIFFICULTIES

    def counts() -> Counter:
        return Counter(q.get("difficulty", "medium") for q in pool)

    def needs_more() -> bool:
        c = counts()
        if len(pool) < target_total:
            return True
        return any(c.get(d, 0) < target_per_difficulty for d in CORE_DIFFICULTIES)

    while needs_more():
        c = counts()
        deficit_diff = min(fill_targets, key=lambda d: c.get(d, 0))
        sources = [q for q in pool if not q.get("synthesized")] or pool
        source = sources[synth_counter % len(sources)]
        synth_counter += 1
        new_id = f"{source['id']}-syn{synth_counter:03d}"
        while new_id in existing_ids:
            synth_counter += 1
            new_id = f"{source['id']}-syn{synth_counter:03d}"

        disc = DISCRIMINATIONS[synth_counter % len(DISCRIMINATIONS)]
        variant = copy.deepcopy(source)
        variant.update(
            {
                "id": new_id,
                "competency": competency,
                "difficulty": deficit_diff,
                "discrimination": disc,
                "stem": _stem_for_difficulty(source["stem"], deficit_diff),
                "synthesized": True,
                "source_id": source["id"],
            }
        )
        enriched = enrich_fn(variant)
        pool.append(enriched)
        existing_ids.add(new_id)
        logs.append(
            f"{competency}: +{new_id} ({deficit_diff}, a={enriched['a']}, b={enriched['b']:.1f}) "
            f"from {source['id']}"
        )
        if synth_counter > 200:
            logs.append(f"{competency}: synthesis safety cap at {len(pool)} items")
            break

    return pool, logs


def synthesize_bank(
    by_comp: dict[str, list[dict]],
    enrich_fn,
) -> tuple[dict[str, list[dict]], list[str]]:
    expanded: dict[str, list[dict]] = {}
    all_logs: list[str] = []
    for comp, items in sorted(by_comp.items()):
        pool, logs = synthesize_competency_pool(items, comp, enrich_fn)
        expanded[comp] = pool
        all_logs.extend(logs)
    return expanded, all_logs
