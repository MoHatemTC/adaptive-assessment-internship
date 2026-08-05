"""The Orchestrator: the controller of a multi-modality adaptive assessment.

Owns the loop and exactly two decisions — WHICH VARIABLE to probe next, and WHEN a variable
is finished. Which ITEM measures a variable belongs to the Picking Agent; how a response is
graded belongs to the Grader Agent. Keeping those apart is what lets a modality be added
without touching this file.

    seed -> fill queue -> choose variable -> present -> grade -> update -> finalise -> repeat

Stateless between calls, like both engines it drives: it takes an `AssessmentState`, returns
a new one, and holds nothing. An assessment can therefore span HTTP requests, be persisted
between them, and resume on a different worker.

WHAT IS NEVER DELEGATED

A model picks an item from a shortlist the engine ranked, and interprets code. It does not
grade multiple choice, does not write an estimate, does not choose which variable to probe,
and cannot stop an assessment. Every number in the report is computed by deterministic code
from inputs recorded beside it.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

import numpy as np

from app.config.settings import settings
from app.schemas.orchestration import (
    DEFAULT_SECONDS_BY_MODALITY,
    AssessmentReport,
    AssessmentState,
    BankItem,
    GradedResponse,
    QueuedCandidate,
    VariableReport,
    VariableState,
)
from app.services.adaptive import personfit
from app.services.adaptive.irt import ability_band
from app.services.orchestrator import budget
from app.services.orchestrator import variables as variables_module
from app.services.orchestrator.bank import UnifiedBankRepository
from app.services.orchestrator.competency import (
    affected_mains,
    main_competency,
    rollup_outcomes,
)
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.picker import pick
from app.services.orchestrator.queue import CandidateQueue
from app.services.competency_graph import config as graph_config
from app.services.competency_graph.coverage import (
    coverage_allows_convergence,
    unmeasured_required_nodes,
)
from app.services.competency_graph.evidence import (
    DEFAULT_CONFIDENCE,
    EvidenceEvent,
    evidence_id_for,
)
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.ledger import EvidenceLedger
from app.services.competency_graph.propagation import apply_direct_evidence
from app.services.competency_graph.report import build_main_report, build_node_reports
from app.services.competency_graph.state import CompetencyGraphState
from app.services.orchestrator.graph_delta import GraphDelta

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives one multi-variable, multi-modality assessment."""

    def __init__(
        self,
        bank: UnifiedBankRepository,
        grader: GraderAgent,
        *,
        graph: CompetencyGraphService | None = None,
        coverage_critical_only: bool | None = None,
        bank_id: str | None = None,
    ) -> None:
        """`graph` is the competency graph that belongs to `bank`.

        Injected rather than loaded, because a graph is only meaningful against the bank
        it was authored for: the coverage gate asks the graph what a main requires and the
        bank what measures it, so a mismatched pair marks every required node unmeasured
        and vetoes convergence forever. Left None, a deprecated fallback loads the active
        bank's graph and `_graph` checks whether it actually matches.

        `bank_id` names which bank that is, for the resolved-policy lookups. It is separate
        from `bank` because the repository does not carry its own id. Without it those
        lookups fall through to `settings.active_bank`, which is wrong wherever more than
        one bank is served at once — and `app.main` keeps one orchestrator per bank
        precisely so that it can be.
        """
        self._bank = bank
        self._grader = grader
        self._graph_service = graph
        self._coverage_critical_only = coverage_critical_only
        self._bank_id = bank_id
        self._graph_checked = False

    # --- graph and coverage policy -----------------------------------------
    def _graph(self) -> CompetencyGraphService | None:
        """The competency graph for this session's bank, or None when unusable.

        Fails OPEN on a bank/graph mismatch. A mismatched graph otherwise produces a
        silent, permanent convergence veto that presents as a measurement problem; a loud
        log plus an ungated engine is the recoverable failure.
        """
        if self._graph_service is None and not self._graph_checked:
            from app.services.orchestrator.registry import get_graph_service

            logger.warning(
                "orchestrator built without a graph — falling back to the active bank's "
                "(%s). Pass graph=registry.get_graph_service(bank_id) instead.",
                settings.active_bank,
            )
            try:
                self._graph_service = get_graph_service()
            except (KeyError, OSError, ValueError):
                logger.error("no usable competency graph for the active bank", exc_info=True)
                self._graph_service = None

        if self._graph_service is not None and not self._graph_checked:
            self._graph_checked = True
            if not self._graph_covers_bank(self._graph_service):
                self._graph_service = None
        self._graph_checked = True
        return self._graph_service

    def _graph_covers_bank(self, graph: CompetencyGraphService) -> bool:
        bank_mains = set(self._bank.variables())
        graph_mains = {
            nid for nid, node in graph.graph.nodes.items() if node.node_type == "main"
        }
        graph_mains |= {nid.split(".", 1)[0] for nid in graph.graph.nodes}
        if bank_mains & graph_mains:
            return True
        logger.error(
            "graph/bank mismatch: bank measures %s, graph declares %s — running without "
            "graph gating for this session rather than vetoing every convergence",
            sorted(bank_mains),
            sorted(graph_mains),
        )
        return False

    def _minimum_failures_to_block(self) -> int | None:
        """The bank's own blocking threshold, when it declares one.

        Read from the resolved policy rather than the graph file, so the deployment floor
        has already been applied and a bank can only ever tighten it.

        Resolved for THIS orchestrator's bank. Passing no id resolves `settings.active_bank`
        instead, so an orchestrator serving DA on a deployment whose active bank is AIE
        would silently enforce AIE's threshold — a wrong number, in the direction of
        blocking a candidate on fewer failures than their bank asked for.
        """
        try:
            from app.services.orchestrator.registry import get_propagation_policy

            resolved = get_propagation_policy(self._bank_id)
        except (ImportError, KeyError, OSError, ValueError):
            return None
        return resolved.minimum_failures_to_block if resolved is not None else None

    def coverage_critical_only(self) -> bool:
        """Whether coverage requires only the critical sub-competencies.

        Per bank, because the right answer depends on how many sub-nodes a main declares
        against how many questions the budget allows.
        """
        if self._coverage_critical_only is not None:
            return self._coverage_critical_only
        return settings.graph_coverage_critical_only

    # --- lifecycle ---------------------------------------------------------
    def begin(
        self,
        target_variables: list[str],
        intake: dict[str, int] | None = None,
        *,
        confidence: dict[str, bool] | None = None,
        rating_confident: bool = False,
    ) -> AssessmentState:
        """Open an assessment over several main competencies, seeding each from intake.

        A variable with no bank coverage is refused here rather than silently included: it
        would sit open forever, never be pickable, and prevent the session from ever
        reporting every variable finalised.

        `confidence` maps each main competency to whether the self-rating is trusted
        (narrow prior). When absent for a variable, `rating_confident` is the fallback.
        """
        available = set(self._bank.variables())
        unknown = [v for v in target_variables if v not in available]
        if unknown:
            raise ValueError(f"no bank coverage for: {unknown}")

        # Loudly, at the start, rather than as a session that runs long and is cut off
        # mid-answer. Never raises: the check is about configuration, not this candidate.
        budget.warn_if_infeasible(len(target_variables))

        intake = intake or {}
        confidence = confidence or {}
        return AssessmentState(
            session_id=f"asmt_{uuid.uuid4().hex[:12]}",
            variables={
                variable: variables_module.seed_variable(
                    variable,
                    intake.get(variable),
                    confidence.get(variable, rating_confident),
                )
                for variable in target_variables
            },
            started_at=time.time(),
        )

    # --- step 2: fill the queue -------------------------------------------
    async def fill_queue(
        self,
        state: AssessmentState,
        *,
        use_llm: bool = True,
        rng: np.random.Generator | None = None,
        only: set[str] | None = None,
    ) -> AssessmentState:
        """Top up one candidate per open main competency that lacks a queue slot.

        While a question is being answered (`presenting` is set), that competency is
        skipped so the queue holds only *other* competencies' next questions. After grading,
        pass `only=` to refill just the competencies whose estimates moved.
        """
        queue = CandidateQueue(state.queue)
        served = set(state.served_item_ids)
        exhausted: list[str] = []
        out_of_time: list[str] = []
        presenting = state.presenting.variable if state.presenting else None

        for variable in state.open_variables:
            if presenting and variable == presenting:
                continue
            if only is not None and variable not in only:
                continue
            if variable in queue:
                continue

            all_available = self._bank.shortlist(variable, exclude=set())
            pool = self._bank.shortlist(variable, exclude=served | queue.queued_item_ids())
            bank_had_items = bool(pool)
            pool, time_filtered = self._time_admissible(
                pool=pool, state=state, variable=variable
            )
            if not pool and bank_had_items and time_filtered:
                out_of_time.append(variable)
                continue
            # Kept before any constraint narrows `pool`, so the modality blueprint can be
            # computed against everything still servable rather than against whatever
            # coverage happened to leave behind.
            unconstrained_pool = list(pool)

            coverage_pending = False
            graph = self._graph() if graph_config.convergence_gate_enabled() else None
            if graph is not None:
                unmeasured = unmeasured_required_nodes(
                    graph,
                    variable,
                    measured=set(state.graph_direct_measured_nodes),
                    mastered=set(state.graph_direct_mastered_nodes),
                    not_mastered=set(state.graph_direct_not_mastered_nodes),
                    critical_only=self.coverage_critical_only(),
                )
                coverage_pool = [
                    item
                    for item in pool
                    if any(measure.variable in unmeasured for measure in item.measures)
                ]
                if coverage_pool:
                    # Coverage is a constraint, not a soft utility bonus: otherwise highly
                    # discriminating repeats can consume the budget while required nodes
                    # remain unseen. Information ranking still chooses within this pool.
                    pool = coverage_pool
                    coverage_pending = True
                    logger.debug(
                        "coverage-first selection for %s: missing=%s candidates=%d",
                        variable,
                        sorted(unmeasured),
                        len(pool),
                    )
            probe_pool = self._unblocking_probe_pool(
                pool=pool, state=state, variable=variable
            )
            probing = False
            if not coverage_pending and probe_pool:
                pool = probe_pool
                probing = True
                logger.info(
                    "unblocking probe for %s: %d candidates measuring a blocked node",
                    variable,
                    len(pool),
                )

            # THE BLUEPRINT IS NOT PREEMPTED. Coverage and the blueprint are orthogonal:
            # coverage decides WHICH node must be measured, the blueprint WHICH modality
            # measures it. The deficit is therefore computed against the UNNARROWED pool
            # and intersected with whatever coverage or a probe has already chosen, so both
            # hold whenever the bank allows it.
            #
            # WHY THIS CHANGED. Previously an unblocking probe skipped the blueprint check
            # outright and the deficit was computed against the already-narrowed pool.
            # Measured, that put modality compliance at 0.714 in the full-graph arm against
            # 0.979 for Approach B: a probe fires every fifth observation, and on a
            # graph-heavy session it fires often enough to starve the code and voice
            # minimums for the rest of the assessment. An assessment that silently stops
            # being mixed is a different instrument, not a faster one.
            #
            # `_modality_deficit_pool` only returns anything once the deficit is close to
            # unmeetable, so when it fires and the intersection is empty, the blueprint
            # wins: a required modality that can still be served is worth more than one
            # more coverage node, because the coverage node has later chances and the
            # modality slot does not.
            modality_pending = False
            deficit_pool = self._modality_deficit_pool(
                pool=unconstrained_pool, state=state, variable=variable, available=all_available
            )
            if deficit_pool:
                deficit_ids = {item.item_id for item in deficit_pool}
                both = [item for item in pool if item.item_id in deficit_ids]
                if both:
                    pool = both
                    modality_pending = True
                elif coverage_pending or probing:
                    logger.info(
                        "%s: blueprint and %s cannot both hold — serving the owed modality",
                        variable,
                        "coverage" if coverage_pending else "an unblocking probe",
                    )
                    pool = deficit_pool
                    modality_pending = True
                    coverage_pending = False
                    probing = False
                else:
                    pool = deficit_pool
                    modality_pending = True

            corroboration_pending = False
            if not coverage_pending and not probing and not modality_pending and all_available:
                administered_difficulties = [
                    float(served_item.cat.b)
                    for served_item_id in state.variables[variable].served_item_ids
                    if (served_item := self._bank.get(served_item_id)) is not None
                ]
                max_available_b = max(float(item.cat.b) for item in all_available)
                if not variables_module.difficulty_is_corroborated(
                    state.variables[variable],
                    administered_difficulties,
                    maximum_available_difficulty=max_available_b,
                ):
                    required_b = variables_module.required_corroboration_difficulty(
                        state.variables[variable],
                        maximum_available_difficulty=max_available_b,
                    )
                    corroboration_pool = [
                        item for item in pool if float(item.cat.b) >= required_b - 1e-9
                    ]
                    if corroboration_pool:
                        pool = corroboration_pool
                        corroboration_pending = True
                        logger.debug(
                            "corroboration-first selection for %s: b>=%.2f candidates=%d",
                            variable,
                            required_b,
                            len(pool),
                        )
            challenge_b = (
                None
                if coverage_pending or corroboration_pending or probing or modality_pending
                else variables_module.upper_challenge_difficulty(
                    state.variables[variable]
                )
            )
            if challenge_b is not None:
                challenge_ceiling = (
                    challenge_b + settings.cat_challenge_difficulty_window
                )
                challenge_pool = [
                    item
                    for item in pool
                    if challenge_b <= item.cat.b <= challenge_ceiling
                ]
                if not challenge_pool:
                    # Sparse banks may have no item in the ideal window. Prefer any
                    # harder item over silently returning to easy local confirmation.
                    challenge_pool = [item for item in pool if item.cat.b >= challenge_b]
                if challenge_pool:
                    logger.debug(
                        "upper-band challenge for %s: %d candidates near b %.2f",
                        variable,
                        len(challenge_pool),
                        challenge_b,
                    )
                    pool = challenge_pool
            utility_modifiers = self._graph_utility_modifiers(
                pool=pool, state=state, variable=variable
            )
            candidate = await pick(
                pool,
                state.variables[variable],
                variable,
                use_llm=use_llm,
                rng=rng,
                utility_modifiers_by_item_id=utility_modifiers,
            )
            if candidate is None:
                exhausted.append(variable)
                continue
            queue.put(candidate)

        updated = dict(state.variables)
        for variable in exhausted:
            logger.info("no items remain for %s — finalising as exhausted", variable)
            updated[variable] = variables_module.mark_exhausted(updated[variable])
            queue.release(variable)
        for variable in out_of_time:
            logger.info("no item fits the remaining time for %s — finalising", variable)
            updated[variable] = variables_module.mark_time_exhausted(updated[variable])
            queue.release(variable)

        return state.model_copy(update={"queue": queue.pending(), "variables": updated})

    def _time_admissible(
        self, *, pool: list[BankItem], state: AssessmentState, variable: str
    ) -> tuple[list[BankItem], bool]:
        """Items that still fit the clock. Returns (pool, filtered_anything).

        RESERVING UP FRONT is the point. Without it a session spends its last nine minutes
        on a tenth multiple-choice item and then has no room for the code item its own
        blueprint requires. Every other open main's unmet minimum is held back before this
        main is allowed to spend.

        The flag is reported separately because an empty pool means two different things:
        the BANK ran out, or the CLOCK did. Reporting a time stop as `bank_exhausted`
        names a constraint that was not the one that bound.
        """
        if not settings.orchestrator_time_aware_selection_enabled or not state.started_at:
            return pool, False

        limit_seconds = settings.orchestrator_time_limit_minutes * 60.0
        remaining = (
            limit_seconds
            - state.elapsed_minutes * 60.0
            - settings.orchestrator_time_reserve_seconds
        )

        minimums = settings.modality_minimums()
        reserved = 0.0
        for other in state.open_variables:
            if other == variable:
                continue
            served = {
                item.modality
                for item_id in state.variables[other].served_item_ids
                if (item := self._bank.get(item_id)) is not None
            }
            for modality, required in minimums.items():
                if required > 0 and modality not in served:
                    reserved += DEFAULT_SECONDS_BY_MODALITY.get(modality, 0.0)

        budget = remaining - reserved
        admissible = [item for item in pool if item.expected_seconds <= budget]
        if not admissible and pool:
            logger.info(
                "no item fits %s's remaining budget (%.0fs after reserving %.0fs)",
                variable,
                remaining,
                reserved,
            )
        return admissible, len(admissible) != len(pool)

    def _modality_deficit_pool(
        self,
        *,
        pool: list[BankItem],
        state: AssessmentState,
        variable: str,
        available: list[BankItem] | None = None,
    ) -> list[BankItem]:
        """Items in a modality this main still owes, when time is running out to get one.

        WHY A CONSTRAINT AND NOT A BONUS. Measured on the live bank, ranking by
        information per minute puts MCQ ahead of code by about 6.6x. No soft weight
        survives that ratio, so a "mixed" assessment quietly becomes a multiple-choice
        test — which is not the instrument anyone agreed to, and is a different construct.
        The existing coverage constraint makes the same argument for the same reason.

        Triggered off the OBSERVATION FLOOR rather than the question cap, so the minimums
        are met before any convergence stop can fire. At a floor of six and two minimums
        that is the last two picks: four free information-per-minute choices, then the
        code item, then the voice item.
        """
        minimums = settings.modality_minimums()
        if not minimums:
            return []

        # What this main has been ASKED, not what moved its estimate. Counting only
        # scoring responses creates a feedback loop: an unscorable modality — a sandbox
        # that is down, a microphone that fails — never clears its deficit, so the
        # blueprint re-serves it for the rest of the session and measures nothing.
        served_modalities: dict[str, int] = {}
        for item_id in state.administered_by_variable.get(variable, []):
            served = self._bank.get(item_id)
            if served is not None:
                served_modalities[served.modality] = (
                    served_modalities.get(served.modality, 0) + 1
                )

        unmet = {
            modality: required - served_modalities.get(modality, 0)
            for modality, required in minimums.items()
            if served_modalities.get(modality, 0) < required
        }
        # A minimum the bank cannot supply is not a deficit, it is a bank fact. Enforcing
        # one is how a single missing modality starves every other pick for the rest of
        # the session: the constraint can never be satisfied, so it never releases.
        if available is not None:
            supplied = {item.modality for item in available}
            unmeetable = sorted(set(unmet) - supplied)
            if unmeetable:
                logger.debug(
                    "%s cannot supply %s — that minimum is waived for this bank",
                    variable,
                    unmeetable,
                )
            unmet = {m: n for m, n in unmet.items() if m in supplied}
        if not unmet:
            return []

        # The deficit is enforced for the WHOLE session, not only as the observation floor
        # approaches. Measured on this bank, information per minute favours multiple
        # choice by about 6.6x, so an unenforced slot is never spent on anything else: a
        # session that merely *could* have mixed reliably does not. The only question is
        # WHEN to spend the slot, and spending it late buys a better-targeted item because
        # the estimate has moved by then.
        #
        # `urgency` is how many slots remain before the deficit becomes unmeetable. Below
        # the floor there is still room to choose freely; at or past it, the constraint
        # binds every pick until the mix is satisfied.
        observations = state.variables[variable].observations
        slots_to_floor = settings.cat_precision_min_questions - observations
        slots_to_cap = settings.cat_max_questions - observations
        owed = sum(unmet.values())
        if slots_to_floor > owed and slots_to_cap > owed + settings.cat_precision_min_questions:
            return []

        deficit_pool = [item for item in pool if item.modality in unmet]
        if deficit_pool:
            logger.info(
                "modality blueprint for %s: owes %s, %d slots to the floor, %d to the cap",
                variable,
                unmet,
                slots_to_floor,
                slots_to_cap,
            )
        return deficit_pool

    def _graph_utility_modifiers(
        self, *, pool: list[BankItem], state: AssessmentState, variable: str
    ) -> dict[str, float] | None:
        """Per-item selection penalties and bonuses from graph state.

        PENALTIES, NOT EXCLUSIONS (review C13/A4). These two classes used to be dropped
        from the pool outright:

          - an item whose measured node has a blocked prerequisite ancestor
          - an item all of whose measured nodes are non-critical and already mastered

        Both are usually the right item to skip, and occasionally the most informative one
        available. Hard exclusion starves a thin bank and pushes sessions toward the
        bank-exhausted stop; worse, an item that is never served can never disprove the
        block, which is precisely why contradiction recovery could not fire. A large
        penalty says "almost never" without saying "never".
        """
        modifiers: dict[str, float] = {}
        contradicted = set(state.graph_contradicted_nodes)
        blocked = set(state.graph_blocked_nodes)
        mastered = set(state.graph_direct_mastered_nodes)

        graph = self._graph() if graph_config.filtering_enabled() else None
        if graph is not None and (blocked or mastered):
            for item in pool:
                measured = [m.variable for m in item.measures if m.variable in graph.graph.nodes]
                if not measured:
                    continue
                if any(blocked & graph.ancestors(nid, include_self=True) for nid in measured):
                    modifiers[item.item_id] = (
                        modifiers.get(item.item_id, 0.0) + settings.graph_blocked_penalty
                    )
                    continue
                redundant = all(
                    not graph.graph.nodes[nid].critical and nid in mastered
                    for nid in measured
                )
                if redundant:
                    modifiers[item.item_id] = (
                        modifiers.get(item.item_id, 0.0)
                        + settings.graph_mastered_noncritical_penalty
                    )

        if graph_config.utility_enabled() and contradicted:
            for item in pool:
                if any(m.variable in contradicted for m in item.measures):
                    modifiers[item.item_id] = (
                        modifiers.get(item.item_id, 0.0)
                        + settings.graph_contradiction_bonus
                    )

        # SHARED-MAIN GAIN. Ranking is per variable, so an item that also evidences
        # another main competency the session is still measuring delivers value this
        # variable's information score cannot see. Without the correction the sharing is
        # real but never chosen: measured on this bank, evidence reuse sat at 0.98 main
        # updates per item — the machinery present and doing nothing.
        #
        # It is a bonus and not a constraint because the extra evidence is genuinely
        # partial: a secondary loading of 0.5 is half a measurement, not a second one.
        if graph_config.utility_enabled() and settings.graph_shared_main_gain > 0:
            open_mains = {
                v for v in state.open_variables if v != variable
            }
            for item in pool:
                extra = {
                    main_competency(measure.variable) for measure in item.measures
                } & open_mains
                if extra:
                    modifiers[item.item_id] = (
                        modifiers.get(item.item_id, 0.0)
                        + settings.graph_shared_main_gain * len(extra)
                    )

        return modifiers or None

    def _unblocking_probe_pool(
        self, *, pool: list[BankItem], state: AssessmentState, variable: str
    ) -> list[BankItem]:
        """Items that would re-test a blocked node, when a probe is due.

        The graph's blocked-node claim is a prediction, and a prediction nothing ever
        tests is not falsifiable. A probe at a fixed low rate is the only way a false
        block becomes visible in production rather than in an offline study — which is
        what makes the false-blocking rate measurable at all.

        Deterministic on the observation count, not random: this design commits to
        replayable sessions, and an RNG here would make two runs of the same responses
        disagree about which items were served.
        """
        blocked = set(state.graph_blocked_nodes)
        if not blocked or settings.graph_unblocking_probe_period <= 0:
            return []
        observations = state.variables[variable].observations
        if observations == 0 or observations % settings.graph_unblocking_probe_period:
            return []
        return [item for item in pool if any(m.variable in blocked for m in item.measures)]

    # --- step 3: choose the variable --------------------------------------
    def choose_variable(self, state: AssessmentState) -> str | None:
        """The least-measured open variable that has a candidate ready.

        Lowest certainty first, ties broken by fewest observations then by name so the
        order is deterministic and a session can be replayed. Certainty rather than raw
        standard error because it is the number the report is written in, and because it is
        comparable across variables regardless of how each was primed.
        """
        candidates = [
            variable
            for variable in state.open_variables
            if variable in state.queue
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda v: (
                variables_module.certainty(state.variables[v]),
                state.variables[v].observations,
                v,
            ),
        )

    def ensure_presenting(self, state: AssessmentState) -> AssessmentState:
        """Move the chosen queue candidate into `presenting`, emptying that queue slot."""
        if state.presenting is not None:
            return state
        variable = self.choose_variable(state)
        if variable is None:
            return state
        queue = CandidateQueue(state.queue)
        candidate = queue.take(variable)
        if candidate is None:
            return state
        return state.model_copy(
            update={
                "presenting": candidate,
                "queue": queue.pending(),
                "presenting_since": time.time(),
            }
        )

    def next_item(self, state: AssessmentState) -> tuple[BankItem, QueuedCandidate] | None:
        """The item currently being presented. Call `ensure_presenting` first."""
        if state.presenting is None:
            return None
        candidate = state.presenting
        item = self._bank.get(candidate.item_id)
        if item is None:  # pragma: no cover — the bank changed under a live session
            logger.error("queued item %s vanished from the bank", candidate.item_id)
            return None
        return item, candidate

    # --- graph evidence ----------------------------------------------------
    def _apply_graph_evidence(
        self, state: AssessmentState, item: BankItem, graded: GradedResponse
    ) -> GraphDelta:
        """Apply every outcome of one response to the graph, all or nothing.

        COMPUTE, THEN MERGE. Every consequence accumulates in a local delta and only
        reaches the session state once all of them succeeded. The previous version marked
        an evidence id as processed BEFORE doing the work, inside a broad exception
        handler — so a failure on the second of three outcomes left the first applied, the
        second permanently marked as done, and no way to retry it.

        Returns an empty delta when the graph is off or unusable. A graph problem must
        cost the candidate nothing.
        """
        delta = GraphDelta.restore(state)
        graph = self._graph() if graph_config.graph_enabled() else None
        if graph is None:
            return delta

        config = graph_config.propagation_config_from_settings(
            minimum_failures_to_block=self._minimum_failures_to_block()
        )
        # Distinguishes re-administrations of one item. Computed before `served` grows.
        attempt_no = state.served_item_ids.count(item.item_id) + 1
        now = datetime.now(timezone.utc).isoformat()

        seen_variables: set[str] = set()
        events: list[EvidenceEvent] = []
        for outcome in graded.outcomes:
            target_node = str(outcome.get("variable") or "")
            if not target_node:
                continue
            if target_node in seen_variables:
                # Two outcomes for one variable would collide on the evidence id and the
                # second would be silently dropped as a duplicate. Refuse instead: the
                # grader is expected to aggregate per competency before it gets here.
                raise ValueError(
                    f"{item.item_id}: two outcomes for {target_node!r} in one response"
                )
            seen_variables.add(target_node)
            modality = str(outcome.get("modality") or item.modality)
            events.append(
                EvidenceEvent(
                    evidence_id=evidence_id_for(
                        session_id=state.session_id,
                        item_id=item.item_id,
                        attempt_no=attempt_no,
                        modality=modality,
                        target_node=target_node,
                    ),
                    session_id=state.session_id,
                    item_id=item.item_id,
                    modality=modality,
                    target_node=target_node,
                    score=float(outcome.get("score", 0.0)),
                    weight=float(outcome.get("weight", 1.0)),
                    confidence=float(outcome.get("confidence", DEFAULT_CONFIDENCE)),
                    source=str(outcome.get("source_item_id") or item.item_id),
                    evidence_kind="direct",
                    directly_tested=True,
                )
            )

        try:
            graph_state = CompetencyGraphState.from_dict(state.graph_node_states)
            graph_state.ensure_nodes(set(graph.graph.nodes))
            ledger = EvidenceLedger.from_ids(state.graph_processed_evidence_ids)

            results = []
            for event in events:
                if ledger.contains(event.evidence_id):
                    continue
                results.append(
                    apply_direct_evidence(
                        graph,
                        graph_state,
                        event,
                        ledger=ledger,
                        config=config,
                        now=now,
                        item_minimum_confidence=item.minimum_success_confidence,
                    )
                )
        except Exception:  # noqa: BLE001 — the graph never fails an assessment
            logger.exception("graph evidence discarded for %s", item.item_id)
            return delta

        delta.absorb(
            graph=graph,
            graph_state=graph_state,
            ledger=ledger,
            results=results,
            session_variables=set(state.variables),
        )
        return delta

    # --- steps 5-7: grade, update, finalise --------------------------------
    def record_response(
        self, state: AssessmentState, item: BankItem, response: object
    ) -> tuple[AssessmentState, GradedResponse]:
        """Grade one response, fold it into every main competency it evidences, finalise.

        A code submission may update several main competencies. Queue slots for every
        competency touched are cleared and must be refilled — those picks were made against
        stale estimates.
        """
        graded = self._grader.grade(item, response)

        session_variables = set(state.variables)
        touched = affected_mains(item, session_variables)
        graph_delta = self._apply_graph_evidence(state, item, graded)

        queue = CandidateQueue(state.queue)
        for variable in touched | graph_delta.selection_affected_mains:
            queue.release(variable)

        served = [*state.served_item_ids, item.item_id]
        # An item is administered FOR the variable it was presented for, and it evidences
        # every main it measures. Both are recorded: an unscorable response is still a
        # question the candidate answered, and the blueprint must not ask again for a
        # modality it already spent a slot on.
        administered = {k: list(v) for k, v in state.administered_by_variable.items()}
        asked_for = set(touched)
        if state.presenting is not None:
            asked_for.add(state.presenting.variable)
        for variable in asked_for:
            entries = administered.setdefault(variable, [])
            if item.item_id not in entries:
                entries.append(item.item_id)

        updated = dict(state.variables)

        # ONE path from a graded response to a posterior, and only direct evidence is on
        # it. Inferred consequences reach selection and the report; they never multiply a
        # likelihood, because a deduction FROM a response is not a second response.
        rolled = rollup_outcomes(graded.outcomes, session_variables)

        aberrant = list(state.aberrant_responses)
        for outcome in rolled:
            if outcome.variable not in updated:
                continue
            if outcome.moves_the_estimate:
                # BEFORE the update: posterior-predictive means predictive of an
                # observation not yet absorbed. Computing it after would make it
                # self-referential and hide exactly the responses it exists to surface.
                fit = personfit.residual(
                    variable=outcome.variable,
                    item_id=item.item_id,
                    posterior=np.asarray(updated[outcome.variable].posterior, dtype=float),
                    a=item.cat.a,
                    b=item.cat.b,
                    c=item.cat.c,
                    score=outcome.score,
                    weight=outcome.weight,
                )
                if fit.aberrant:
                    logger.info(
                        "aberrant response on %s: scored %.2f where %.2f was expected (z=%.2f)",
                        outcome.variable, fit.score, fit.expected, fit.z,
                    )
                    aberrant.append(fit.as_dict())
            updated[outcome.variable] = variables_module.apply_outcome(
                updated[outcome.variable],
                outcome,
                item.cat.a,
                item.cat.b,
                item.cat.c,
                item.item_id,
            )

        # Required nodes a main was finalised without, recorded rather than inferred
        # later from the absence of a set membership.
        waived: dict[str, list[str]] = dict(state.graph_waived_nodes)

        # Convergence after every question, for every competency in the session.
        for variable, variable_state in list(updated.items()):
            if variable_state.finalised:
                continue
            all_variable_items = self._bank.shortlist(variable, exclude=set())
            remaining = sum(
                1 for candidate in all_variable_items if candidate.item_id not in set(served)
            )
            maximum_available_difficulty = (
                max(float(candidate.cat.b) for candidate in all_variable_items)
                if all_variable_items
                else None
            )
            administered_difficulties = []
            for served_item_id in variable_state.served_item_ids:
                served_item = self._bank.get(served_item_id)
                if served_item is not None:
                    administered_difficulties.append(float(served_item.cat.b))
            candidate_state = variables_module.evaluate_finalisation(
                variable_state,
                remaining,
                administered_difficulties=administered_difficulties,
                maximum_available_difficulty=maximum_available_difficulty,
            )
            gate_graph = (
                self._graph()
                if (
                    candidate_state.finalised
                    and candidate_state.converged
                    and graph_config.convergence_gate_enabled()
                )
                else None
            )
            if gate_graph is not None:
                if not coverage_allows_convergence(
                    gate_graph,
                    variable,
                    measured=graph_delta.measured,
                    mastered=graph_delta.mastered,
                    not_mastered=graph_delta.not_mastered,
                    critical_only=self.coverage_critical_only(),
                ):
                    missing = unmeasured_required_nodes(
                        gate_graph,
                        variable,
                        measured=graph_delta.measured,
                        mastered=graph_delta.mastered,
                        not_mastered=graph_delta.not_mastered,
                        critical_only=self.coverage_critical_only(),
                    )
                    if remaining <= 0:
                        candidate_state = variable_state.model_copy(
                            update={
                                "finalised": True,
                                "stop_reason": "bank_exhausted",
                                "converged": False,
                            }
                        )
                        waived[variable] = sorted(missing)
                    elif variable_state.observations >= settings.cat_max_questions:
                        # NOT `question_budget`. Measurement DID converge; the graph
                        # refused to certify it because required nodes were never seen.
                        # Reporting that as "ran out of questions" describes a stop the
                        # session did not make, and makes the deadlock rate — a stated
                        # rollout target — unmeasurable, because the two are then
                        # indistinguishable in the record.
                        candidate_state = variable_state.model_copy(
                            update={
                                "finalised": True,
                                "stop_reason": "graph_gates_waived",
                                "converged": False,
                            }
                        )
                        waived[variable] = sorted(missing)
                        logger.warning(
                            "%s finalised with coverage waived — unmeasured=%s",
                            variable,
                            sorted(missing),
                        )
                    else:
                        logger.info(
                            "graph_coverage gate held %s open — unmeasured=%s",
                            variable,
                            sorted(missing),
                        )
                        continue

            updated[variable] = candidate_state
            if updated[variable].finalised:
                queue.release(variable)
                logger.info(
                    "%s finalised: %s (converged=%s)",
                    variable, updated[variable].stop_reason, updated[variable].converged,
                )

        elapsed = (time.time() - state.started_at) / 60.0 if state.started_at else 0.0
        item_seconds = dict(state.item_seconds)
        if state.presenting_since:
            item_seconds[item.item_id] = round(time.time() - state.presenting_since, 2)
        # What this item actually delivered, against what selection predicted it would.
        # The only way the expected-weight table stops being an assertion.
        realized = dict(state.realized_weight_by_item)
        if rolled:
            realized[item.item_id] = round(max(o.weight for o in rolled), 4)
        return (
            state.model_copy(
                update={
                    "variables": updated,
                    "queue": queue.pending(),
                    "presenting": None,
                    "served_item_ids": served,
                    "items_administered": state.items_administered + 1,
                    "elapsed_minutes": round(elapsed, 3),
                    "administered_by_variable": administered,
                    "item_seconds": item_seconds,
                    "realized_weight_by_item": realized,
                    "presenting_since": 0.0,
                    "aberrant_responses": aberrant,
                    "graph_waived_nodes": waived,
                    **graph_delta.as_state_update(),
                }
            ),
            graded,
        )

    async def after_response(
        self,
        state: AssessmentState,
        item: BankItem,
        *,
        use_llm: bool = True,
        rng: np.random.Generator | None = None,
    ) -> AssessmentState:
        """Refill queue slots for competencies whose estimates just moved."""
        touched = affected_mains(item, set(state.variables))
        if graph_config.filtering_enabled() and state.graph_last_affected_mains:
            touched = set(touched) | set(state.graph_last_affected_mains)
        updated = await self.fill_queue(state, use_llm=use_llm, rng=rng, only=touched)
        if graph_config.filtering_enabled():
            updated = updated.model_copy(update={"graph_last_affected_mains": []})
        return updated

    # --- step 8: termination ------------------------------------------------
    def should_stop(self, state: AssessmentState) -> tuple[bool, str]:
        """Whether the whole assessment is over, and why.

        Budget stops are reported as such and never as convergence: running out of items or
        time is not the same as knowing a candidate's level, and a report that conflates
        them claims a measurement nobody made.
        """
        if not state.open_variables:
            return True, "all_variables_finalised"
        # Time first: it is the constraint that actually binds, and a session that hit the
        # clock must not be reported as having exhausted a question budget it never
        # approached.
        if state.started_at and state.elapsed_minutes >= settings.orchestrator_time_limit_minutes:
            return True, "time_limit"
        if state.items_administered >= settings.orchestrator_max_items:
            return True, "item_budget"
        if state.presenting is None and not state.queue:
            return True, "no_candidates_available"
        return False, ""

    # --- reporting ----------------------------------------------------------
    def summarise(self, state: AssessmentState, stop_reason: str = "") -> AssessmentReport:
        """The end-of-assessment report. Nothing here is inferred."""
        by_item = {i.item_id: i for i in self._bank.all_items()}

        def modalities_for(variable: str) -> list[str]:
            """Every modality this main was ASKED in.

            From the administered record rather than the scoring one: a voice answer that
            failed to transcribe was still a voice question the candidate sat, and a
            report that omits it describes an assessment that did not happen.
            """
            return sorted(
                {
                    by_item[item_id].modality
                    for item_id in state.administered_by_variable.get(variable, [])
                    if item_id in by_item
                }
            )

        # The master switch, checked here as it is at every other entry point. It used to
        # be absent from this one, so a session run with COMPETENCY_GRAPH_ENABLED=false
        # still loaded the graph, computed coverage, and emitted a full graph block in the
        # report. A disabled feature that keeps running in production reporting is the
        # exact failure the switch was added to prevent.
        graph = self._graph() if graph_config.graph_enabled() else None
        aberrant_by_variable: dict[str, int] = {}
        for record in state.aberrant_responses:
            key = str(record.get("variable", ""))
            aberrant_by_variable[key] = aberrant_by_variable.get(key, 0) + 1

        reports: list[VariableReport] = []
        for variable, variable_state in sorted(state.variables.items()):
            level, band = ability_band(variable_state.theta_hat)
            measured = variable_state.observations > 0
            bands = variables_module.band_probability(variable_state)
            most_probable = max(bands, key=lambda k: bands[k]) if bands else None

            unmeasured: list[str] = []
            if graph is not None:
                unmeasured = sorted(
                    unmeasured_required_nodes(
                        graph,
                        variable,
                        measured=set(state.graph_direct_measured_nodes),
                        mastered=set(state.graph_direct_mastered_nodes),
                        not_mastered=set(state.graph_direct_not_mastered_nodes),
                        critical_only=self.coverage_critical_only(),
                    )
                )

            precision_index = round(variables_module.certainty(variable_state), 1)
            reports.append(
                VariableReport(
                    variable=variable,
                    theta_hat=round(variable_state.theta_hat, 4),
                    standard_error=round(variable_state.standard_error, 4),
                    precision_index_pct=precision_index,
                    certainty_pct=precision_index,
                    level=level if measured else None,
                    band=band if measured else "Not assessed",
                    p_reported_band=round(bands.get(level, 0.0), 4) if measured else 0.0,
                    band_probability=round(max(bands.values()), 4) if bands else 0.0,
                    most_probable_band=most_probable if measured else None,
                    credible_interval_95=(
                        tuple(round(x, 3) for x in variables_module.posterior_interval(variable_state))
                        if measured
                        else None
                    ),
                    aberrant_response_count=aberrant_by_variable.get(variable, 0),
                    graph_coverage_satisfied=not unmeasured,
                    graph_unmeasured_nodes=unmeasured,
                    observations=variable_state.observations,
                    finalised=variable_state.finalised,
                    converged=variable_state.converged,
                    stop_reason=variable_state.stop_reason,
                    modalities_used=modalities_for(variable),
                )
            )

        graph_report: dict = {}
        if graph is not None:
            graph_state = CompetencyGraphState.from_dict(state.graph_node_states)
            graph_report = {
                "nodes": [n.as_dict() for n in build_node_reports(graph, graph_state)],
                "mains": [
                    build_main_report(
                        graph,
                        graph_state,
                        report.variable,
                        unmeasured_nodes=report.graph_unmeasured_nodes,
                        preview_inferred=state.graph_preview_inferred_nodes,
                        preview_blocked=set(state.graph_preview_blocked_nodes),
                    ).as_dict()
                    for report in reports
                ],
                "contradictions": list(state.graph_contradictions),
                "waived_nodes": dict(state.graph_waived_nodes),
                "unblocking_probes": list(state.graph_unblocking_probes),
                # The unvalidated-edge forecast. Present in every report, whatever the
                # enforcement flags say — a record that only appears once a feature is
                # enabled cannot be used to decide whether to enable it.
                "preview_inferred_nodes": dict(state.graph_preview_inferred_nodes),
                "preview_blocked_nodes": list(state.graph_preview_blocked_nodes),
                "preview_refuted_nodes": list(state.graph_preview_refuted_nodes),
            }

        return AssessmentReport(
            session_id=state.session_id,
            items_administered=state.items_administered,
            variables=reports,
            all_finalised=not state.open_variables,
            stop_reason=stop_reason,
            graph=graph_report,
            aberrant_responses=list(state.aberrant_responses),
            seconds_by_item=dict(state.item_seconds),
        )
