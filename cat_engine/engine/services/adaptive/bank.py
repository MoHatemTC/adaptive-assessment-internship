"""Where items come from, and whether the bank can support a test at all.

`ItemRepository` is the seam. The engine never reads a file or a database directly, so
the parent project can back it with Supabase without touching any selection code, and the
bundled JSON implementation keeps the module runnable and verifiable with no credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from cat_engine.engine.config.settings import settings
from cat_engine.engine.config.paths import DATA_DIR
from cat_engine.engine.schemas.adaptive import Item
from cat_engine.engine.services.adaptive.irt import (
    DIFFICULTY_TO_B,
    DISCRIMINATION_TO_A,
    fisher_information,
)

DEFAULT_BANK_PATH = DATA_DIR / "item_bank.json"


class ItemRepository(Protocol):
    """Source of calibrated items, one competency at a time.

    Deliberately narrow. Selection needs the whole pool for a competency in order to rank
    it, and nothing else — no search, no pagination, no ordering. A wider interface would
    invite selection logic to leak into the data layer, where it cannot be tested against
    the psychometrics.
    """

    async def items_for_competency(self, competency: str) -> list[Item]:
        """Every calibrated item for one competency, in any order."""
        ...

    async def competencies(self) -> list[str]:
        """Competency names available in the bank."""
        ...


def coerce_item(raw: dict) -> Item:
    """Build an `Item` from a bank record, filling IRT parameters from labels if absent.

    Numeric `a`/`b`/`c` always win over the difficulty and discrimination words. A bank
    that has been properly calibrated carries numbers, and silently overwriting them with
    the label midpoint would discard the calibration — which is the one thing that makes
    the scores mean anything.

    `c` falls back to 1/n_options, the theoretical chance floor. That is an assumption,
    not a measurement: real calibration usually lands below it, because attractive
    distractors pull weak candidates under chance. It is used only when the bank gives
    nothing better.
    """
    data = dict(raw)

    if not isinstance(data.get("b"), (int, float)):
        data["b"] = DIFFICULTY_TO_B.get(data.get("difficulty", "medium"), 0.0)
    if not isinstance(data.get("a"), (int, float)):
        data["a"] = DISCRIMINATION_TO_A.get(data.get("discrimination", "medium"), 1.0)
    if not isinstance(data.get("c"), (int, float)):
        n_options = len(data.get("options") or []) or 4
        data["c"] = 1.0 / n_options

    return Item.model_validate(data)


class JsonItemRepository:
    """Reads a calibrated bank from a JSON array on disk.

    The default implementation, and the one the tests run against: it makes the engine
    verifiable end to end without a database or a network.
    """

    def __init__(self, path: Path | str = DEFAULT_BANK_PATH) -> None:
        self._path = Path(path)
        self._by_competency: dict[str, list[Item]] | None = None

    def _load(self) -> dict[str, list[Item]]:
        if self._by_competency is None:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            grouped: dict[str, list[Item]] = {}
            for record in raw:
                item = coerce_item(record)
                grouped.setdefault(item.competency, []).append(item)
            self._by_competency = grouped
        return self._by_competency

    async def items_for_competency(self, competency: str) -> list[Item]:
        return list(self._load().get(competency, []))

    async def competencies(self) -> list[str]:
        return sorted(self._load().keys())


def information_profile(items: list[Item], theta: float) -> list[float]:
    """Information each item carries at one ability, best first."""
    return sorted(
        (fisher_information(theta, i.a, i.b, i.c) for i in items), reverse=True
    )


def questions_needed(items: list[Item], theta: float, prior_sd: float) -> int | None:
    """How many items a candidate at `theta` needs to reach the SE target, at best.

    Greedy: assumes every administered item is the most informative one remaining, which
    no real test achieves. Returns None when the whole pool cannot get there.

    This is the check that decides whether a bank is usable, and it is not the same as
    "is there a sharp item at every ability". An item is consumed once, so a pool holding
    a single sharp item passes any peak-information check and still cannot carry a test:
    information adds, and precision comes from the *sum* over the items actually
    administered. A bank that fails this cannot reach the precision target however good
    the selection algorithm is, and every session will end on the question budget.
    """
    required_precision = 1.0 / (settings.cat_se_target**2)
    precision = 1.0 / (prior_sd**2)
    for count, info in enumerate(information_profile(items, theta), start=1):
        if precision >= required_precision:
            return count - 1
        precision += info
    return None if precision < required_precision else len(items)
