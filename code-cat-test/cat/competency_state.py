"""Learner competency state and the deterministic update (section 11).

The LLM never writes any of this. It produces evidence; code decides what that evidence
does to a learner's estimate. That boundary is the point of the whole design — a model
that can edit the score directly can make a candidate look however it likes, and no
amount of prompt discipline recovers auditability once it can.

MODEL CHOICE. A Beta belief over mastery, updated by evidence weight. Section 11.1 lists
GPCM and the graded response model as the destinations, and they are the right ones — but
both need calibrated item parameters, which need real learner responses, which do not
exist yet. Beta is chosen because it is honest in the meantime: it is a real posterior
with a real variance, its standard error falls only as evidence accumulates, and it can
be swapped for GPCM without changing anything upstream of `update`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CompetencyState:
    """Belief about one competency. `mastery` is the posterior mean of a Beta."""

    competency_id: str
    alpha: float = 1.0
    beta: float = 1.0
    evidence_count: int = 0
    misconception_codes: list[str] = field(default_factory=list)
    last_updated_by: str = "init"

    @property
    def mastery(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def standard_error(self) -> float:
        """Posterior SD of the Beta. Falls only as real evidence arrives."""
        a, b = self.alpha, self.beta
        return math.sqrt((a * b) / (((a + b) ** 2) * (a + b + 1.0)))

    @property
    def observed(self) -> bool:
        """False while the estimate is still just the prior.

        Reporting a competency that has never been assessed is worse than reporting
        nothing: Beta(1,1) has a mastery of exactly 0.5, which reads as a measured
        mid-level result rather than as absence of evidence.
        """
        return self.evidence_count > 0


@dataclass
class LearnerModel:
    """All competency beliefs for one candidate."""

    states: dict[str, CompetencyState] = field(default_factory=dict)

    def get(self, competency_id: str) -> CompetencyState:
        if competency_id not in self.states:
            self.states[competency_id] = CompetencyState(competency_id)
        return self.states[competency_id]

    def weakest(self, limit: int = 3) -> list[CompetencyState]:
        seen = [s for s in self.states.values() if s.observed]
        return sorted(seen, key=lambda s: s.mastery)[:limit]

    def most_uncertain(self, limit: int = 3) -> list[CompetencyState]:
        return sorted(self.states.values(), key=lambda s: -s.standard_error)[:limit]

    def snapshot(self) -> dict[str, dict]:
        return {
            cid: {
                "mastery": round(s.mastery, 4),
                "standard_error": round(s.standard_error, 4),
                "evidence_count": s.evidence_count,
                "observed": s.observed,
                "misconception_codes": s.misconception_codes,
            }
            for cid, s in sorted(self.states.items())
        }


def update(model: LearnerModel, evidence_items: list) -> LearnerModel:
    """Apply competency evidence. The only function permitted to move a learner estimate.

    Evidence with zero strength is skipped entirely rather than applied as a tiny update:
    an infrastructure failure carries strength 0, and adding 0 to both Beta parameters
    would still increment `evidence_count` and make an unmeasured competency look
    measured.
    """
    for item in evidence_items:
        strength = float(item.evidence_strength) * float(item.confidence)
        if strength <= 0.0:
            continue

        state = model.get(item.competency_id)
        state.alpha += strength * float(item.score)
        state.beta += strength * (1.0 - float(item.score))
        state.evidence_count += 1
        state.last_updated_by = "code"

        for code in item.misconception_codes:
            if code not in state.misconception_codes:
                state.misconception_codes.append(code)

    return model
