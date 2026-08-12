"""The surface a host calls.

WHAT THIS REPLACES

Seven FastAPI applications on seven ports. Every route here became a method; every route
that existed only to let one service reach another became a function call and then a
function call that no longer needed making. The route DESCRIPTIONS did not survive as
descriptions — they survive as the docstrings below, because what they documented was
never HTTP, it was which decisions this component is allowed to make.

ONE SHAPE FOR EVERY LIFECYCLE CALL

`begin`, `answer`, `state` all return `AssessmentStateResponse`: `presenting` when there is
a question, `report` when there is not. A host has one thing to render and one place to look
for "what now". That property was load-bearing for a frontend and is preserved exactly.

THE CANDIDATE BOUNDARY IS STILL A TYPE

Lifecycle methods return contract DTOs, which have no field to put an answer key in. The
instrumented view lives behind `author_diagnostics_enabled` and RAISES when off rather than
returning a thinned version — so "is this safe to return" is never a judgement call made at
a call site.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from cat_engine import catalogue, projection
from cat_engine import scope as scoping
from cat_engine.config import CatConfig
from cat_engine.contracts import (
    AssessmentReportDTO,
    AssessmentStateResponse,
    BankItemFull,
    BankSummary,
    CompetencyGraphDTO,
    DiagnosticsResponse,
    PolicyDTO,
    PresentingDTO,
    ScopeManifest,
    ScopeRequest,
    ScopeSelection,
    UploadReceipt,
)
from cat_engine.engine.schemas.orchestration import AssessmentState, BankItem
from cat_engine.engine.schemas.voice import VoiceResponsePackage
from cat_engine.engine.services.orchestrator import registry, session_dump
from cat_engine.errors import (
    AnswerInvalid,
    AnswerMissing,
    AnswerTypeMismatch,
    AssessmentUnknown,
    CapacityReached,
    CatError,
    DiagnosticsDisabled,
    GraderUnavailable,
    ScopeInvalid,
    ScopeUnassessable,
    StaleAnswer,
    WritesDisabled,
)
from cat_engine.ingest import Ingest
from cat_engine.stores import Session, SessionConflict, SessionStore, open_session_store
from cat_engine.wiring import Wiring

logger = logging.getLogger(__name__)

__all__ = ["AssessmentModule", "SyncAssessmentModule"]


class AssessmentModule:
    """One configured assessment engine, with the state it owns.

    Async-native, because the picking agent and rubric grading are model calls and the
    engine's loop is written around them. `SyncAssessmentModule` wraps this for a host that
    is not.
    """

    def __init__(
        self,
        config: CatConfig | None = None,
        *,
        sessions: SessionStore | None = None,
    ) -> None:
        self.config = config or CatConfig()
        self.settings = self.config.apply()

        if self.settings.bank_store_dir.strip() or self.settings.bank_database_url.strip():
            self._open_bank_store()

        self.sessions: SessionStore = (
            sessions
            if sessions is not None
            else open_session_store(self.settings.session_database_url)
        )
        self.wiring = Wiring()
        self.ingest = Ingest(self.settings)
        self._live = None

    @property
    def live(self):
        """Interview rooms, built on first access.

        Lazy because `cat_engine.live` reaches a realtime SDK that is an OPTIONAL
        dependency. Constructing it eagerly made `from cat_engine import AssessmentModule`
        fail with `No module named 'google'` on any host that installed the base package —
        so every host that never runs an interview had to install one anyway.
        """
        if self._live is None:
            from cat_engine.live import Live

            self._live = Live(self.settings)
        return self._live

    # --- construction helpers ----------------------------------------------
    def _open_bank_store(self) -> None:
        """Point the process at a bank store other than the packaged one.

        Both branches go through `registry.use_store`, which is the seam the engine already
        provides for exactly this — it exists because a deployment resolves its store at
        startup rather than from a module-level constant.
        """
        from cat_engine.engine.services.orchestrator.bank_store import (
            BankStore,
            _seed_profiles,
        )

        dsn = self.settings.bank_database_url.strip()
        if dsn:
            # `install` ensures the schema and seeds the checked-in banks idempotently on
            # their content hash, so every boot costs one query per bank and no deployment
            # has a seeding step somebody has to remember to run exactly once.
            from cat_engine.stores.sql.backed import install

            install(dsn)
            logger.info("banks resolve from Postgres")
            return

        registry.use_store(
            BankStore(seeds=_seed_profiles(), store_dir=self.settings.bank_store_dir)
        )
        logger.info("banks resolve from %s", self.settings.bank_store_dir)

    def _session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise AssessmentUnknown(f"no assessment {session_id}")
        return session

    def _orchestrator(self, session: Session):
        return self.wiring.orchestrator_for(
            session.bank_id, session.bank_version, session.scope
        )

    # --- catalogue ----------------------------------------------------------
    def banks(self) -> list[BankSummary]:
        """Every registered bank, for a picker."""
        return catalogue.banks()

    def bank(self, bank_id: str | None = None) -> BankSummary:
        return catalogue.summary(bank_id)

    def items(self, bank_id: str | None = None) -> list[BankItemFull]:
        """Every item WITH its payload. Rendering and authoring, never selection."""
        return catalogue.items(bank_id)

    def item(self, item_id: str, bank_id: str | None = None) -> BankItemFull:
        return catalogue.item(bank_id, item_id)

    def graph(self, bank_id: str | None = None) -> CompetencyGraphDTO | None:
        return catalogue.graph(bank_id)

    def policy(self, bank_id: str | None = None) -> PolicyDTO | None:
        return catalogue.policy(bank_id)

    # --- scoping ------------------------------------------------------------
    def scope(
        self,
        selected: list[str],
        *,
        bank_id: str | None = None,
        critical_only: bool | None = None,
        include_prerequisites: bool = False,
    ) -> ScopeManifest:
        """Preview what a selection of competencies would assess.

        Worth calling before `begin` whenever the selection is user-built. An unassessable
        selection comes back with `coverage.reachable = False` and the offending
        competencies named, which is what a picker needs to tell somebody what to add;
        `begin` refuses the same selection, but by then there is a user staring at an error.

        A SELECTION WIDENS. Sub-competencies can be shared — selecting one main whole may
        necessarily measure something evidencing another, so that other main is opened too
        and marked `implied` on the report.
        """
        return scoping.build_manifest(
            ScopeRequest(
                bank_id=registry.resolve_bank_id(bank_id),
                selected=list(selected),
                critical_only=critical_only,
                include_prerequisites=include_prerequisites,
            )
        )

    def scope_graph(self, selected: list[str], **kwargs) -> str:
        """The induced sub-graph, drawn as mermaid.

        Rendered from the manifest that produced the allowlist, so the drawing and the
        allowlist cannot disagree. Excluded siblings are drawn greyed rather than omitted: a
        scope is a statement about what is NOT being measured at least as much as what is,
        and a picture of the retained nodes alone makes two-of-seven look like a whole
        competency.
        """
        return scoping.render_mermaid(self.scope(selected, **kwargs))

    # --- lifecycle ----------------------------------------------------------
    async def begin(
        self,
        *,
        bank_id: str | None = None,
        target_variables: list[str] | None = None,
        scope: list[str] | ScopeSelection | None = None,
        intake: dict[str, int] | None = None,
        confidence: dict[str, bool] | None = None,
        use_llm: bool = True,
        seed: int | None = None,
    ) -> AssessmentStateResponse:
        """Begin an assessment.

        The bank id and the bank VERSION are pinned to the session here and never sent
        again: a caller that began against one bank must not be able to answer it against
        another, and a bank replaced mid-session must not change the item pool underneath a
        live candidate.

        `scope` and `target_variables` are mutually exclusive — `target_variables` is the
        same idea restricted to main competencies, and two answers to "which competencies
        does this assessment cover" is one too many.

        Do not pass `seed` in production. It exists for deterministic replay; omitting it
        draws OS entropy, and exposure control must not be predictable.
        """
        try:
            resolved = registry.resolve_bank_id(bank_id)
        except registry.UnknownBankError as exc:
            raise ScopeInvalid(str(exc), code="bank_unknown") from exc
        version = self.wiring.current_version(resolved)

        manifest = self._scope_for(scope, target_variables, resolved)
        orchestrator = self.wiring.orchestrator_for(resolved, version, manifest)

        # THE SCOPE DECIDES THE TARGETS, AND THEY ARE ALWAYS MAINS.
        #
        # A sub-competency must never become a target variable. `rollup_outcomes` folds
        # every graded outcome to `variable.split(".")[0]` and drops it when that main is
        # not in the session, so a session begun on ["C1.1"] would compute outcomes,
        # propagate them, and discard every one before the posterior — running to the
        # question cap with the standard error exactly where it started, raising nothing.
        if manifest is not None:
            targets = [row.main for row in manifest.mains]
        else:
            targets = target_variables or orchestrator._bank.variables()

        try:
            state = orchestrator.begin(
                targets, intake=intake or {}, confidence=confidence or {}
            )
        except ValueError as exc:
            raise ScopeInvalid(str(exc), code="targets_invalid") from exc

        rng = np.random.default_rng(seed)
        state = await orchestrator.fill_queue(state, use_llm=use_llm, rng=rng)
        state = orchestrator.ensure_presenting(state)

        if self.sessions.at_capacity():
            raise CapacityReached(
                "assessment session capacity reached; retry after a session finishes"
            )
        session = Session(
            bank_id=resolved,
            bank_version=version,
            state=state,
            rng=rng,
            scope=manifest,
        )
        self.sessions.add(session)
        self.sessions.save(session)
        return self._state_response(session)

    def _scope_for(
        self,
        scope: list[str] | ScopeSelection | None,
        target_variables: list[str] | None,
        bank_id: str,
    ) -> ScopeManifest | None:
        """Build the scope this session runs under, and refuse it if it cannot be assessed.

        Built HERE rather than accepted as a `scope_id`. The id is a hash of its inputs, so
        it cannot be turned back into an allowlist, and accepting one would mean applying a
        set of item ids nobody in this process derived. Because the manifest is a pure
        function of (bank version, selection), a caller that previewed the scope gets the
        identical `scope_id` back on the session anyway.

        An unassessable scope is refused BEFORE a candidate is involved. The alternative is
        a session that opens, presents two questions, exhausts its pool and finalises every
        competency as `bank_exhausted` — a shape indistinguishable afterwards from a
        candidate who simply stopped answering.
        """
        if scope is None:
            return None
        if target_variables:
            raise ScopeInvalid(
                "pass either `scope` or `target_variables`; `target_variables` is the same "
                "idea restricted to main competencies, and two answers to which "
                "competencies this assessment covers is one too many",
                code="scope_and_targets",
            )

        if isinstance(scope, ScopeSelection):
            request = ScopeRequest(
                bank_id=bank_id,
                selected=list(scope.selected),
                critical_only=scope.critical_only,
                include_prerequisites=scope.include_prerequisites,
            )
        else:
            request = ScopeRequest(bank_id=bank_id, selected=list(scope))

        manifest = scoping.build_manifest(request)
        if not manifest.assessable:
            raise ScopeUnassessable(_why_unassessable(manifest))
        return manifest

    def state(self, session_id: str) -> AssessmentStateResponse:
        """Where this assessment is: the presented item, or the report."""
        session = self._session(session_id)
        session.state = self._orchestrator(session).ensure_presenting(session.state)
        return self._state_response(session)

    def next_item(self, session_id: str) -> PresentingDTO:
        """The question to present, for a caller that wants only that.

        Raises once the assessment has stopped — returning nothing would read as "not yet"
        rather than "this is over".
        """
        response = self.state(session_id)
        if response.presenting is None:
            raise CatError(
                f"this assessment has stopped ({response.stop_reason or 'no reason recorded'})",
                code="assessment_stopped",
                status_code=409,
            )
        return response.presenting

    def report(self, session_id: str) -> AssessmentReportDTO:
        """The report. Refused while the assessment is still running.

        Report the BAND RANGE, not the point band. At the standard-error target,
        within-one-level accuracy runs about 99% while exact-band accuracy runs about 65% —
        a single reported level is more confident than the measurement supports.
        """
        response = self.state(session_id)
        if response.report is None:
            raise CatError(
                "this assessment has not stopped; a report before then would state a "
                "measurement that has not been made",
                code="assessment_running",
                status_code=409,
            )
        return response.report

    def discard(self, session_id: str) -> bool:
        if not self.sessions.drop(session_id):
            raise AssessmentUnknown(f"no assessment {session_id}")
        return True

    # --- answering ----------------------------------------------------------
    async def answer(
        self,
        session_id: str,
        *,
        mcq: int | None = None,
        code: str | None = None,
        transcript: str | None = None,
        audio: bytes | None = None,
        audio_filename: str = "answer.wav",
        use_llm: bool = True,
    ) -> AssessmentStateResponse:
        """Record one response and advance the loop.

        Two concurrent answers to one assessment are serialised, and the second raises
        `StaleAnswer` if the presented item changed while it waited. Serialising alone is
        not enough: the second would otherwise wake up and apply its stale answer to the
        next item whenever the modality happened to match.

        The returned `last_graded` is an ACKNOWLEDGEMENT, not a result — no score, no
        correctness, no detail. Feedback after each answer changes what the assessment
        measures: a candidate told which hidden test failed learns something between
        question four and question five, and the estimate that comes out is of a person who
        was being taught mid-measurement.
        """
        session = self._session(session_id)
        orchestrator = self._orchestrator(session)

        # Capture which item this answer is FOR before waiting for the lock. Two concurrent
        # calls can otherwise both read the same state, grade the same presentation, then
        # race to overwrite the session.
        session.state = orchestrator.ensure_presenting(session.state)
        snapshot = orchestrator.next_item(session.state)
        expected_item_id = snapshot[0].item_id if snapshot else None

        lock = self.sessions.lock_for(session_id)
        if lock is None:
            raise AssessmentUnknown(f"no assessment {session_id}")

        async with lock:
            session.state = orchestrator.ensure_presenting(session.state)
            current = orchestrator.next_item(session.state)
            if (current[0].item_id if current else None) != expected_item_id:
                raise StaleAnswer("the presented item changed while this answer waited")
            if current is None:
                return self._state_response(session)

            item, _candidate = current
            response = await self._coerce_answer(
                item,
                mcq=mcq,
                code=code,
                transcript=transcript,
                audio=audio,
                audio_filename=audio_filename,
                use_llm=use_llm,
            )

            try:
                new_state, graded = orchestrator.record_response(
                    session.state, item, response
                )
            except (TypeError, ValueError) as exc:
                raise AnswerInvalid(str(exc)) from exc
            except RuntimeError as exc:
                # The sandbox or the model gateway is unreachable. Nothing was recorded, so
                # the same item can be retried — which is why this is not a graded response
                # of weight 0.
                raise GraderUnavailable(str(exc)) from exc

            new_state = await orchestrator.after_response(
                new_state, item, use_llm=use_llm, rng=session.rng
            )
            session.state = orchestrator.ensure_presenting(new_state)
            try:
                self.sessions.save(session)
            except SessionConflict as exc:
                # Another worker recorded an answer while this one was grading. Same
                # condition as the in-process race, for the same reason.
                raise StaleAnswer(str(exc)) from exc
            return self._state_response(
                session, last_graded=projection.grade_receipt(graded)
            )

    async def _coerce_answer(
        self,
        item: BankItem,
        *,
        mcq: int | None,
        code: str | None,
        transcript: str | None,
        audio: bytes | None,
        audio_filename: str,
        use_llm: bool,
    ):
        """One answer, in whatever form the modality takes, refusing a mismatch.

        THE VOICE PATH IS WHERE THE COLLAPSE HAD TO BE CAREFUL.

        `GraderAgent._grade_open` requires an already-evaluated `GradedVoiceResponse`,
        because evaluation is a model call and `record_response` is sync. The grader service
        hid that by awaiting it server-side before grading. Here the await happens at this
        call site instead — the same two steps in the same order, with the network removed
        from between them.
        """
        given = {
            "mcq": mcq is not None,
            "code": code is not None,
            "open": transcript is not None or audio is not None,
        }
        given["voice"] = given["open"]
        if not given.get(item.modality):
            supplied = [k for k, v in given.items() if v and k != "voice"] or ["nothing"]
            raise AnswerTypeMismatch(
                f"a {item.modality} item is presented; got {', '.join(supplied)}"
            )

        if item.modality == "mcq":
            return mcq

        if item.modality == "code":
            if not code:
                raise AnswerMissing("code is required for a code item")
            return code

        if item.modality in ("open", "voice"):
            text = (transcript or "").strip()
            if audio is not None:
                text = self.wiring.grader.transcribe(
                    audio, filename=audio_filename
                ).strip()
            if not text:
                raise AnswerMissing(
                    f"a transcript or audio is required for a {item.modality} item"
                )
            package = VoiceResponsePackage.from_text(item.item_id, text)
            return await self.wiring.grader.evaluate(item, package, use_llm=use_llm)

        raise AnswerInvalid(
            f"unsupported modality {item.modality}", code="modality_unsupported"
        )

    # --- the one shape ------------------------------------------------------
    def _state_response(
        self, session: Session, *, stop_reason: str = "", last_graded=None
    ) -> AssessmentStateResponse:
        """`presenting` when there is a question, `report` when there is not."""
        orchestrator = self._orchestrator(session)
        state = session.state
        stop, reason = orchestrator.should_stop(state)
        reason = stop_reason or reason

        shown: PresentingDTO | None = None
        pair = orchestrator.next_item(state)
        if pair is not None:
            ranked, candidate = pair
            # Resolved through the ORCHESTRATOR'S own bank rather than a fresh handle, so
            # the item rendered is the one that was ranked — same version, same parsed copy.
            # The scope has to travel for that to hold; without it this would build a
            # second, unscoped orchestrator and render out of its bank instead.
            #
            # Behind HTTP this was a second call — `payload_for` — because selection
            # received parameters with no payload and rendering needed a separate fetch.
            # In-process there is one object and `get` returns it whole, so the fetch that
            # existed to keep questions away from the ranking path is not needed to keep
            # them away: `presented_item` is what withholds the answer key, and it is the
            # only thing that ever did.
            full = self.wiring.bank_for(
                session.bank_id, session.bank_version, session.scope
            ).get(ranked.item_id)
            if full is None:
                raise CatError(
                    f"the bank no longer holds {ranked.item_id}",
                    code="item_unreadable",
                    status_code=500,
                )
            shown = projection.presenting(full, candidate)

        report = None
        if stop:
            report = projection.report_dto(orchestrator.summarise(state, reason))
            report.scope = projection.scope_summary(session.scope)
            if not session.recorded:
                # Once per session: polling an assessment must not append it again.
                session.recorded = True
                self.sessions.mark_finished(state.session_id)
                session_dump.record_session(state, session.bank_id, stop_reason=reason)

        return AssessmentStateResponse(
            session_id=state.session_id,
            bank_id=session.bank_id,
            bank_version=session.bank_version,
            stop=stop,
            stop_reason=reason if stop else "",
            items_administered=state.items_administered,
            open_variables=state.open_variables,
            presenting=shown,
            last_graded=last_graded,
            report=report,
        )

    # --- bank writes --------------------------------------------------------
    def upload_bank(
        self,
        bank_id: str,
        content: bytes | str,
        *,
        filename: str = "bank.json",
        dry_run: bool = False,
    ) -> UploadReceipt:
        """Register a bank from an uploaded file. The only way a bank enters the system.

        The competency graph is DERIVED from the questions — an author does not write one
        and is not asked for one. Idempotent by nature: the same bytes under the same id
        produce the same version, first upload or fifth.

        Returns a receipt rather than raising on a bad bank, because the caller is usually
        an authoring tool and the useful answer is which item is at fault. `dry_run=True`
        runs the identical path and writes nothing.
        """
        raw = content.encode("utf-8") if isinstance(content, str) else content
        return self.ingest.register(
            bank_id, raw, filename=filename, write=not dry_run
        )

    def validate_bank(self, bank_id: str, content: bytes | str) -> UploadReceipt:
        """Every check the write path runs, writing nothing."""
        return self.upload_bank(bank_id, content, dry_run=True)

    def delete_bank(self, bank_id: str) -> bool:
        """Remove a stored bank. A checked-in one of the same id reappears underneath it."""
        if not self.settings.admin_api_enabled:
            raise WritesDisabled(
                "the bank write path is disabled on this deployment (ADMIN_API_ENABLED)"
            )
        return self.ingest.delete(bank_id)

    # --- grading primitives -------------------------------------------------
    def public_tests(self, item_id: str, bank_id: str | None = None) -> list[dict]:
        """The example cases a candidate may run against. Public only."""
        return self.wiring.grader.public_tests(bank_id, item_id)

    def trial_run(self, item_id: str, source: str, bank_id: str | None = None):
        """Run a candidate's code against the public cases. Grades nothing.

        Without this, the first time a candidate's code ever executes is the moment it is
        graded — which measures something other than competency: a misread signature they
        would have caught in five seconds becomes a permanent observation about what they
        know.
        """
        return self.wiring.grader.trial_run(bank_id, item_id, source)

    def transcribe(self, audio: bytes, *, filename: str = "answer.wav") -> str:
        return self.wiring.grader.transcribe(audio, filename=filename)

    # --- the author view, gated --------------------------------------------
    def diagnostics(self, session_id: str) -> DiagnosticsResponse:
        """Posteriors, selection reasoning and graph state. NOT candidate-safe.

        Refused unless `author_diagnostics_enabled`. It carries the posterior, the shortlist
        and which item the engine would have picked — enough to reverse-engineer difficulty,
        and enough for a candidate to tell how they are doing while still being measured.
        """
        self._require_diagnostics()
        from cat_engine import diagnostics as view

        session = self._session(session_id)
        return view.build(
            orchestrator=self._orchestrator(session),
            state=session.state,
            bank_id=session.bank_id,
        )

    def raw_state(self, session_id: str) -> AssessmentState:
        """The whole session object, unprojected. NOT candidate-safe.

        For replay, for an appeal, and for debugging a selection nobody can explain.
        Deliberately the engine's own type: a projected copy here would be a second
        definition that drifts from the one sessions are stored in.
        """
        self._require_diagnostics()
        return self._session(session_id).state

    def _require_diagnostics(self) -> None:
        if not self.settings.author_diagnostics_enabled:
            raise DiagnosticsDisabled(
                "author diagnostics are disabled (AUTHOR_DIAGNOSTICS_ENABLED)"
            )


def _why_unassessable(scope: ScopeManifest) -> str:
    """Name the competency at fault. A refusal nobody can act on gets retried unchanged."""
    reasons: list[str] = []
    if scope.rejected:
        reasons.append(f"not measurable in this bank: {', '.join(scope.rejected)}")
    if scope.coverage.unserved:
        reasons.append(
            f"required but measured by no active item: {', '.join(scope.coverage.unserved)}"
        )
    if scope.coverage.over_budget:
        reasons.append(
            f"{', '.join(scope.coverage.over_budget)} would require more sub-competencies "
            f"than the {scope.coverage.question_budget}-question budget can cover, so the "
            "coverage gate could never be satisfied"
        )
    if not scope.item_ids:
        reasons.append("no active item measures anything in this selection")
    return "; ".join(reasons) or "this selection cannot be assessed"


class SyncAssessmentModule:
    """`AssessmentModule` for a host that is not async.

    Every coroutine is wrapped in `asyncio.run`. Called from inside a running loop it
    RAISES rather than deadlocking — which is what `asyncio.run` does there anyway, but with
    a message that says what to do instead.

    Live interview rooms are not wrapped. A room is a duplex audio stream and there is no
    synchronous form of one; a host that wants interviews needs a loop, and `.live` is
    exposed unchanged so it can use one where it has it.
    """

    def __init__(self, config: CatConfig | None = None, **kwargs) -> None:
        self._async = AssessmentModule(config, **kwargs)

    def __getattr__(self, name: str):
        """Sync methods pass through; coroutines come back wrapped."""
        attribute = getattr(self._async, name)
        if not asyncio.iscoroutinefunction(attribute):
            return attribute

        def run(*args, **kwargs):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(attribute(*args, **kwargs))
            raise RuntimeError(
                f"SyncAssessmentModule.{name}() was called from inside a running event "
                "loop, where it would deadlock. Use AssessmentModule and await it."
            )

        run.__name__ = name
        run.__doc__ = attribute.__doc__
        return run

    def __dir__(self) -> list[str]:
        return sorted(set(dir(self._async)) | set(super().__dir__()))
