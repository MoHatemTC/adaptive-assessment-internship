"""Evidence strength and competency projection for voice answers."""

from __future__ import annotations

from app.config.voice_settings import voice_settings
from app.schemas.voice import (
    VoiceCompetencyEvidence,
    VoiceEvaluation,
    VoiceResponsePackage,
)
from app.services.voice.rubrics import (
    criterion_to_competency_weights,
    normalize_criterion_score,
)

_DEPENDENCY_MULT = {
    "independent": 1.0,
    "probe_supported": 0.9,
    "probe_dependent": 0.75,
}


def evidence_strength(package: VoiceResponsePackage, evaluation: VoiceEvaluation) -> float:
    """Voice analogues of the code path's evidence-strength rungs."""
    if package.outcome_status == "infrastructure_error":
        return 0.0
    if package.outcome_status == "unscorable":
        return 0.0
    if package.total_speech_seconds < voice_settings.min_speech_seconds_scorable:
        # typed/text fallback may report 0 speech — treat long transcripts as spoken
        if package.word_count < 12 and len(package.transcript.split()) < 12:
            return 0.0

    if package.mean_transcript_confidence < voice_settings.transcript_confidence_floor:
        strength = 0.25
    elif package.cut_off or package.outcome_status == "truncated":
        strength = 0.4
    else:
        strength = 1.0

    words = package.word_count or len(package.transcript.split())
    speech = package.total_speech_seconds
    if speech > 0 and speech < 20 and words < 50:
        strength *= 0.5
    if package.explicit_decline:
        strength *= 0.5
    if evaluation.evaluation_confidence < 0.5:
        strength *= 0.8
    if evaluation.degraded:
        strength *= 0.6
    return min(max(strength, 0.0), 1.0)


def project_competency_evidence(
    package: VoiceResponsePackage,
    evaluation: VoiceEvaluation,
    rubric: dict,
) -> list[VoiceCompetencyEvidence]:
    """Fold validated criterion evidence into per-competency scores."""
    projection = criterion_to_competency_weights(rubric)
    rubric_weights: dict[str, float] = {}
    for crit in rubric.get("criteria", []):
        rubric_weights[crit["competency_id"]] = (
            rubric_weights.get(crit["competency_id"], 0.0) + float(crit.get("weight", 0.0))
        )

    # accumulators per competency
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    conf_num: dict[str, float] = {}
    covered: dict[str, float] = {}

    for ev in evaluation.criterion_evidence:
        s = normalize_criterion_score(ev.raw_score, ev.maximum_score)
        dep = _DEPENDENCY_MULT.get(ev.prompt_dependency, 0.9)
        if ev.quote is None and ev.quote_tier == "described":
            dep *= 0.7
        row = projection.get(ev.criterion_id, {ev.competency_id: 1.0})
        for comp, proj_w in row.items():
            w = proj_w * dep
            num[comp] = num.get(comp, 0.0) + s * w
            den[comp] = den.get(comp, 0.0) + w
            conf_num[comp] = conf_num.get(comp, 0.0) + ev.confidence * w
            covered[comp] = covered.get(comp, 0.0) + float(
                next(
                    (
                        c.get("weight", 0.0)
                        for c in rubric.get("criteria", [])
                        if c["criterion_id"] == ev.criterion_id
                    ),
                    0.0,
                )
            )

    base = evidence_strength(package, evaluation)
    out: list[VoiceCompetencyEvidence] = []
    # Ensure every measured competency appears (zero if no evidence)
    comps = set(rubric_weights) | set(den)
    for comp in sorted(comps):
        d = den.get(comp, 0.0)
        score = (num[comp] / d) if d > 0 else 0.0
        conf = (conf_num[comp] / d) if d > 0 else 0.0
        total_w = rubric_weights.get(comp, 0.0) or 1.0
        coverage = min((covered.get(comp, 0.0) / total_w) if total_w else 0.0, 1.0)
        strength = base * coverage
        out.append(
            VoiceCompetencyEvidence(
                competency_id=comp,
                score=score,
                confidence=conf,
                evidence_strength=strength,
                coverage=coverage,
                source_item_id=package.item_id,
            )
        )
    return out
