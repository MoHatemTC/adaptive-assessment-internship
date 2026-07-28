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

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives one multi-variable, multi-modality assessment."""

    def __init__(self, bank: UnifiedBankRepository, grader: GraderAgent) -> None:
        self._bank = bank
        self._grader = grader

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

            pool = self._bank.shortlist(variable, exclude=served | queue.queued_item_ids())
            candidate = await pick(
                pool, state.variables[variable], variable, use_llm=use_llm, rng=rng
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
        session_variables = set(state.variables)
        touched = affected_mains(item, session_variables)

        queue = CandidateQueue(state.queue)
        for variable in touched:
            queue.release(variable)

        served = [*state.served_item_ids, item.item_id]
        updated = dict(state.variables)
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
            remaining = len(self._bank.shortlist(variable, exclude=set(served)))
            updated[variable] = variables_module.evaluate_finalisation(variable_state, remaining)
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
        return await self.fill_queue(state, use_llm=use_llm, rng=rng, only=touched)

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
