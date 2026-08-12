"""Banks are data. This is where they live, and how a new one arrives.

WHAT CHANGED, AND WHY

`registry.REGISTRY` was a dict of five profiles written into the source. Adding a bank
meant editing a Python file and redeploying, which makes an assessment bank part of the
application rather than part of its content — and it makes "another service posts a bank"
impossible by construction.

A store with TWO LAYERS replaces it:

    seed     the five checked-in banks, read-only, resolved through `ENGINE_DATA_DIR`.
             Every existing test, the evaluation harness and every default deployment see
             exactly what they saw before.
    stored   `BANK_STORE_DIR`, writable. What another service posts lands here, one
             directory per bank.

Stored shadows seed on the same id, which is what makes a checked-in bank replaceable
without deleting it — and why replacing one is a PUT rather than a POST.

VERSION IS A CONTENT HASH, AND IT IS LOAD-BEARING

Every read carries a version. The orchestrator caches item parameters against it, and an
assessment pins the version it began under. Without that, posting a bank mid-session
changes the pool underneath a candidate — items vanish from a queue that already ranked
them, and a session's own record of what it asked stops matching what the bank says exists.

The hash is over the bytes, so it is stable across processes and across machines, which an
mtime is not.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from cat_engine.engine.config.paths import DATA_DIR
from cat_engine.engine.schemas.orchestration import BankItem
from cat_engine.engine.services.competency_graph.validator import (
    CompetencyGraphValidationError,
    parse_and_validate_graph,
)

logger = logging.getLogger(__name__)

#: A bank id becomes a directory name. Anything that could escape the store, collide on a
#: case-insensitive filesystem, or read as a shell argument is refused at the door — this
#: is the only place an id supplied over HTTP touches a path.
BANK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

BANK_FILE = "bank.json"
GRAPH_FILE = "graph.json"
PROFILE_FILE = "profile.json"


def default_store_dir() -> Path:
    """Where posted banks land. `BANK_STORE_DIR`, or `banks/` beside the seeds.

    Read through `app.config.paths` rather than a second environment variable of its own
    when unset, so a deployment that moves the data directory does not have to remember to
    move this one too.
    """
    import os

    configured = os.environ.get("BANK_STORE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else DATA_DIR / "banks"


@dataclass(frozen=True)
class BankProfile:
    """One assessable bank and everything that must travel with it.

    The unit is the PAIR, not the bank: the coverage gate asks the graph which
    sub-competencies a main requires and the bank which items measure them, so a mismatched
    pair reports every required node unmeasured and vetoes convergence for the whole
    session. A profile that could name a bank without a graph would make that possible to
    configure by accident.
    """

    bank_id: str
    title: str
    bank_path: Path
    graph_path: Path | None = None
    #: Declared for tests and the bank picker. The authoritative source is still the file;
    #: this is what the bank is SUPPOSED to contain, so a silent content change is caught.
    mains: tuple[str, ...] = ()
    #: None defers to `settings.graph_coverage_critical_only`. Set per bank because the
    #: right answer depends on how many sub-competencies sit under a main against how many
    #: questions the budget allows.
    coverage_critical_only: bool | None = None
    source: Literal["seed", "stored"] = "seed"


@dataclass(frozen=True)
class Finding:
    """One thing wrong, or one thing worth knowing, about a bank."""

    severity: Literal["error", "warning"]
    code: str
    message: str
    subject: str = ""


@dataclass
class Validation:
    """Why a bank was accepted or refused, in enough detail to fix it."""

    bank_id: str
    findings: list[Finding] = field(default_factory=list)
    items: int = 0
    mains: list[str] = field(default_factory=list)
    modalities: list[str] = field(default_factory=list)
    version: str = ""

    def error(self, code: str, message: str, subject: str = "") -> None:
        self.findings.append(Finding("error", code, message, subject))

    def warn(self, code: str, message: str, subject: str = "") -> None:
        self.findings.append(Finding("warning", code, message, subject))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def accepted(self) -> bool:
        return not self.errors


class UnknownBankError(KeyError):
    """A bank id that is not registered. Names the valid ones — the caller is usually an
    environment variable or a request field, and 'KeyError: AIE2' helps nobody."""

    def __init__(self, bank_id: str, registered: list[str]) -> None:
        super().__init__(f"unknown bank {bank_id!r}; registered: {sorted(registered)}")


class BankStoreError(ValueError):
    """A write that cannot proceed. Distinct from a validation failure, which is data."""


def _hash_bytes(*blobs: bytes) -> str:
    digest = hashlib.sha256()
    for blob in blobs:
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
    return digest.hexdigest()[:16]


class BankStore:
    """Seeds, overlaid by whatever has been posted. The one place a bank is resolved."""

    def __init__(
        self,
        *,
        seeds: Mapping[str, BankProfile],
        store_dir: Path | None = None,
    ) -> None:
        self._seeds = dict(seeds)
        self._store_dir = store_dir if store_dir is not None else default_store_dir()
        self._versions: dict[str, tuple[tuple, str]] = {}
        self._banks: dict[str, tuple[str, Any]] = {}
        self._graphs: dict[str, tuple[str, Any]] = {}
        self._policies: dict[str, tuple[str, Any]] = {}

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    # --- discovery ---------------------------------------------------------
    def _stored_profile(self, directory: Path) -> BankProfile | None:
        try:
            raw = json.loads((directory / PROFILE_FILE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.error("stored bank %s has no readable profile", directory.name)
            return None
        graph_path = directory / GRAPH_FILE
        return BankProfile(
            bank_id=str(raw.get("bank_id") or directory.name),
            title=str(raw.get("title") or directory.name),
            bank_path=directory / BANK_FILE,
            graph_path=graph_path if graph_path.is_file() else None,
            mains=tuple(str(m) for m in raw.get("mains") or ()),
            coverage_critical_only=raw.get("coverage_critical_only"),
            source="stored",
        )

    def profiles(self) -> dict[str, BankProfile]:
        """Every registered bank. Stored entries shadow seeds on the same id."""
        resolved = dict(self._seeds)
        if self._store_dir.is_dir():
            for directory in sorted(self._store_dir.iterdir()):
                if not directory.is_dir() or not (directory / BANK_FILE).is_file():
                    continue
                profile = self._stored_profile(directory)
                if profile is not None:
                    resolved[profile.bank_id] = profile
        return resolved

    def profile(self, bank_id: str) -> BankProfile:
        resolved = self.profiles()
        if bank_id not in resolved:
            raise UnknownBankError(bank_id, list(resolved))
        return resolved[bank_id]

    def __contains__(self, bank_id: object) -> bool:
        return bank_id in self.profiles()

    # --- versioning --------------------------------------------------------
    @staticmethod
    def _stamp(paths: list[Path]) -> tuple:
        """A cheap change token: (path, mtime_ns, size) per file.

        Guards the hash, which reads up to 1.8 MB. A file rewritten in place with an
        identical size and timestamp would defeat it — `reset_caches()` is the answer to
        that, and it is what every test that rewrites a bank already calls.
        """
        stamp = []
        for path in paths:
            try:
                info = path.stat()
                stamp.append((str(path), info.st_mtime_ns, info.st_size))
            except OSError:
                stamp.append((str(path), 0, -1))
        return tuple(stamp)

    def version(self, bank_id: str) -> str:
        """A content hash over the bank, its graph and its declared profile.

        Over the BYTES, so two processes and two machines agree. An mtime does not survive
        a container image build, and a version that changes on deploy would invalidate
        every orchestrator cache for no reason.
        """
        profile = self.profile(bank_id)
        paths = [profile.bank_path]
        if profile.graph_path is not None:
            paths.append(profile.graph_path)

        stamp = self._stamp(paths)
        cached = self._versions.get(bank_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        blobs = []
        for path in paths:
            try:
                blobs.append(path.read_bytes())
            except OSError:
                blobs.append(b"")
        blobs.append(
            json.dumps(
                {
                    "mains": list(profile.mains),
                    "coverage_critical_only": profile.coverage_critical_only,
                    "title": profile.title,
                },
                sort_keys=True,
            ).encode()
        )
        version = _hash_bytes(*blobs)
        self._versions[bank_id] = (stamp, version)
        return version

    # --- reads, cached against the version ---------------------------------
    def _cached(self, store: dict, bank_id: str, build):
        """Return the cached object when the bank's version has not moved.

        Version-keyed rather than `@cache`-keyed, so posting a bank invalidates exactly
        the entries that changed and nothing else. Identity is preserved for an unchanged
        bank, which several tests and the orchestrator's own parameter cache rely on.
        """
        version = self.version(bank_id)
        cached = store.get(bank_id)
        if cached is not None and cached[0] == version:
            return cached[1]
        built = build()
        store[bank_id] = (version, built)
        return built

    def bank(self, bank_id: str):
        from cat_engine.engine.services.orchestrator.bank import JsonUnifiedBank

        profile = self.profile(bank_id)
        return self._cached(
            self._banks, bank_id, lambda: JsonUnifiedBank(profile.bank_path)
        )

    def graph(self, bank_id: str):
        """The raw validated graph, before any policy is applied."""
        from cat_engine.engine.services.competency_graph import load_competency_graph

        profile = self.profile(bank_id)
        if profile.graph_path is None:
            return None
        return load_competency_graph(profile.graph_path)

    # --- writes ------------------------------------------------------------
    def _directory_for(self, bank_id: str) -> Path:
        if not BANK_ID.match(bank_id):
            raise BankStoreError(
                f"bank id {bank_id!r} is not usable as a directory name; expected "
                "letters, digits, dot, dash or underscore, starting alphanumeric, "
                "at most 64 characters"
            )
        directory = (self._store_dir / bank_id).resolve()
        # Belt and braces. The pattern already forbids a separator, but the one place an
        # id supplied over HTTP becomes a path is worth checking twice.
        if self._store_dir.resolve() not in directory.parents:
            raise BankStoreError(f"bank id {bank_id!r} escapes the bank store")
        return directory

    def is_stored(self, bank_id: str) -> bool:
        return (self._store_dir / bank_id / BANK_FILE).is_file()

    def save(
        self,
        *,
        bank_id: str,
        title: str,
        items: list[dict[str, Any]],
        graph: dict[str, Any] | None,
        coverage_critical_only: bool | None,
        validation: Validation,
    ) -> None:
        """Write an ALREADY-VALIDATED bank. Refuses otherwise.

        Validation and writing are separate calls so `POST /banks/validate` can run the
        first without the second, and so this can never be the place where a caller
        forgets. Written to a temporary directory and moved into place, so a failure
        halfway through leaves the previous bank intact rather than a half-written one.
        """
        if not validation.accepted:
            raise BankStoreError(
                f"refusing to write {bank_id}: {[f.message for f in validation.errors]}"
            )

        directory = self._directory_for(bank_id)
        staging = directory.with_name(f".{bank_id}.incoming")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        try:
            (staging / BANK_FILE).write_text(
                json.dumps({"items": items}, indent=2, sort_keys=False),
                encoding="utf-8",
            )
            if graph is not None:
                (staging / GRAPH_FILE).write_text(
                    json.dumps(graph, indent=2), encoding="utf-8"
                )
            (staging / PROFILE_FILE).write_text(
                json.dumps(
                    {
                        "bank_id": bank_id,
                        "title": title or bank_id,
                        "mains": validation.mains,
                        "coverage_critical_only": coverage_critical_only,
                        "registered_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            previous = directory.with_name(f".{bank_id}.previous")
            if previous.exists():
                shutil.rmtree(previous)
            if directory.exists():
                directory.rename(previous)
            staging.rename(directory)
            if previous.exists():
                shutil.rmtree(previous)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        self.reset(bank_id)

    def delete(self, bank_id: str) -> bool:
        """Remove a stored bank. A seed underneath it reappears — that is the point.

        Deregistering a bank that shadows a checked-in one restores the checked-in one
        rather than leaving nothing, which is what makes an accidental overwrite
        recoverable without a redeploy.
        """
        directory = self._directory_for(bank_id)
        if not directory.is_dir():
            return False
        shutil.rmtree(directory)
        self.reset(bank_id)
        return True

    def reset(self, bank_id: str | None = None) -> None:
        """Drop cached banks, graphs, policies and versions."""
        if bank_id is None:
            self._versions.clear()
            self._banks.clear()
            self._graphs.clear()
            self._policies.clear()
            return
        for store in (self._versions, self._banks, self._graphs, self._policies):
            store.pop(bank_id, None)

    # --- validation --------------------------------------------------------
    def validate(
        self,
        *,
        bank_id: str,
        items: list[dict[str, Any]],
        graph: dict[str, Any] | None,
        coverage_critical_only: bool | None,
        question_budget: int,
        deployment_critical_only: bool,
    ) -> Validation:
        """Everything that can be checked before a bank is allowed to measure anybody.

        The checks are the ones the engine's own suite already asserts of the five
        checked-in banks — a bank arriving over HTTP has to clear the same bar, or the
        suite is testing the seeds rather than the system.

        ERRORS are things that make the bank unusable or the coverage gate unsatisfiable.
        WARNINGS are things worth knowing. The information-parity diagnostic is deliberately
        a warning: an item that loses a ranking BECAUSE IT GENUINELY MEASURES LESS is
        correct behaviour, and only the miscalibrated case is a defect.
        """
        report = Validation(bank_id=bank_id)

        if not BANK_ID.match(bank_id):
            report.error(
                "bank_id_invalid",
                f"{bank_id!r} is not a usable bank id; expected letters, digits, dot, "
                "dash or underscore, starting alphanumeric, at most 64 characters",
            )
            return report

        parsed = self._validate_items(items, report)
        if not parsed:
            return report

        mains = sorted({m.variable.split(".")[0] for i in parsed for m in i.measures})
        report.mains = mains
        report.modalities = sorted({i.modality for i in parsed if i.status == "active"})
        report.items = len(parsed)

        if graph is not None:
            self._validate_graph(
                graph=graph,
                items=parsed,
                mains=mains,
                report=report,
                critical_only=(
                    deployment_critical_only
                    if coverage_critical_only is None
                    else coverage_critical_only
                ),
                question_budget=question_budget,
            )
        else:
            report.warn(
                "no_graph",
                "no competency graph supplied; the coverage gate cannot run and this "
                "bank will be assessed without one",
            )
        return report

    @staticmethod
    def _validate_items(
        items: list[dict[str, Any]], report: Validation
    ) -> list[BankItem]:
        parsed: list[BankItem] = []
        seen: set[str] = set()
        for entry in items:
            identifier = str(entry.get("item_id") or "<no id>")
            try:
                item = BankItem.model_validate(entry)
            except ValidationError as exc:
                # The a/b/c bounds live on the schema, so a discrimination of 12.0 is
                # caught here rather than producing silently meaningless information
                # scores on every ranking for the life of the bank.
                report.error("item_invalid", str(exc), identifier)
                continue
            if item.item_id in seen:
                report.error(
                    "item_id_duplicate",
                    f"{item.item_id} appears more than once; the second would shadow the "
                    "first in every lookup",
                    item.item_id,
                )
                continue
            seen.add(item.item_id)
            parsed.append(item)

        if not parsed:
            report.error("no_valid_items", "no item in this submission parsed")
        elif not any(i.status == "active" for i in parsed):
            report.error(
                "no_active_items",
                "every item is inactive; this bank can never present a question",
            )
        return parsed

    @staticmethod
    def _validate_graph(
        *,
        graph: dict[str, Any],
        items: list[BankItem],
        mains: list[str],
        report: Validation,
        critical_only: bool,
        question_budget: int,
    ) -> None:
        from cat_engine.engine.services.competency_graph.coverage import sub_nodes_for_main
        from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService

        try:
            parsed = parse_and_validate_graph(graph, source=report.bank_id)
        except (CompetencyGraphValidationError, KeyError, TypeError, ValueError) as exc:
            report.error("graph_invalid", str(exc))
            return

        service = CompetencyGraphService(parsed)
        node_ids = set(parsed.nodes)

        graph_mains = {
            nid for nid, node in parsed.nodes.items() if node.node_type == "main"
        }
        graph_mains |= {nid.split(".", 1)[0] for nid in node_ids}
        if not set(mains) & graph_mains:
            report.error(
                "graph_bank_mismatch",
                f"the bank measures {mains} and the graph declares "
                f"{sorted(graph_mains)}; a mismatched pair marks every required node "
                "unmeasured and vetoes convergence for the whole session",
            )
            return

        measured = {m.variable for item in items for m in item.measures}
        missing = sorted(measured - node_ids)
        if missing:
            report.error(
                "measured_node_absent",
                f"measured but absent from the graph: {missing} — a measured variable "
                "with no node is invisible to coverage and to blocking",
            )

        measured_active = {
            m.variable for item in items if item.status == "active" for m in item.measures
        }
        for main in mains:
            required = sub_nodes_for_main(service, main, critical_only=critical_only)
            if len(required) > question_budget - 2:
                report.error(
                    "coverage_unreachable",
                    f"{main} requires {len(required)} sub-competencies "
                    f"({'critical only' if critical_only else 'all'}) against a "
                    f"{question_budget}-question cap; the coverage gate could never be "
                    "satisfied and every session would end on the budget escape",
                    main,
                )
            unserved = sorted(set(required) - measured_active)
            if unserved:
                report.error(
                    "required_node_unmeasured",
                    f"{main} requires {unserved}, which no active item measures; the "
                    "coverage gate would be unsatisfiable",
                    main,
                )


def _seed_profiles() -> dict[str, BankProfile]:
    """The five checked-in banks.

    Still written down rather than discovered, because each row carries a claim the file
    cannot make about itself — which mains it is supposed to measure, and whether full
    coverage is reachable for it. A directory scan would lose both, and with them the test
    that catches a bank file changing content without anyone noticing.
    """
    return {
        "DA": BankProfile(
            bank_id="DA",
            title="Prepare and Analyze Data",
            bank_path=DATA_DIR / "question_bank.json",
            graph_path=DATA_DIR / "competency_graph.json",
            mains=("DA",),
            # Six sub-nodes against a twelve-question cap: full coverage is reachable.
            coverage_critical_only=None,
        ),
        "PY": BankProfile(
            bank_id="PY",
            title="Python Engineering",
            bank_path=DATA_DIR / "question_bank_PY_20260803_083616.json",
            graph_path=DATA_DIR / "competency_graph_PY_20260803_083616.json",
            mains=("PY",),
            coverage_critical_only=None,
        ),
        "AIE": BankProfile(
            bank_id="AIE",
            title="AI Engineer",
            bank_path=DATA_DIR / "question_bank_AIE.json",
            graph_path=DATA_DIR / "competency_graph_AIE.json",
            mains=("C1", "C3", "C6"),
            # C6 declares sixteen sub-competencies and every AIE item measures exactly
            # one, so full coverage would cost sixteen questions against a cap of twelve —
            # the gate could never be satisfied and every session would end on the budget
            # escape. Critical-only reduces the requirement to five.
            coverage_critical_only=True,
        ),
        "AIE-JR-V3": BankProfile(
            bank_id="AIE-JR-V3",
            title="Junior AI Engineer v3 (60 items)",
            bank_path=DATA_DIR / "question_bank_AIE_JR_v3.json",
            graph_path=DATA_DIR / "competency_graph_AIE_JR_v3.json",
            mains=("C1",),
            # The generated coverage graph contains exactly the three measurable nodes.
            coverage_critical_only=False,
        ),
        "JAI-600": BankProfile(
            bank_id="JAI-600",
            title="Junior AI Engineer 2026 (600 items)",
            bank_path=DATA_DIR / "question_bank_JAI_2026_600.json",
            graph_path=DATA_DIR / "competency_graph_JAI_2026_600.json",
            mains=("C1", "C2", "C3", "C4", "C5", "C6"),
            # Each main has only 3-5 nodes, so full direct coverage is reachable under the
            # twelve-question per-main limit and is preferable to weakening it.
            coverage_critical_only=False,
        ),
    }


class ProfileView(Mapping):
    """A live read-only mapping of bank id -> profile.

    `registry.REGISTRY` used to be a plain dict, and it is read by the test suite, by the
    unknown-bank error and by the bank picker. Keeping the NAME and the mapping behaviour
    while making the contents dynamic is what lets a posted bank appear everywhere a
    checked-in one does, with no caller changed.
    """

    def __init__(self, store: BankStore) -> None:
        self._store = store

    def __getitem__(self, bank_id: str) -> BankProfile:
        return self._store.profile(bank_id)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._store.profiles()))

    def __len__(self) -> int:
        return len(self._store.profiles())

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"ProfileView({sorted(self._store.profiles())})"
