"""The adaptive state: everything needed to continue the assessment, and nothing else.

The engine is a pure state transition — the caller persists this object between calls
and sends it back. It is JSON-compatible by construction:

    payload = state.model_dump(mode="json")
    restored = AdaptiveState.model_validate(payload)

and continuing from ``restored`` produces the same decisions as continuing from the
original, on any process.

WHAT IT DELIBERATELY DOES NOT CONTAIN

No user or session identity, no timestamps, no database ids — those belong to the host.
No copy of the assessment definition and no answer keys — the compiled assessment is
supplied on every call. And no derived numbers: the ability estimate and its standard
error are always read from the posterior, never stored beside it where the two could
drift apart. The posterior itself IS stored in full: a 3PL posterior is genuinely skewed
near the guessing floor, and reducing it to a displayed level and confidence would change
what the next selection does.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from adaptive_engine.irt import GRID_SIZE
from adaptive_engine.models import FrozenModel

STATE_SCHEMA_VERSION = 1


class CompetencyState(FrozenModel):
    """One competency's belief: the full posterior over the ability grid.

    ``observations`` counts pieces of direct evidence applied — one per question that
    measures this competency, however many competencies that question touched. Initial
    beliefs shape the posterior but never this counter.
    """

    posterior: list[float] = Field(min_length=GRID_SIZE, max_length=GRID_SIZE)
    observations: int = Field(default=0, ge=0)

    @field_validator("posterior")
    @classmethod
    def _is_a_distribution(cls, weights: list[float]) -> list[float]:
        if any(w < 0.0 for w in weights):
            raise ValueError("posterior weights must be non-negative")
        if sum(weights) <= 0.0:
            raise ValueError("posterior has non-positive mass")
        return weights


class GraphNodeEvidence(FrozenModel):
    """Direct-evidence classification for one graph node (= one competency).

    Only what cannot be recomputed from the posterior lives here: how often direct
    evidence was strong enough to classify. Everything the graph derives at decision time
    — which nodes are failing, which competencies are blocked — is computed from this one
    record, never stored in parallel.
    """

    strong_successes: int = Field(default=0, ge=0)
    strong_failures: int = Field(default=0, ge=0)


class AdaptiveState(FrozenModel):
    """The complete runtime state of one assessment run.

    ``random_seed`` drives exposure control: the next selection's randomization is a pure
    function of the seed and how many questions have been administered, so a state
    restored elsewhere draws exactly the same window offset.
    """

    schema_version: int = STATE_SCHEMA_VERSION
    assessment_id: str = Field(min_length=1)
    assessment_version: str = Field(min_length=1)
    competency_states: dict[str, CompetencyState]
    administered_question_ids: list[str] = Field(default_factory=list)
    current_question_id: str | None = None
    graph_evidence: dict[str, GraphNodeEvidence] = Field(default_factory=dict)
    random_seed: int = Field(ge=0)

    @property
    def questions_answered(self) -> int:
        """Derived from the administered list — the one authoritative record."""
        return len(self.administered_question_ids)

    @property
    def is_finished(self) -> bool:
        """A finished run presents no question. There is no separate status flag to
        contradict this."""
        return self.current_question_id is None
