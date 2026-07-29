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

import numpy as np

from app.config.settings import settings
from app.schemas.orchestration import (
    AssessmentReport,
    AssessmentState,
    BankItem,
    GradedResponse,
    QueuedCandidate,
    VariableReport,
    VariableState,
)
from app.services.adaptive.irt import ability_band
from app.services.orchestrator import variables as variables_module
from app.services.orchestrator.bank import UnifiedBankRepository
from app.services.orchestrator.competency import affected_mains, rollup_outcomes
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.picker import pick
from app.services.orchestrator.queue import CandidateQueue
from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.coverage import (
    coverage_allows_convergence,
    unmeasured_required_nodes,
)
from app.services.competency_graph.evidence import EvidenceEvent
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.propagation import PropagationConfig, shadow_apply_events
from app.services.competency_graph.rollup import graph_rollup_outcomes
from app.services.competency_graph.rollup import infer_upward_inferred_nodes

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives one multi-variable, multi-modality assessment."""

    def __init__(self, bank: UnifiedBankRepository, grader: GraderAgent) -> None:
        self._bank = bank
        self._grader = grader
        self._graph_service: CompetencyGraphService | None = None

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
            pool = self._filter_pool_by_competency_graph(pool=pool, state=state)
            coverage_pending = False
            if settings.graph_convergence_gate_enabled:
                if self._graph_service is None:
                    g = load_default_competency_graph()
                    self._graph_service = CompetencyGraphService(g)
                unmeasured = unmeasured_required_nodes(
                    self._graph_service,
                    variable,
                    measured=set(state.graph_direct_measured_nodes),
                    mastered=set(state.graph_direct_mastered_nodes),
                    not_mastered=set(state.graph_direct_not_mastered_nodes),
                    critical_only=settings.graph_coverage_critical_only,
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
            corroboration_pending = False
            if not coverage_pending and all_available:
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
                if coverage_pending or corroboration_pending
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
            utility_modifiers: dict[str, float] | None = None
            if settings.graph_utility_enabled and state.graph_contradicted_nodes:
                contradicted = set(state.graph_contradicted_nodes)
                # Give a small utility presence to items that touch any contradicted node.
                utility_modifiers = {
                    item.item_id: (
                        0.25
                        if any(m.variable in contradicted for m in item.measures)
                        else 0.0
                    )
                    for item in pool
                }
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

        return state.model_copy(update={"queue": queue.pending(), "variables": updated})

    def _filter_pool_by_competency_graph(
        self,
        *,
        pool: list[BankItem],
        state: AssessmentState,
    ) -> list[BankItem]:
        """Filter candidates so they don't violate blocked prerequisites.

        Phase C only uses persisted *blocking* state (no inferred-upward mastery).
        """
        if not settings.graph_filtering_enabled:
            return pool

        if self._graph_service is None:
            g = load_default_competency_graph()
            self._graph_service = CompetencyGraphService(g)

        blocked = set(state.graph_blocked_nodes)
        direct_mastered = set(state.graph_direct_mastered_nodes)

        eligible: list[BankItem] = []
        for item in pool:
            measured_nodes = [m.variable for m in item.measures]

            # 1) Exclude items whose measured nodes have blocked prerequisite ancestors.
            blocked_prereq = False
            for nid in measured_nodes:
                if nid not in self._graph_service.graph.nodes:
                    continue
                ancestors = self._graph_service.ancestors(nid, include_self=True)
                if blocked & ancestors:
                    blocked_prereq = True
                    break
            if blocked_prereq:
                continue

            # 2) Skip redundant mastered noncritical nodes.
            if measured_nodes:
                measured_in_graph = [nid for nid in measured_nodes if nid in self._graph_service.graph.nodes]
                if measured_in_graph:
                    redundant_for_item = True
                    for nid in measured_in_graph:
                        node = self._graph_service.graph.nodes[nid]
                        if node.critical:
                            redundant_for_item = False
                            break
                        if nid not in direct_mastered:
                            redundant_for_item = False
                            break
                    if redundant_for_item:
                        continue

            eligible.append(item)

        return eligible

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
            update={"presenting": candidate, "queue": queue.pending()}
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

        shadow_direct_mastered = set(
            getattr(state, "graph_shadow_direct_mastered_nodes", [])
        )
        shadow_inferred_mastered = set(
            getattr(state, "graph_shadow_inferred_mastered_nodes", [])
        )
        shadow_direct_not_mastered = set(
            getattr(state, "graph_shadow_direct_not_mastered_nodes", [])
        )
        shadow_blocked = set(getattr(state, "graph_shadow_blocked_nodes", []))
        shadow_contradicted = set(
            getattr(state, "graph_shadow_contradicted_nodes", [])
        )

        # Graph shadow update is analysis-only: it must never alter CAT posterior
        # math, picker ranking, queue contents, or convergence decisions.
        if settings.graph_shadow_mode:
            try:
                if self._graph_service is None:
                    g = load_default_competency_graph()
                    self._graph_service = CompetencyGraphService(g)

                events: list[EvidenceEvent] = []
                for o in graded.outcomes:
                    modality = str(o.get("modality") or item.modality)
                    variable = str(o.get("variable") or "")
                    evidence_id = (
                        f"{state.session_id}:{item.item_id}:{modality}:{variable}"
                    )
                    events.append(
                        EvidenceEvent(
                            evidence_id=evidence_id,
                            session_id=state.session_id,
                            item_id=item.item_id,
                            modality=modality,
                            target_node=variable,
                            score=float(o.get("score", 0.0)),
                            weight=float(o.get("weight", 1.0)),
                            confidence=float(o.get("confidence", 1.0)),
                            source=str(o.get("source_item_id") or ""),
                            evidence_kind="direct",
                            source_node=None,
                            propagation_distance=0,
                            directly_tested=True,
                            rubric_criterion_id=None,
                            evaluator_version=None,
                            metadata={},
                        )
                    )

                updates = shadow_apply_events(
                    self._graph_service,
                    events,
                    config=PropagationConfig(),
                )
                if updates:
                    cfg = PropagationConfig()
                    success_targets = {
                        event.target_node
                        for event in events
                        if event.score >= cfg.strong_success_threshold
                        and event.confidence >= cfg.minimum_propagation_confidence
                        and event.weight > 0.0
                    }
                    failure_targets = {
                        event.target_node
                        for event in events
                        if event.score <= cfg.strong_failure_threshold
                        and event.confidence >= cfg.downward_block_confidence
                        and event.weight > 0.0
                    }
                    shadow_direct_mastered.update(success_targets)
                    shadow_direct_not_mastered.difference_update(success_targets)
                    shadow_direct_not_mastered.update(failure_targets)
                    shadow_inferred_mastered.update(
                        inferred.target_node
                        for update in updates
                        for inferred in update.inferred_updates
                    )
                    # Node status is exclusive: direct evidence supersedes inferred
                    # mastery, including direct negative evidence.
                    shadow_inferred_mastered -= (
                        shadow_direct_mastered | shadow_direct_not_mastered
                    )
                    shadow_blocked.update(
                        node for update in updates for node in update.blocked_nodes
                    )
                    shadow_contradicted = (
                        shadow_direct_mastered | shadow_inferred_mastered
                    ) & (shadow_blocked | shadow_direct_not_mastered)
                    changed_nodes = {n for u in updates for n in u.changed_nodes}
                    blocked = {n for u in updates for n in u.blocked_nodes}
                    logger.debug(
                        "graph_shadow item=%s events=%d changed=%d inferred=%d blocked=%d",
                        item.item_id,
                        len(events),
                        len(changed_nodes),
                        sum(len(update.inferred_updates) for update in updates),
                        len(blocked),
                    )
            except Exception:  # noqa: BLE001
                logger.debug("graph_shadow_failed", exc_info=True)

        session_variables = set(state.variables)
        touched = affected_mains(item, session_variables)

        # Phase C: live graph blocking / mastered-node tracking used by eligibility
        # filtering and queue invalidation. It must not change posterior math.
        graph_processed_ids = set(state.graph_processed_evidence_ids)
        graph_blocked_nodes = set(state.graph_blocked_nodes)
        graph_direct_measured_nodes = set(state.graph_direct_measured_nodes)
        graph_direct_mastered_nodes = set(state.graph_direct_mastered_nodes)
        graph_direct_not_mastered_nodes = set(state.graph_direct_not_mastered_nodes)
        graph_contradicted_nodes = set(state.graph_contradicted_nodes)
        graph_last_affected_mains: set[str] = set()

        if settings.graph_filtering_enabled or settings.graph_convergence_gate_enabled or settings.graph_utility_enabled:
            try:
                if self._graph_service is None:
                    g = load_default_competency_graph()
                    self._graph_service = CompetencyGraphService(g)

                cfg = PropagationConfig()

                for o in graded.outcomes:
                    modality = str(o.get("modality") or item.modality)
                    target_node = str(o.get("variable") or "")
                    evidence_id = (
                        f"{state.session_id}:{item.item_id}:{modality}:{target_node}"
                    )
                    if evidence_id in graph_processed_ids:
                        continue

                    graph_processed_ids.add(evidence_id)

                    score = float(o.get("score", 0.0))
                    weight = float(o.get("weight", 1.0))
                    confidence = float(o.get("confidence", 0.0))

                    if weight <= 0.0 or confidence <= 0.0 or not target_node:
                        continue
                    if target_node in self._graph_service.graph.nodes:
                        graph_direct_measured_nodes.add(target_node)

                    strong_success = (
                        score >= cfg.strong_success_threshold
                        and confidence >= cfg.minimum_propagation_confidence
                    )
                    strong_failure = (
                        score <= cfg.strong_failure_threshold
                        and confidence >= cfg.downward_block_confidence
                    )

                    if strong_success:
                        graph_direct_mastered_nodes.add(target_node)
                        graph_direct_not_mastered_nodes.discard(target_node)
                        if settings.graph_filtering_enabled:
                            graph_last_affected_mains.update(
                                self._graph_service.mains_for_node(target_node)
                            )

                        if settings.graph_upward_inference_enabled:
                            direct_effective = weight * confidence
                            inferred = infer_upward_inferred_nodes(
                                graph=self._graph_service,
                                target_node=target_node,
                                direct_effective=direct_effective,
                                confidence=confidence,
                                score=score,
                                config=cfg,
                            )
                            for anc in inferred:
                                graph_direct_mastered_nodes.add(anc)
                                graph_direct_not_mastered_nodes.discard(anc)
                                if settings.graph_filtering_enabled:
                                    graph_last_affected_mains.update(
                                        self._graph_service.mains_for_node(anc)
                                    )

                    if strong_failure:
                        graph_direct_not_mastered_nodes.add(target_node)
                        graph_direct_mastered_nodes.discard(target_node)
                        if settings.graph_descendant_blocking_enabled:
                            for desc in self._graph_service.descendants(
                                target_node, include_self=False
                            ):
                                graph_blocked_nodes.add(desc)
                                if settings.graph_filtering_enabled:
                                    graph_last_affected_mains.update(
                                        self._graph_service.mains_for_node(desc)
                                    )
            except Exception:  # noqa: BLE001
                logger.debug("graph_phase_c_failed", exc_info=True)

        graph_last_affected_mains &= session_variables
        graph_contradicted_nodes = (
            graph_direct_mastered_nodes
            & (graph_blocked_nodes | graph_direct_not_mastered_nodes)
        )

        queue = CandidateQueue(state.queue)
        for variable in touched | graph_last_affected_mains:
            queue.release(variable)

        served = [*state.served_item_ids, item.item_id]
        updated = dict(state.variables)
        if settings.graph_shared_main_rollup_enabled and settings.graph_upward_inference_enabled:
            if self._graph_service is None:
                g = load_default_competency_graph()
                self._graph_service = CompetencyGraphService(g)
            rolled = graph_rollup_outcomes(
                graded.outcomes,
                session_variables=session_variables,
                graph=self._graph_service,
                config=PropagationConfig(),
            )
        else:
            rolled = rollup_outcomes(graded.outcomes, session_variables)

        for outcome in rolled:
            if outcome.variable not in updated:
                continue
            updated[outcome.variable] = variables_module.apply_outcome(
                updated[outcome.variable],
                outcome,
                item.cat.a,
                item.cat.b,
                item.cat.c,
                item.item_id,
            )

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
            if (
                candidate_state.finalised
                and candidate_state.converged
                and settings.graph_convergence_gate_enabled
            ):
                if self._graph_service is None:
                    g = load_default_competency_graph()
                    self._graph_service = CompetencyGraphService(g)

                if not coverage_allows_convergence(
                    self._graph_service,
                    variable,
                    measured=graph_direct_measured_nodes,
                    mastered=graph_direct_mastered_nodes,
                    not_mastered=graph_direct_not_mastered_nodes,
                    critical_only=settings.graph_coverage_critical_only,
                ):
                    missing = unmeasured_required_nodes(
                        self._graph_service,
                        variable,
                        measured=graph_direct_measured_nodes,
                        mastered=graph_direct_mastered_nodes,
                        not_mastered=graph_direct_not_mastered_nodes,
                        critical_only=settings.graph_coverage_critical_only,
                    )
                    # Precision has precedence inside convergence.evaluate(). If coverage
                    # vetoes that precision stop, restore the hard budget semantics rather
                    # than repeatedly vetoing forever past CAT_MAX_QUESTIONS.
                    if remaining <= 0:
                        candidate_state = variable_state.model_copy(
                            update={
                                "finalised": True,
                                "stop_reason": "bank_exhausted",
                                "converged": False,
                            }
                        )
                    elif variable_state.observations >= settings.cat_max_questions:
                        candidate_state = variable_state.model_copy(
                            update={
                                "finalised": True,
                                "stop_reason": "question_budget",
                                "converged": False,
                            }
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
        return (
            state.model_copy(
                update={
                    "variables": updated,
                    "queue": queue.pending(),
                    "presenting": None,
                    "served_item_ids": served,
                    "items_administered": state.items_administered + 1,
                    "elapsed_minutes": round(elapsed, 3),
                    "graph_processed_evidence_ids": sorted(graph_processed_ids),
                    "graph_blocked_nodes": sorted(graph_blocked_nodes),
                    "graph_direct_measured_nodes": sorted(graph_direct_measured_nodes),
                    "graph_direct_mastered_nodes": sorted(graph_direct_mastered_nodes),
                    "graph_direct_not_mastered_nodes": sorted(
                        graph_direct_not_mastered_nodes
                    ),
                    "graph_contradicted_nodes": sorted(graph_contradicted_nodes),
                    "graph_shadow_direct_mastered_nodes": sorted(shadow_direct_mastered),
                    "graph_shadow_inferred_mastered_nodes": sorted(shadow_inferred_mastered),
                    "graph_shadow_direct_not_mastered_nodes": sorted(
                        shadow_direct_not_mastered
                    ),
                    "graph_shadow_blocked_nodes": sorted(shadow_blocked),
                    "graph_shadow_contradicted_nodes": sorted(shadow_contradicted),
                    "graph_last_affected_mains": sorted(graph_last_affected_mains),
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
        if settings.graph_filtering_enabled and state.graph_last_affected_mains:
            touched = set(touched) | set(state.graph_last_affected_mains)
        updated = await self.fill_queue(state, use_llm=use_llm, rng=rng, only=touched)
        if settings.graph_filtering_enabled:
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
        if state.items_administered >= settings.orchestrator_max_items:
            return True, "item_budget"
        if state.started_at and state.elapsed_minutes >= settings.orchestrator_time_limit_minutes:
            return True, "time_limit"
        if state.presenting is None and not state.queue:
            return True, "no_candidates_available"
        return False, ""

    # --- reporting ----------------------------------------------------------
    def summarise(self, state: AssessmentState, stop_reason: str = "") -> AssessmentReport:
        """The end-of-assessment report. Nothing here is inferred."""
        by_item = {i.item_id: i for i in self._bank.all_items()}

        def modalities_for(variable_state: VariableState) -> list[str]:
            return sorted(
                {
                    by_item[item_id].modality
                    for item_id in variable_state.served_item_ids
                    if item_id in by_item
                }
            )

        reports: list[VariableReport] = []
        for variable, variable_state in sorted(state.variables.items()):
            level, band = ability_band(variable_state.theta_hat)
            reports.append(
                VariableReport(
                    variable=variable,
                    theta_hat=round(variable_state.theta_hat, 4),
                    standard_error=round(variable_state.standard_error, 4),
                    certainty_pct=round(variables_module.certainty(variable_state), 1),
                    level=level if variable_state.observations else None,
                    band=band if variable_state.observations else "Not assessed",
                    observations=variable_state.observations,
                    finalised=variable_state.finalised,
                    converged=variable_state.converged,
                    stop_reason=variable_state.stop_reason,
                    modalities_used=modalities_for(variable_state),
                )
            )

        return AssessmentReport(
            session_id=state.session_id,
            items_administered=state.items_administered,
            variables=reports,
            all_finalised=not state.open_variables,
            stop_reason=stop_reason,
        )
