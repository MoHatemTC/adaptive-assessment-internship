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
from app.services.orchestrator.competency import main_competency

logger = logging.getLogger(__name__)

# Canonical branch-wide bank (mcq + code + open), under backend/app/data.
BANK_PATH = Path(__file__).resolve().parents[2] / "data" / "question_bank.json"


def _main_code_sort_key(code: str) -> tuple[int, str]:
    """C1..C10 in numeric order, not lexicographic (C10 before C2)."""
    if code.startswith("C") and code[1:].isdigit():
        return (0, int(code[1:]))
    return (1, code)


def _sorted_main_codes(codes: set[str] | list[str]) -> list[str]:
    return sorted(codes, key=_main_code_sort_key)


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
        """Main competencies (C1..C10) that the bank can assess."""
        return _sorted_main_codes(
            {main_competency(m.variable) for i in self._load() for m in i.measures}
        )

    def main_competencies(self) -> list[dict]:
        """Candidate-facing main competencies with names and coverage."""
        return self.tracks()

    def coverage(self) -> dict[str, dict[str, int]]:
        """main competency -> {modality: count}."""
        counts: dict[str, dict[str, int]] = {}
        for item in self._load():
            if item.status != "active":
                continue
            for entry in item.measures:
                main = main_competency(entry.variable)
                bucket = counts.setdefault(main, {})
                bucket[item.modality] = bucket.get(item.modality, 0) + 1
        ordered = _sorted_main_codes(counts.keys())
        return {code: counts[code] for code in ordered}

    def tracks(self) -> list[dict]:
        """The five assessable tracks — T1..T5 — each with the variables under it.

        A track is what a candidate chooses at the start, and it is derived, not authored:
        the track code is the prefix of a variable id (`T1.4` -> `T1`), and its name is the
        `competency` string carried by the items whose PRIMARY measure sits in it.

        BY PRIMARY MEASURE, and that distinction is load-bearing. Items cross-load: a
        pandas question measures `T2.1` at 0.8 and `T1.1` at 0.2, because writing it well
        does require Python. Grouping by every measured variable would file that item under
        both tracks and report a candidate choosing "Python" into questions about
        dataframes. The heaviest measure is what the item is actually about.

        Variables are listed per track including cross-loaded ones, because they remain
        assessable there — an examinee taking T2 can still be measured on T1.1 by a T2
        item, and the queue is per variable, not per track.
        """
        names: dict[str, dict[str, int]] = {}
        variables: dict[str, set[str]] = {}
        for item in self._load():
            if item.status != "active" or not item.measures:
                continue
            primary = max(item.measures, key=lambda m: m.weight).variable
            code = primary.split(".")[0]
            names.setdefault(code, {})
            names[code][item.competency] = names[code].get(item.competency, 0) + 1
            variables.setdefault(code, set()).update(m.variable for m in item.measures)

        coverage = self.coverage()
        return [
            {
                "code": code,
                # The modal name, so one mislabelled item cannot rename a whole track.
                "name": max(names[code].items(), key=lambda kv: kv[1])[0],
                "variables": sorted(variables[code]),
                # The track's own sub-competencies, separated from the ones its questions
                # merely touch. A candidate choosing "Data & ML" should be offered T2.*,
                # not asked whether they also want to be assessed on core Python because
                # a pandas question happens to load 0.2 on it.
                "own_variables": sorted(
                    v for v in variables[code] if v.split(".")[0] == code
                ),
                "items": sum(names[code].values()),
                "modalities": sorted(
                    {m for v in variables[code] for m in coverage.get(v, {})}
                ),
            }
            for code in _sorted_main_codes(names.keys())
        ]

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
