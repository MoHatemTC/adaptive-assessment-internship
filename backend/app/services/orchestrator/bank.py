"""The multi-modality question bank, behind a seam.

One file holds MCQ, code and (later) open-ended items. The engine reads only the envelope —
which variables an item measures and its CAT parameters on theta — so adding a modality is
a bank change and a grader change, never a selection change.

`UnifiedBankRepository` is the seam. The JSON implementation ships so the orchestrator runs
standalone; a database implementation replaces it without touching anything downstream.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.schemas.orchestration import BankItem
from app.services.adaptive.irt import THETA_GRID, fisher_information

logger = logging.getLogger(__name__)

BANK_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "question_bank.json"


class UnifiedBankRepository(Protocol):
    def all_items(self) -> list[BankItem]: ...
    def get(self, item_id: str) -> BankItem | None: ...
    def shortlist(self, variable: str, exclude: set[str]) -> list[BankItem]: ...
    def variables(self) -> list[str]: ...


class JsonUnifiedBank:
    """Reads the bundled unified bank. Parsed and validated once per process."""

    def __init__(self, path: Path | str = BANK_PATH) -> None:
        self._path = Path(path)

    @lru_cache(maxsize=1)  # noqa: B019 — one repository per path, bounded by construction
    def _load(self) -> tuple[BankItem, ...]:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        entries = raw["items"] if isinstance(raw, dict) else raw

        items: list[BankItem] = []
        rejected: list[str] = []
        for entry in entries:
            try:
                items.append(BankItem.model_validate(entry))
            except ValidationError as exc:
                identifier = entry.get("item_id", "<no id>") if isinstance(entry, dict) else "?"
                rejected.append(identifier)
                logger.error("bank item %s rejected: %s", identifier, exc)

        if rejected:
            # Named and skipped rather than raised: one malformed item should not deny
            # every candidate an assessment, but it must not pass silently either.
            logger.error("%d of %d bank items rejected: %s", len(rejected), len(entries), rejected)
        if not items:
            raise ValueError(f"no valid items in {self._path}")
        return tuple(items)

    def all_items(self) -> list[BankItem]:
        return list(self._load())

    def get(self, item_id: str) -> BankItem | None:
        return next((i for i in self._load() if i.item_id == item_id), None)

    def shortlist(self, variable: str, exclude: set[str]) -> list[BankItem]:
        """Every unserved active item measuring `variable`, ACROSS MODALITIES.

        The candidate pool for one Picking Agent. Deliberately not filtered by modality:
        the whole point of a common theta scale is that an MCQ item and a code question
        compete on information for the same variable.
        """
        return [
            item
            for item in self._load()
            if item.status == "active"
            and item.item_id not in exclude
            and item.loading(variable) > 0
        ]

    def variables(self) -> list[str]:
        return sorted({m.variable for i in self._load() for m in i.measures})

    def coverage(self) -> dict[str, dict[str, int]]:
        """variable -> {modality: count}. What can actually be adaptively assessed."""
        counts: dict[str, dict[str, int]] = {}
        for item in self._load():
            if item.status != "active":
                continue
            for entry in item.measures:
                bucket = counts.setdefault(entry.variable, {})
                bucket[item.modality] = bucket.get(item.modality, 0) + 1
        return dict(sorted(counts.items()))

    def information_parity(self, variable: str) -> dict:
        """Can each modality ever win a ranking for this variable?

        The diagnostic for the risk in `calibration.py`. Code items carry AUTHORED
        discrimination, MCQ items carry CALIBRATED discrimination, and information scales
        with a squared — so if the authored values run low, code questions lose every
        ranking and the assessment quietly becomes MCQ-only while still looking mixed.

        Peak information is compared rather than information at any particular ability,
        because a modality that can never win anywhere is the failure worth catching; one
        that wins only at some abilities is working as intended.

        TWO CAUSES, ONLY ONE OF THEM A PROBLEM. A modality can lose because its items are
        badly calibrated, or because they only lightly measure this variable — a code
        question loading 0.2 on a variable genuinely tells you less about it, and losing
        the ranking is the correct outcome, not a fault. So both are reported: `intrinsic`
        ignores loading and exposes calibration; `effective` includes it and predicts what
        will actually be administered. Only a modality weak on BOTH is miscalibrated.
        """
        pool = self.shortlist(variable, exclude=set())
        effective: dict[str, float] = {}
        intrinsic: dict[str, float] = {}
        for item in pool:
            peak = max(
                fisher_information(float(t), item.cat.a, item.cat.b, item.cat.c)
                for t in THETA_GRID
            )
            loading = item.loading(variable)
            intrinsic[item.modality] = max(intrinsic.get(item.modality, 0.0), peak)
            effective[item.modality] = max(effective.get(item.modality, 0.0), peak * loading)

        def relative(values: dict[str, float]) -> dict[str, float]:
            best = max(values.values(), default=0.0)
            return {m: round(v / best, 3) if best else 0.0 for m, v in values.items()}

        relative_effective = relative(effective)
        relative_intrinsic = relative(intrinsic)
        return {
            "variable": variable,
            "modalities": sorted(effective),
            "peak_effective": {m: round(v, 4) for m, v in effective.items()},
            "peak_intrinsic": {m: round(v, 4) for m, v in intrinsic.items()},
            "relative_effective": relative_effective,
            "relative_intrinsic": relative_intrinsic,
            # Rarely administered here, for whichever reason. Expected when loading is low.
            "rarely_selected": sorted(m for m, v in relative_effective.items() if v < 0.5),
            # Weak even at full loading: the item parameters themselves are the problem.
            # This is what `calibration.py` warns about and the only entry worth acting on.
            "miscalibrated": sorted(m for m, v in relative_intrinsic.items() if v < 0.5),
        }

    def parity_report(self) -> list[dict]:
        """`information_parity` for every variable carrying more than one modality."""
        return [
            self.information_parity(variable)
            for variable, modalities in self.coverage().items()
            if len(modalities) > 1
        ]
