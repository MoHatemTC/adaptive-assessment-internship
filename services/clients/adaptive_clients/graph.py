"""The competency graph, over HTTP, satisfying the engine's propagation port.

`PropagationPort` has exactly two methods, and that is the whole surface the orchestrator
uses to reach the graph for WRITING. Traversal — which sub-competencies a main requires,
which nodes are blocked — stays local against a cached graph structure, because selection
reads it on every candidate on every step and a network hop there would blow the 100 ms
budget.

FAILING OPEN IS THE CORRECT BEHAVIOUR

If this service is unreachable, the candidate's response is still graded, still folded into
the posterior, and the session continues — the graph layer is off for that response and the
result says so. That is not a compromise: propagation ships INERT on this deployment
already, and the graph's job is to change which question is asked next, not what is
estimated. An outage that abandoned a session would be a far worse failure than one that
degrades to the ungated engine.
"""

from __future__ import annotations

import logging

from adaptive_contracts import (
    CoverageResponse,
    ManifestResponse,
    PropagateResponse,
)

from .transport import BaseClient, ClientError

logger = logging.getLogger(__name__)


class CompetencyGraphClient(BaseClient):
    """Implements `app.services.orchestrator.propagation_port.PropagationPort`.

    Structurally rather than by inheritance — the port is a `Protocol`, so this package
    stays free of any engine import and a frontend's client generator can install it
    without pulling in numpy and 600 KB of question banks.
    """

    def __init__(self, *args, bank_id: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._bank_id = bank_id

    def manifest(self) -> tuple[dict, str]:
        try:
            body = ManifestResponse.model_validate(
                self.get(f"/manifest/{self._bank_id}").json()
            )
        except ClientError as exc:
            # A session that cannot describe its configuration is worse than one that
            # cannot record it, but only slightly — and refusing to start is worse than
            # both.
            logger.warning("could not read the propagation manifest: %s", exc)
            return {}, ""
        return body.manifest, body.manifest_hash

    def apply(self, state, item, graded):
        """One response's outcomes into the graph. Returns the engine's own result type.

        Imported lazily so the module-level contract of this package holds: no engine
        import at import time. A caller that has the engine installed — which is every
        service that would use this — gets the same object an in-process run produces, and
        the parity suite asserts the two are indistinguishable.
        """
        from cat_engine.engine.services.orchestrator.propagation_port import PropagationResult

        payload = {
            "bank_id": self._bank_id,
            "session_id": state.session_id,
            "item": {
                "item_id": item.item_id,
                "modality": item.modality,
                "minimum_success_confidence": item.minimum_success_confidence,
            },
            "outcomes": [
                {
                    "variable": o.get("variable", ""),
                    "score": float(o.get("score", 0.0)),
                    "weight": float(o.get("weight", 1.0)),
                    "confidence": float(o.get("confidence", 1.0)),
                    "source_item_id": str(o.get("source_item_id") or item.item_id),
                    "modality": o.get("modality") or item.modality,
                }
                for o in graded.outcomes
                if o.get("variable")
            ],
            "graph_state": {
                field: getattr(state, field)
                for field in type(state).model_fields
                if field.startswith("graph_")
            },
            "session_variables": sorted(state.variables),
            # Computed before `served_item_ids` grows, so a re-administration of one item
            # is distinguishable from a replay of the same one.
            "attempt_no": state.served_item_ids.count(item.item_id) + 1,
        }

        try:
            body = PropagateResponse.model_validate(
                self.post("/propagate", json=payload).json()
            )
        except ClientError as exc:
            logger.warning(
                "propagation unavailable for %s; continuing without the graph: %s",
                item.item_id,
                exc,
            )
            return PropagationResult(applied=False)

        return PropagationResult(
            state_update=body.state_update,
            selection_affected_mains=set(body.selection_affected_mains),
            manifest=body.manifest,
            manifest_hash=body.manifest_hash,
            applied=body.applied,
        )

    def coverage(
        self, bank_id: str, main: str, measured: list[str], *, critical_only: bool = False
    ) -> CoverageResponse:
        """Which required sub-competencies still lack direct evidence.

        For an operator or a diagnostics view. The convergence gate itself asks the LOCAL
        graph, because it runs on every response for every open competency.
        """
        return CoverageResponse.model_validate(
            self.post(
                "/coverage",
                json={
                    "bank_id": bank_id,
                    "main": main,
                    "directly_measured": measured,
                    "critical_only": critical_only,
                },
            ).json()
        )
