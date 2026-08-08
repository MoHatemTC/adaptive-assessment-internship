"""Validate a grader LLM reply against §12 dispositions."""

from __future__ import annotations

from app.schemas.voice import (
    CriterionEvidence,
    QuoteTier,
    VoiceEvaluation,
    VoiceResponsePackage,
)
from app.services.voice.quotes import match_quote

_ALLOWED_DEP = {"independent", "probe_supported", "probe_dependent"}


def validate(
    reply: dict,
    item_id: str,
    rubric: dict,
    package: VoiceResponsePackage,
) -> VoiceEvaluation:
    flags: list[str] = []
    degraded = False

    if str(reply.get("item_id", item_id)) != item_id:
        flags.append("QUESTION_ID_MISMATCH")
        return _fatal(item_id, rubric, flags, reply)
    if str(reply.get("rubric_id", rubric.get("rubric_id"))) != rubric.get("rubric_id"):
        flags.append("RUBRIC_ID_MISMATCH")
        return _fatal(item_id, rubric, flags, reply)
    if str(reply.get("rubric_version", rubric.get("version", "1.0"))) != str(
        rubric.get("version", "1.0")
    ):
        flags.append("RUBRIC_VERSION_MISMATCH")
        return _fatal(item_id, rubric, flags, reply)

    by_id = {c["criterion_id"]: c for c in rubric.get("criteria", [])}
    turn_by_id = {t.turn_id: t for t in package.turns}
    if not turn_by_id and package.transcript:
        # text-fallback path: synthesise a single candidate turn
        turn_by_id = {
            "t0": type(
                "T",
                (),
                {"turn_id": "t0", "role": "candidate", "text": package.transcript},
            )()
        }

    seen: set[str] = set()
    evidence: list[CriterionEvidence] = []
    raw_list = reply.get("criterion_evidence") or reply.get("criteria") or []

    for raw in raw_list:
        cid = str(raw.get("criterion_id", ""))
        if cid in seen:
            flags.append("DUPLICATE_CRITERION")
            continue
        seen.add(cid)
        if cid not in by_id:
            flags.append("UNKNOWN_CRITERION")
            degraded = True
            continue
        meta = by_id[cid]
        comp = str(raw.get("competency_id", meta["competency_id"]))
        if comp != meta["competency_id"]:
            flags.append("CRITERION_COMPETENCY_MISMATCH")
            degraded = True
            continue
        try:
            raw_score = float(raw.get("raw_score", raw.get("score", 0)))
            maximum = float(raw.get("maximum_score", meta["maximum_score"]))
            confidence = float(raw.get("confidence", 0.8))
        except (TypeError, ValueError):
            flags.append("UNPARSEABLE_SCORE")
            degraded = True
            continue
        if abs(maximum - float(meta["maximum_score"])) > 1e-6:
            flags.append("MAXIMUM_SCORE_MISMATCH")
            degraded = True
            continue
        if not (0.0 <= raw_score <= maximum):
            flags.append("SCORE_OUT_OF_RANGE")
            degraded = True
            continue
        if not (0.0 <= confidence <= 1.0):
            flags.append("CONFIDENCE_OUT_OF_RANGE")
            degraded = True
            continue

        dep = str(raw.get("prompt_dependency", "independent"))
        if dep not in _ALLOWED_DEP:
            flags.append("UNKNOWN_PROMPT_DEPENDENCY")
            dep = "probe_supported"
            degraded = True

        quote = raw.get("quote", None)
        turn_id = raw.get("quote_turn_id") or raw.get("turn_id")
        quote_tier: QuoteTier | None = None
        if quote is not None or turn_id:
            turn = turn_by_id.get(str(turn_id)) if turn_id else None
            if turn is None and len(turn_by_id) == 1:
                turn = next(iter(turn_by_id.values()))
                turn_id = turn.turn_id
            if turn is None:
                flags.append("UNKNOWN_TURN")
                degraded = True
                continue
            if getattr(turn, "role", "candidate") == "interviewer":
                flags.append("INTERVIEWER_TURN_AS_EVIDENCE")
                degraded = True
                continue
            tier, _ratio = match_quote(quote, getattr(turn, "text", ""))
            if tier is None:
                flags.append("QUOTE_NOT_IN_TURN" if quote else "NO_EVIDENCE_CITED")
                degraded = True
                continue
            if quote is not None and len((quote or "").strip()) < 3:
                flags.append("QUOTE_TOO_SHORT")
                degraded = True
                continue
            quote_tier = tier
        else:
            flags.append("NO_EVIDENCE_CITED")
            degraded = True
            continue

        evidence.append(
            CriterionEvidence(
                criterion_id=cid,
                competency_id=comp,
                raw_score=raw_score,
                maximum_score=maximum,
                confidence=confidence,
                prompt_dependency=dep,  # type: ignore[arg-type]
                quote=quote,
                quote_turn_id=str(turn_id) if turn_id else None,
                quote_tier=quote_tier,
                description=str(raw.get("description", raw.get("rationale", "")))[:500],
            )
        )

    required = {
        c["criterion_id"] for c in rubric.get("criteria", []) if c.get("required")
    }
    missing = required - {e.criterion_id for e in evidence}
    if missing:
        flags.append("MISSING_REQUIRED_CRITERION")
        degraded = True

    coverage = len(evidence) / max(len(by_id), 1)
    conf = float(reply.get("evaluation_confidence", 0.8))
    conf = min(max(conf, 0.0), 1.0)

    return VoiceEvaluation(
        item_id=item_id,
        rubric_id=rubric.get("rubric_id", ""),
        rubric_version=str(rubric.get("version", "1.0")),
        criterion_evidence=evidence,
        flags=flags,
        degraded=degraded,
        evaluation_confidence=conf if coverage >= 0.5 else min(conf, 0.4),
        overall_rationale=str(
            reply.get("overall_rationale", reply.get("rationale", ""))
        )[:800],
        raw_reply=reply,
    )


def _fatal(
    item_id: str, rubric: dict, flags: list[str], reply: dict
) -> VoiceEvaluation:
    return VoiceEvaluation(
        item_id=item_id,
        rubric_id=rubric.get("rubric_id", ""),
        rubric_version=str(rubric.get("version", "1.0")),
        criterion_evidence=[],
        flags=flags,
        degraded=True,
        evaluation_confidence=0.0,
        overall_rationale="fatal validation failure",
        raw_reply=reply,
    )
