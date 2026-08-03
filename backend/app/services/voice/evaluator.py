"""Async voice response evaluator — LLM rubric grading before sync grade_voice."""

from __future__ import annotations

import json
import logging

from app.schemas.orchestration import BankItem
from app.schemas.voice import GradedVoiceResponse, VoiceEvaluation, VoiceResponsePackage, VoiceTurn
from app.services import observability
from app.services.adaptive.llm import LLMUnavailable, chat_json
from app.services.voice.prompts import EVALUATOR_SYSTEM
from app.services.voice.language import candidate_turns_non_english, looks_non_english
from app.services.voice.rubrics import load_rubric
from app.services.voice.validation import validate

logger = logging.getLogger(__name__)


def package_from_text(
    item_id: str,
    text: str,
    *,
    outcome_status: str = "complete",
    speech_seconds: float | None = None,
) -> VoiceResponsePackage:
    """Build a VoiceResponsePackage from a typed / pasted transcript (Streamlit tester)."""
    words = text.split()
    secs = speech_seconds if speech_seconds is not None else max(len(words) / 2.5, 5.0)
    return VoiceResponsePackage(
        item_id=item_id,
        outcome_status=outcome_status,  # type: ignore[arg-type]
        # Typed text is exact — not ASR — so confidence 1.0 is honest here.
        turns=[VoiceTurn(turn_id="t0", role="candidate", text=text, transcript_confidence=1.0)],
        total_speech_seconds=float(secs),
        mean_transcript_confidence=1.0,
        word_count=len(words),
        live_text=text,
        final_text=text,
    )


async def evaluate(
    item: BankItem,
    package: VoiceResponsePackage,
    *,
    use_llm: bool = True,
) -> GradedVoiceResponse:
    """Async evaluation. Returns a GradedVoiceResponse ready for sync grade_voice."""
    rubric_id = (item.payload or {}).get("rubric_id", "")
    rubric = load_rubric(rubric_id) if rubric_id else _rubric_from_payload(item)

    if package.outcome_status in {"unscorable", "infrastructure_error"}:
        return GradedVoiceResponse(
            package=package,
            evaluation=VoiceEvaluation(
                item_id=item.item_id,
                rubric_id=rubric.get("rubric_id", ""),
                criterion_evidence=[],
                flags=[f"PACKAGE_{package.outcome_status.upper()}"],
                degraded=True,
                evaluation_confidence=0.0,
                overall_rationale=package.reason_code or package.outcome_status,
            ),
            rubric=rubric,
        )

    if not use_llm or len(package.transcript.strip()) < 12:
        evaluation = _heuristic_from_rubric(
            item.item_id, rubric, package, flags=["HEURISTIC_FALLBACK"]
        )
        evaluation = _apply_english_only_clamp(evaluation, package, rubric)
        return GradedVoiceResponse(package=package, evaluation=evaluation, rubric=rubric)

    payload = _build_payload(item, package, rubric)
    evaluation = await _call_grader(
        payload, item.item_id, rubric, package, modality=item.modality
    )
    coverage = len(evaluation.criterion_evidence) / max(len(rubric.get("criteria", [])), 1)

    if (
        any(
            f in {"QUESTION_ID_MISMATCH", "RUBRIC_ID_MISMATCH", "RUBRIC_VERSION_MISMATCH"}
            for f in evaluation.flags
        )
        or coverage < 0.5
        or not evaluation.criterion_evidence
    ):
        payload["previous_attempt_errors"] = evaluation.flags
        evaluation = await _call_grader(
            payload, item.item_id, rubric, package, modality=item.modality
        )

    evaluation = _apply_english_only_clamp(evaluation, package, rubric)
    return GradedVoiceResponse(package=package, evaluation=evaluation, rubric=rubric)


async def _call_grader(
    payload: dict,
    item_id: str,
    rubric: dict,
    package: VoiceResponsePackage,
    *,
    modality: str = "open",
) -> VoiceEvaluation:
    try:
        # Async OpenAI + custom httpx is not auto-patched by langfuse.openai; pass
        # generation labels explicitly so open grades land in the assessment session.
        reply = await chat_json(
            EVALUATOR_SYSTEM,
            json.dumps(payload, indent=2),
            require=("item_id", "criterion_evidence"),
            trace=observability.generation(
                name="open_grader",
                item_id=item_id,
                rubric_id=rubric.get("rubric_id"),
                stage="grade",
                # Threaded in, not read off an `item` this function never receives. It
                # takes `item_id`, so `item.modality` was a NameError on the only path
                # that reaches the grader — one that needs a live gateway, and so is
                # invisible to every offline test.
                modality=modality,
            ),
        )
    except (LLMUnavailable, ValueError, TypeError) as exc:
        logger.warning("voice grader unavailable (%s) — heuristic fallback", exc)
        return _heuristic_from_rubric(item_id, rubric, package, flags=["LLM_UNAVAILABLE"])
    return validate(reply, item_id, rubric, package)


def _build_payload(item: BankItem, package: VoiceResponsePackage, rubric: dict) -> dict:
    non_en = candidate_turns_non_english(package.turns)
    whole_non_en = looks_non_english(package.transcript)
    return {
        "item_id": item.item_id,
        "rubric_id": rubric.get("rubric_id"),
        "rubric_version": rubric.get("version", "1.0"),
        "question": (item.payload or {}).get("prompt")
        or (item.payload or {}).get("question")
        or "",
        "transcript": package.transcript,
        "turns": [t.model_dump() for t in package.turns],
        "criteria": [
            {
                "criterion_id": c["criterion_id"],
                "competency_id": c["competency_id"],
                "maximum_score": c["maximum_score"],
                "descriptor": c.get("descriptor", ""),
                "required": c.get("required", True),
            }
            for c in rubric.get("criteria", [])
        ],
        "expected_answer_points": rubric.get("expected_answer_points", [])[:8],
        "common_pitfalls": rubric.get("common_pitfalls", [])[:6],
        "instructions": {
            "score_each_criterion": True,
            "cite_quote_or_null": True,
            "quote_must_appear_in_candidate_turn": True,
            "no_protected_attribute_language": True,
            "english_only": True,
            "non_english_answers_score_near_zero": True,
            "non_english_candidate_turns": non_en[:4],
            "transcript_flagged_non_english": whole_non_en or bool(non_en),
        },
    }


def _apply_english_only_clamp(
    evaluation: VoiceEvaluation,
    package: VoiceResponsePackage,
    rubric: dict,
) -> VoiceEvaluation:
    """Deterministic floor when candidate speech is non-English (prompt-only is soft)."""
    non_en_turns = candidate_turns_non_english(package.turns)
    whole = looks_non_english(package.transcript)
    if not non_en_turns and not whole:
        return evaluation

    candidate_turns = [t for t in package.turns if t.role == "candidate" and t.text.strip()]
    english_turns = [t for t in candidate_turns if not looks_non_english(t.text)]
    # Entire answer non-English, or only non-English evidence → near-zero everything.
    # Mixed: English first + non-English probe → keep modest credit from English turns only
    # by capping required technical criteria hard when the last turn is non-English.
    last_non_en = bool(candidate_turns) and looks_non_english(candidate_turns[-1].text)
    only_non_en = not english_turns

    flags = list(evaluation.flags)
    if "NON_ENGLISH_SPEECH" not in flags:
        flags.append("NON_ENGLISH_SPEECH")

    capped = []
    for ev in evaluation.criterion_evidence:
        maximum = float(ev.maximum_score)
        score = float(ev.raw_score)
        cid = ev.criterion_id
        if only_non_en or whole and not english_turns:
            score = min(score, 0.15 * maximum)
        elif last_non_en and cid in {
            "technical_accuracy",
            "completeness",
            "reasoning_and_justification",
        }:
            # Probe answer was non-English — do not let that inflate technical credit.
            score = min(score, 0.55 * maximum)
        capped.append(ev.model_copy(update={"raw_score": round(score, 2)}))

    rationale = evaluation.overall_rationale or ""
    note = (
        "Non-English candidate speech detected; technical credit clamped per "
        "english_only policy."
    )
    if note not in rationale:
        rationale = f"{rationale} {note}".strip()

    return evaluation.model_copy(
        update={
            "criterion_evidence": capped,
            "flags": flags,
            "overall_rationale": rationale,
            "evaluation_confidence": max(float(evaluation.evaluation_confidence), 0.75),
        }
    )


def _rubric_from_payload(item: BankItem) -> dict:
    payload = item.payload or {}
    # Prefer weight_pct-aligned defaults matching the global open grading rubric.
    default_weights = {
        "technical_accuracy": 0.40,
        "completeness": 0.25,
        "reasoning_and_justification": 0.20,
        "clarity_and_communication": 0.15,
    }
    criteria = []
    raw_criteria = payload.get("rubric_criteria") or []
    for crit in raw_criteria:
        comp = crit.get("competency_id") or (
            item.measures[0].variable if item.measures else ""
        )
        cid = crit["criterion_id"]
        criteria.append(
            {
                "criterion_id": cid,
                "competency_id": comp,
                "weight": float(
                    crit.get("weight", default_weights.get(cid, 1.0 / max(len(raw_criteria), 1)))
                ),
                "required": bool(crit.get("required", cid != "clarity_and_communication")),
                "maximum_score": float(crit.get("maximum_score", 5)),
                "descriptor": crit.get("descriptor", ""),
            }
        )
    return {
        "rubric_id": payload.get("rubric_id", f"inline_{item.item_id}"),
        "version": "1.0",
        "criteria": criteria,
        # Voice items author the same two things under different names. Without the
        # aliases the grader prompt loses the answer checklist and the worked exemplar,
        # and still returns a grade — a quietly worse one.
        "expected_answer_points": (
            payload.get("expected_answer_points")
            or payload.get("evaluation_criteria")
            or []
        ),
        "common_pitfalls": payload.get("common_pitfalls", []),
        "reference_answer": (
            payload.get("reference_answer")
            or payload.get("sample_strong_answer")
            or ""
        ),
    }


def _heuristic_from_rubric(
    item_id: str, rubric: dict, package: VoiceResponsePackage, flags: list[str]
) -> VoiceEvaluation:
    words = max(package.word_count, len(package.transcript.split()))
    frac = min(words / 180.0, 1.0)
    evidence = []
    turn_id = package.turns[0].turn_id if package.turns else "t0"
    for crit in rubric.get("criteria", []):
        maximum = float(crit.get("maximum_score", 4))
        evidence.append(
            {
                "criterion_id": crit["criterion_id"],
                "competency_id": crit["competency_id"],
                "raw_score": round(frac * maximum * 0.6, 2),
                "maximum_score": maximum,
                "confidence": 0.4,
                "prompt_dependency": "independent",
                "quote": None,
                "quote_turn_id": turn_id,
                "description": "heuristic fallback — model unavailable",
            }
        )
    reply = {
        "item_id": item_id,
        "rubric_id": rubric.get("rubric_id"),
        "rubric_version": rubric.get("version", "1.0"),
        "criterion_evidence": evidence,
        "evaluation_confidence": 0.35,
        "overall_rationale": "heuristic fallback",
    }
    ev = validate(reply, item_id, rubric, package)
    ev.flags = list(dict.fromkeys([*flags, *ev.flags]))
    ev.degraded = True
    return ev
