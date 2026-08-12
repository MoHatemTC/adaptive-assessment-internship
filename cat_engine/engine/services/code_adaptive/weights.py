"""The logic/LLM split, as an adjustable and auditable object.

WHY THIS IS NOT JUST A DICT

`SOURCE_WEIGHTS` fixes each approach's split at import time, which is right for a study
comparing three fixed arms but wrong for an operator who needs to tune how much authority
the model holds. This turns that split into a value an admin can set, WITHOUT losing the
property that makes the study's numbers mean anything: a score must be reproducible from
its inputs.

So every profile carries a `fingerprint`, and every evaluation records it. A score of
0.62 is not interpretable on its own — it depends entirely on whether the model held 15%
of the criterion or 70% — and a tuned deployment that does not record its split produces
numbers nobody can reconstruct or defend later.

THE CONTROL AN ADMIN ACTUALLY GETS

One number per criterion: the LLM's share, 0..1. The remainder goes to the deterministic
sources in the ratio the base approach already used, so lowering the model's share hands
authority back to tests and static analysis in the proportion that approach considered
sensible, rather than to whichever source happens to be listed first.

Measured context for whoever sets these (60 submissions, 30 gradable, paired):

    B's model authority is 30% overall and its separation advantage over the fully
    objective approach A was +0.012 [-0.012, +0.037] — indistinguishable from zero at
    7,455 tokens and 57s per submission. What the model measurably adds is DIAGNOSIS:
    misconception recall 0.12 -> 1.00. Misconception codes reach the learner model
    independently of these weights, so lowering the LLM's score share does not cost
    diagnosis.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

CRITERIA = (
    "functional_correctness",
    "edge_case_handling",
    "algorithm_choice",
    "code_quality",
)

DETERMINISTIC_SOURCES = ("tests", "static")

# Functional correctness is the one criterion where a model's opinion can contradict a
# measured fact. Giving it weight there does not merely tune the split, it changes what
# kind of claim the score is, so the UI warns rather than silently accepting it. The
# anchoring clamp in scoring.combine() still applies regardless — this is a warning
# threshold, not the safety mechanism.
CONTRADICTION_SENSITIVE = "functional_correctness"

# Which sources can actually produce a value for each criterion, mirroring what
# objective_criterion_scores() and static_criterion_scores() emit. A weight assigned to a
# source not listed here is dead: combine() drops it and renormalises the rest, so the
# declared number never applies and quoting it back to an admin is a misreport.
#
# Execution cannot judge whether an algorithm was well chosen or whether code reads well —
# only whether it worked. That is the whole reason those criteria exist separately.
SOURCES_THAT_CAN_SCORE = {
    "functional_correctness": {"tests", "llm"},
    "edge_case_handling": {"tests", "static", "llm"},
    "algorithm_choice": {"static", "llm"},
    "code_quality": {"static", "llm"},
}


@dataclass(frozen=True)
class WeightProfile:
    """Per-criterion source weights, plus the identity needed to reproduce a score."""

    criteria: dict[str, dict[str, float]]
    base_approach: str
    label: str = "custom"
    note: str = ""

    # --- construction ----------------------------------------------------------
    @classmethod
    def from_preset(cls, approach: str, presets: dict) -> WeightProfile:
        return cls(
            criteria={c: dict(w) for c, w in presets[approach].items()},
            base_approach=approach,
            label=f"preset:{approach}",
        )

    def with_llm_shares(self, shares: dict[str, float]) -> WeightProfile:
        """Return a profile where each named criterion gives the model `share` of the vote.

        The deterministic remainder keeps the base approach's internal ratio. Where the
        base gave the model everything (so there is no deterministic ratio to preserve),
        the remainder is split evenly across the sources that can actually produce a value
        for that criterion — an even split is an assumption, but a visible one, and the
        alternative is dropping the remainder entirely and silently renormalising the
        model back to full authority.
        """
        updated = {c: dict(w) for c, w in self.criteria.items()}

        for criterion, share in shares.items():
            if criterion not in updated:
                continue
            share = min(max(float(share), 0.0), 1.0)
            base = self.criteria[criterion]

            deterministic = {s: base.get(s, 0.0) for s in DETERMINISTIC_SOURCES}
            total = sum(deterministic.values())
            if total <= 0.0:
                deterministic = {s: 1.0 for s in _plausible_sources(criterion)}
                total = sum(deterministic.values())

            weights = {
                s: (v / total) * (1.0 - share)
                for s, v in deterministic.items()
                if v > 0
            }
            if share > 0.0:
                weights["llm"] = share
            updated[criterion] = {s: round(v, 4) for s, v in weights.items() if v > 0}

        return WeightProfile(
            criteria=updated,
            base_approach=self.base_approach,
            label="custom",
            note=f"tuned from preset {self.base_approach}",
        )

    @classmethod
    def from_dict(cls, payload: dict) -> WeightProfile:
        return cls(
            criteria={c: dict(w) for c, w in payload["criteria"].items()},
            base_approach=payload.get("base_approach", "B"),
            label=payload.get("label", "custom"),
            note=payload.get("note", ""),
        )

    # --- reading ---------------------------------------------------------------
    def for_criterion(self, criterion_id: str) -> dict[str, float]:
        return dict(self.criteria.get(criterion_id, {}))

    def llm_shares(self) -> dict[str, float]:
        return {c: round(w.get("llm", 0.0), 4) for c, w in self.criteria.items()}

    def uses_llm(self) -> bool:
        """False when no criterion gives the model weight, so no call need be made."""
        return any(w.get("llm", 0.0) > 0.0 for w in self.criteria.values())

    def overall_shares(
        self, criterion_weights: dict[str, float] | None = None
    ) -> dict[str, float]:
        """Each source's EFFECTIVE share of the whole score.

        Two corrections separate this from reading the weight table off the page, and both
        were misreporting the exact quantity this control exists to expose.

        SOURCES THAT NEVER PRODUCE A VALUE ARE DROPPED. `algorithm_choice` declares
        `tests 0.35`, but execution cannot say whether an algorithm was well chosen and no
        test-derived value is ever produced for it. combine() correctly drops the absent
        source and renormalises, which quietly hands that 0.35 to static and the model —
        so the model really holds 53.8% of that criterion, not the 35% declared. Reading
        the table gave "LLM 30.0%" when the truth was 34.7%.

        CRITERIA ARE NOT EQUALLY WEIGHTED. They carry `maximum_score` from the bank, so a
        source concentrated in code_quality (15%) counts for far less than one in
        functional correctness (40%). Pass the weights to reflect that; the default is an
        equal split, which is only right for a uniform bank.
        """
        weights = criterion_weights or {
            c: 1.0 / len(self.criteria) for c in self.criteria
        }
        totals = {"tests": 0.0, "static": 0.0, "llm": 0.0}

        for criterion, sources in self.criteria.items():
            usable = {
                s: v
                for s, v in sources.items()
                if v > 0 and s in SOURCES_THAT_CAN_SCORE.get(criterion, set(sources))
            }
            span = sum(usable.values())
            if span <= 0:
                continue
            share_of_total = weights.get(criterion, 0.0)
            for source, value in usable.items():
                totals[source] = (
                    totals.get(source, 0.0) + (value / span) * share_of_total
                )

        return {s: round(v, 4) for s, v in totals.items()}

    def contradiction_risk(self) -> float:
        """Model share on the criterion where it can contradict a measured fact."""
        return self.criteria.get(CONTRADICTION_SENSITIVE, {}).get("llm", 0.0)

    def to_dict(self) -> dict:
        return {
            "criteria": {c: dict(w) for c, w in self.criteria.items()},
            "base_approach": self.base_approach,
            "label": self.label,
            "note": self.note,
            "overall_shares": self.overall_shares(),
            "fingerprint": self.fingerprint(),
        }

    def fingerprint(self) -> str:
        """Stable short hash of the weights alone.

        Of the weights ONLY — not the label or note — so two profiles that score
        identically fingerprint identically whatever they are called. This is what makes
        "was this score produced under the same rules as that one" answerable.
        """
        canonical = json.dumps(
            {
                c: {s: round(v, 4) for s, v in sorted(w.items())}
                for c, w in sorted(self.criteria.items())
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    def validate(self) -> list[str]:
        """Problems worth showing an admin. Never raises — a warning beats a dead page."""
        problems: list[str] = []
        for criterion, weights in self.criteria.items():
            if not weights:
                problems.append(
                    f"{criterion}: no source has any weight — it cannot be scored"
                )
                continue
            total = sum(weights.values())
            if abs(total - 1.0) > 0.01:
                problems.append(f"{criterion}: weights sum to {total:.2f}, not 1.00")
            for source, value in weights.items():
                if value < 0.0:
                    problems.append(f"{criterion}/{source}: negative weight {value}")
        return problems


def _plausible_sources(criterion: str) -> tuple[str, ...]:
    """Deterministic sources that can produce a value for a criterion at all.

    Static analysis cannot judge functional correctness — only execution can — so an even
    fallback split must not invent a static term there.
    """
    if criterion == "functional_correctness":
        return ("tests",)
    if criterion == "code_quality":
        return ("static",)
    return DETERMINISTIC_SOURCES
