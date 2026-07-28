import json
import asyncio
from datetime import datetime, timezone
from fastapi import BackgroundTasks
from supabase import AsyncClient
from openai import AsyncOpenAI

from app.agent.planner import generate_plan, generate_multi_mode_plan, _apply_type_config, LEGACY_TO_MODE
from app.services.template_config import competency_names, resolve_modes, resolve_adaptivity
from app.agent.generator import generate_question, generate_coding_challenge
from app.services.memory import create_memory_card, get_memory_cards
from app.services.learner_profile import get_or_create_profile, update_profile, _read_skill_entry
from app.services import memory_store
from app.services.grading import grade_open_ended, grade_mcq, grade_code_llm, grade_video_visual
from app.services.email import send_report_email
from app.schemas.report import SkillScore
from app.config.settings import settings

SKILLS = ["thinking", "soft", "work", "digital_ai", "growth"]

_UP   = {"easy": "medium", "medium": "hard",   "hard": "hard"}
_DOWN = {"easy": "easy",   "medium": "easy",   "hard": "medium"}

def _adjust_difficulty(planned: str, skill_score: float) -> str:
    if skill_score >= 3.5:
        return _UP.get(planned, planned)
    if skill_score <= 1.5:
        return _DOWN.get(planned, planned)
    return planned


async def _grade_answer(
    tool_type: str,
    question_data: dict,
    tool_result: dict,
) -> dict:
    skill: str = question_data.get("skill_target", "thinking")
    body: str = question_data.get("body", "")
    payload: dict = question_data.get("payload") or {}

    # Candidate explicitly skipped this question → score 0 and advance, no LLM call.
    if tool_result.get("skipped"):
        return {
            "overall_score": 0.0,
            "skill_scores":  {skill: 0.0},
            "skill":         skill,
            "rationale":     "Skipped by the candidate.",
            "feedback":      "",
            "skipped":       True,
        }

    if tool_type == "mcq":
        answer_key = payload.get("answer_key") or {}
        selected = tool_result.get("selected_id", "")
        result = grade_mcq(answer_key, selected)
        result["skill_scores"] = {skill: result["overall_score"]}
        result["skill"] = skill

    elif tool_type == "coding":
        code = tool_result.get("code", "")
        challenge = {
            "body":              question_data.get("body", ""),
            "test_cases":        payload.get("test_cases") or [],
            "expected_approach": payload.get("expected_approach", ""),
            "constraints":       payload.get("constraints", ""),
        }
        result = await grade_code_llm(challenge, code)
        result["skill_scores"] = {skill: result["overall_score"]}
        result["skill"] = skill

    else:
        answer_text = (
            tool_result.get("transcript")
            or tool_result.get("answer_text")
            or (tool_result.get("submission_result") or {}).get("rationale")
            or ""
        )
        rubric = (
            payload.get("evaluation_criteria")
            or payload.get("competency_signals")
            or payload.get("expected_insights")
            or payload.get("rubric")
        )
        result = await grade_open_ended(body, rubric, answer_text, skill)
        result["skill"] = skill

    # P4 E: for VIDEO answers, also score the visual delivery from sampled frames and
    # blend it with the transcript content score (70% content, 30% presentation).
    if tool_type == "video" and tool_result.get("frames"):
        try:
            visual = await grade_video_visual(body, tool_result.get("frames") or [])
            vscore = float(visual.get("visual_score") or 0)
            base = float(result.get("overall_score") or 0)
            combined = round(0.7 * base + 0.3 * vscore, 2)
            result["overall_score"] = combined
            result["skill_scores"] = {skill: combined}
            result["visual_score"] = vscore
            result["visual_rationale"] = visual.get("rationale", "")
            result["rationale"] = (result.get("rationale") or "") + f" [Visual delivery {vscore:.1f}/5: {visual.get('rationale','')}]"
        except Exception as e:
            print(f"[Grade] video visual scoring skipped: {e}")

    return result


def _compute_verdict(score: float) -> str:
    if score >= 3.5:
        return "knows"
    elif score >= 2.0:
        return "partial"
    return "gap"


TRACKS_RIASEC = """
The 9 Sprints.ai tracks with personality archetypes and RIASEC mappings:
- front-end / Front End Development (The Craftsman): Realistic + Artistic. Loves creating visually precise, interactive user experiences. Cares deeply about craft and front-facing quality.
- back-end / Back End Development (The Architect): Realistic + Investigative. Systematic, enjoys designing reliable systems, understanding how things work under the hood.
- ai-ml / AI & Machine Learning (The Scientist): Investigative dominant. Driven by curiosity, loves finding patterns, thrives in open-ended research.
- data-analytics / Data Analytics (The Detective): Investigative + Conventional. Methodical, finds meaning in data, loves proving or disproving hypotheses with evidence.
- product-management / Product Management (The Orchestrator): Enterprising + Social. Strategic communicator who sees the big picture, aligns people, defines what to build and why.
- ui-ux / UI & UX Design (The Artisan): Artistic + Social. Empathetic, visually sensitive, passionate about human-centered design.
- digital-marketing / Digital Marketing (The Amplifier): Enterprising + Artistic. Cultural storyteller who blends creativity with performance data to drive growth.
- mobile / Mobile Development (The Builder): Realistic + Artistic. Hands-on engineer with a strong UX sense, loves building apps people interact with daily.
- software-testing / Software Testing (The Guardian): Conventional + Investigative. Detail-obsessed, systematic thinker who protects quality and finds what others miss.
"""


async def _generate_discovery_report(
    answers: list,
    skill_scores_obj: dict,
    cv_data: dict,
    session: dict,
) -> dict:
    """Use LLM to map assessment signals to a track recommendation."""
    qa_summary = "\n".join(
        f"Q{i+1} (score {float(a.get('score', 0)):.1f}/5): {(a.get('grading_rationale') or '')[:250]}"
        for i, a in enumerate(answers)
    ) or "No answers recorded."

    skill_summary = ", ".join(
        f"{s}: {v.score:.1f}/5" for s, v in skill_scores_obj.items()
    )

    cv_summary = (cv_data.get("raw_summary") or "")[:400]

    prompt = f"""You are a career counselor analyzing a discovery assessment to recommend the best career track.

CANDIDATE:
Name: {session.get('candidate_name') or 'Candidate'}
CV summary: {cv_summary or 'Not provided'}

APTITUDE SIGNAL SCORES (from assessment questions):
{skill_summary}

QUESTION-BY-QUESTION EVIDENCE:
{qa_summary}

{TRACKS_RIASEC}

Based on the candidate's aptitude signals, CV background, and answer patterns,
determine which of the 9 tracks fits them best. Rank the top 3.

Return ONLY valid JSON:
{{
  "recommended_track": "slug (e.g. data-analytics)",
  "recommended_track_name": "Full Name (e.g. Data Analytics)",
  "personality_archetype": "The Detective",
  "top_tracks": [
    {{"slug": "data-analytics", "name": "Data Analytics", "fit_score": 8.5, "reasoning": "2 sentences of specific evidence from the assessment"}},
    {{"slug": "ai-ml", "name": "AI & Machine Learning", "fit_score": 7.1, "reasoning": "2 sentences"}},
    {{"slug": "software-testing", "name": "Software Testing", "fit_score": 5.8, "reasoning": "2 sentences"}}
  ],
  "personality_signals": ["trait1", "trait2", "trait3"],
  "riasec_signals": {{"R": 40, "I": 85, "A": 30, "S": 45, "E": 55, "C": 70}},
  "summary": "2-3 sentences explaining the overall recommendation with specific evidence from their answers"
}}"""

    llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
    res = await llm.chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    return json.loads(res.choices[0].message.content)


MBTI_TYPES: dict[str, dict] = {
    "INTJ": {"name": "The Architect",    "tagline": "Strategic, private, and driven by original ideas."},
    "INTP": {"name": "The Thinker",      "tagline": "Innovative, logical, and hungry for ideas."},
    "ENTJ": {"name": "The Commander",    "tagline": "Bold, decisive, and focused on long-term goals."},
    "ENTP": {"name": "The Debater",      "tagline": "Quick-witted, curious, and loves a good challenge."},
    "INFJ": {"name": "The Advocate",     "tagline": "Purposeful, idealistic, and deeply empathetic."},
    "INFP": {"name": "The Mediator",     "tagline": "Creative, empathetic, and guided by inner values."},
    "ENFJ": {"name": "The Protagonist",  "tagline": "Charismatic, inspiring, and natural-born leader."},
    "ENFP": {"name": "The Campaigner",   "tagline": "Enthusiastic, creative, and deeply people-focused."},
    "ISTJ": {"name": "The Inspector",    "tagline": "Dependable, detail-oriented, and values tradition."},
    "ISFJ": {"name": "The Defender",     "tagline": "Caring, loyal, and dedicated to protecting others."},
    "ESTJ": {"name": "The Executive",    "tagline": "Organized, practical, and committed to order."},
    "ESFJ": {"name": "The Consul",       "tagline": "Warm, caring, and focused on community harmony."},
    "ISTP": {"name": "The Craftsman",    "tagline": "Observant, logical, and masters of tools and methods."},
    "ISFP": {"name": "The Adventurer",   "tagline": "Flexible, charming, and always ready to explore."},
    "ESTP": {"name": "The Entrepreneur", "tagline": "Energetic, perceptive, and driven by action."},
    "ESFP": {"name": "The Entertainer",  "tagline": "Spontaneous, energetic, and lights up any room."},
}


async def _generate_personality_report(
    answers: list,
    gq_map: dict,
    session: dict,
    db: AsyncClient,
    background_tasks: BackgroundTasks,
) -> dict:
    """Determine MBTI type from poll of answers and generate a personality report."""
    session_id = session["id"]

    # ── Count poles per dimension ────────────────────────────────────────────
    dim_votes: dict[str, dict[str, int]] = {
        "EI": {"E": 0, "I": 0},
        "NS": {"N": 0, "S": 0},
        "TF": {"T": 0, "F": 0},
        "JP": {"J": 0, "P": 0},
    }
    for a in answers:
        qnum    = a.get("question_number", 0)
        gq      = gq_map.get(qnum, {})
        payload = gq.get("payload") or {}
        ak      = payload.get("answer_key") or {}
        dim     = ak.get("dimension") or gq.get("skill_target", "")
        if dim not in dim_votes:
            continue
        try:
            answer_data = json.loads(a.get("answer_text") or "{}")
        except Exception:
            answer_data = {}
        selected = answer_data.get("selected_id", "")
        pole = ak.get(f"pole_{selected}")
        if pole and pole in dim_votes.get(dim, {}):
            dim_votes[dim][pole] += 1

    # ── Resolve 4-letter type ────────────────────────────────────────────────
    mbti_type = ""
    dim_scores: dict[str, float] = {}
    for dim in ["EI", "NS", "TF", "JP"]:
        votes = dim_votes[dim]
        poles = list(votes.keys())
        total = sum(votes.values()) or 1
        if votes[poles[0]] >= votes[poles[1]]:
            mbti_type += poles[0]
            dim_scores[dim] = round(votes[poles[0]] / total, 2)
        else:
            mbti_type += poles[1]
            dim_scores[dim] = round(votes[poles[1]] / total, 2)

    type_info = MBTI_TYPES.get(mbti_type, {"name": "Unknown", "tagline": ""})
    candidate_name = session.get("candidate_name") or "Candidate"
    candidate_first = candidate_name.split()[0]

    # ── LLM-generated description ───────────────────────────────────────────
    llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
    desc_res = await llm.chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": f"""Write a warm, insightful personality overview for {candidate_first}.

Their MBTI type: {mbti_type} — {type_info['name']} ("{type_info['tagline']}")
Dimension strengths: {json.dumps(dim_scores)}

Write 3 sentences:
1. Acknowledge their type with warmth (mention {mbti_type} and {type_info['name']})
2. Name 2 natural strengths based on the type
3. Name 1 growth edge and a practical suggestion

Be specific to the type — no generic advice. Start with "{candidate_first}, you came out as..."
Return ONLY the 3 sentences, no JSON, no headers."""}],
        temperature=0.6,
        max_tokens=200,
    )
    description = desc_res.choices[0].message.content.strip()

    # ── Persist report ───────────────────────────────────────────────────────
    # skill_scores keys = MBTI dimensions; score = preference strength (0-1 normalized to 0-5)
    skill_scores_payload = {
        dim: {"score": round(dim_scores.get(dim, 0.5) * 5, 2), "evidence": []}
        for dim in ["EI", "NS", "TF", "JP"]
    }

    existing = await db.table("final_reports").select("id").eq("session_id", session_id).execute()
    if not existing.data:
        await db.table("final_reports").insert({
            "session_id":       session_id,
            "skill_scores":     skill_scores_payload,
            "total_score":      round(sum(v["score"] for v in skill_scores_payload.values()), 2),
            "placement":        mbti_type,
            "feedback":         description,
            "recommendations":  [f"Explore career paths that suit {type_info['name']} personalities — look for roles that reward {type_info['tagline'].rstrip('.')}"],
            "integrity_status": session.get("integrity_status") or "clean",
        }).execute()

    await db.table("candidate_sessions").update({
        "status":       "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", session_id).execute()

    return {
        "mbti_type":  mbti_type,
        "type_name":  type_info["name"],
        "tagline":    type_info["tagline"],
        "description": description,
        "dim_votes":  dim_votes,
        "dim_scores": dim_scores,
    }


async def _finalize_session(
    session: dict,
    answered_count: int,
    total_slots: int,
    elapsed_minutes: float,
    time_limit: int,
    reason: str,          # "time" | "submitted" | "all_done"
    db: AsyncClient,
    background_tasks: BackgroundTasks,
) -> dict:
    """Generate report, save it, update session status, fire email. Returns agent_msg."""
    session_id = session["id"]

    answers_res = await db.table("candidate_answers").select("*").eq("session_id", session_id).execute()
    answers: list = answers_res.data or []

    skill_totals: dict[str, list[float]] = {}
    for a in answers:
        scores: dict = a.get("skill_scores") or {a.get("skill") or "thinking": a.get("score") or 0}
        for s, v in scores.items():
            skill_totals.setdefault(s, []).append(float(v or 0))

    profile = await get_or_create_profile(db, session_id)

    # ── Fetch template for domain context (must come before skill aggregation) ─
    try:
        tpl_res = await db.table("assessment_templates").select(
            "title, track, admin_prompt, assessment_type, modes, tool_config"
        ).eq("id", session.get("template_id", "")).execute()
        template_row = tpl_res.data[0] if tpl_res.data else {}
    except Exception:
        template_row = {}

    # Multi-mode assessments report on the generic competency path, not a single
    # mode's report (discovery/hr/personality).
    if len(resolve_modes(template_row)) > 1:
        template_row = {**template_row, "assessment_type": "track"}

    template_title        = template_row.get("title", "")
    template_track        = template_row.get("track", "")
    template_domain       = (template_row.get("admin_prompt") or "").strip()
    is_discovery_session  = template_row.get("assessment_type") == "discover"
    is_personality_session = template_row.get("assessment_type") == "personality"
    is_hr_session          = template_row.get("assessment_type") == "hr"

    # Dynamic competencies replace the 5 hardcoded SKILLS when set by admin.
    # competency_names() tolerates both legacy list[str] and P1 [{name, tag}],
    # so the final report's skill axis == the configured competencies.
    _tool_cfg: dict = template_row.get("tool_config") or {}
    _competencies: list[str] = competency_names(_tool_cfg)
    active_skills: list[str] = _competencies if _competencies else SKILLS

    # R2.3 default: on the default 5-skill axis (no custom competencies), a
    # behavioural/HR mode records scores under soft-skill names (Communication,
    # Leadership, …) that aren't among the 5 skills — so they'd be dropped from the
    # report. Fold those recognized behavioural scores into the default "soft"
    # dimension so HR performance still counts. Only recognized HR names are folded,
    # leaving discovery/MBTI/custom-competency scores untouched.
    if not _competencies:
        _HR_COMPETENCY_NAMES = {
            "communication", "leadership", "problem solving", "teamwork",
            "adaptability", "conflict resolution", "initiative", "customer focus",
        }
        for _k in list(skill_totals.keys()):
            if _k not in SKILLS and str(_k).strip().lower() in _HR_COMPETENCY_NAMES:
                skill_totals.setdefault("soft", []).extend(skill_totals.pop(_k))

    skill_scores_obj: dict[str, SkillScore] = {}
    total = 0.0
    for skill in active_skills:
        vals = skill_totals.get(skill) or [0.0]
        avg = sum(vals) / len(vals)
        skill_scores_obj[skill] = SkillScore(score=round(avg, 2), evidence=[])
        total += avg

    n_skills = len(active_skills) or 1
    max_total = n_skills * 5.0
    if _competencies:
        # Custom competencies: placement based on percentage of total
        placement = "PRO" if (total / max_total) >= 0.72 else "BEGINNER"
    else:
        thinking_score = skill_scores_obj.get("thinking", SkillScore(score=0.0, evidence=[])).score
        placement = "PRO" if total >= 18.0 and thinking_score >= 3.0 else "BEGINNER"

    # ── Discovery branch: generate track recommendation instead of PRO/BEGINNER ─
    if is_discovery_session:
        cv_data_state = session.get("agent_state") or {}
        cv_data_for_report = cv_data_state.get("cv_data") or {}
        discovery_result = await _generate_discovery_report(answers, skill_scores_obj, cv_data_for_report, session)

        existing = await db.table("final_reports").select("id").eq("session_id", session_id).execute()
        if not existing.data:
            await db.table("final_reports").insert({
                "session_id":       session_id,
                "skill_scores":     {k: {"score": v.score, "evidence": v.evidence} for k, v in skill_scores_obj.items()},
                "total_score":      round(total, 2),
                "placement":        "DISCOVER",
                "feedback":         discovery_result.get("summary") or "",
                "discovery_result": discovery_result,
                "integrity_status": session.get("integrity_status") or "clean",
            }).execute()

        await db.table("candidate_sessions").update({
            "status":       "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", session_id).execute()

        rec_name = discovery_result.get("recommended_track_name") or "your recommended track"
        return {
            "role":        "agent",
            "content": (
                f"You've completed the career discovery assessment, {session.get('candidate_name') or 'there'}! "
                f"Based on your responses, we've identified **{rec_name}** as your best-fit track. "
                f"Head to your results page to see the full breakdown with personality insights and your top 3 track matches."
            ),
            "tool_type":    None,
            "tool_payload": None,
        }

    # ── Fetch generated questions to join with answers ───────────────────────
    try:
        gq_res = await db.table("generated_questions").select(
            "question_number, tool_type, topic, skill_target, difficulty, payload"
        ).eq("session_id", session_id).order("question_number").execute()
        gq_map = {q["question_number"]: q for q in (gq_res.data or [])}
    except Exception:
        gq_map = {}

    # ── Personality branch: MBTI type determination ──────────────────────────
    if is_personality_session:
        result = await _generate_personality_report(answers, gq_map, session, db, background_tasks)
        mbti = result["mbti_type"]
        return {
            "role":     "agent",
            "content": (
                f"{session.get('candidate_name') or 'You'}, your results are in! "
                f"You came out as **{mbti} — {result['type_name']}**. "
                f"{result['tagline']} "
                f"Your full personality breakdown is on the results page."
            ),
            "tool_type":    None,
            "tool_payload": None,
        }

    # ── HR assessment branch: behavioral debrief, no PRO/BEGINNER ──────────────
    if is_hr_session:
        candidate_name = session.get("candidate_name") or "Candidate"
        candidate_first = candidate_name.split()[0]

        # Competency-by-competency performance summary
        soft_skill_summary = "\n".join(
            f"  {s}: {skill_scores_obj.get(s, SkillScore(score=0.0, evidence=[])).score:.1f}/5"
            for s in active_skills
        )

        qa_evidence = "\n".join(
            f"Q{i+1} [{a.get('skill','?')}] score {float(a.get('score',0)):.1f}/5: {(a.get('grading_rationale') or '')[:200]}"
            for i, a in enumerate(answers[:8])
        ) or "No answers recorded."

        hr_prompt = f"""You are a senior HR assessor writing a professional behavioral assessment debrief.

CANDIDATE: {candidate_name}
SOFT SKILL SCORES (each /5):
{soft_skill_summary}

QUESTION-BY-QUESTION EVIDENCE:
{qa_evidence}

Write an honest, supportive behavioral assessment. Rules:
1. Address {candidate_first} directly throughout
2. Be SPECIFIC — name actual behaviors observed in answers, not generic praise
3. Focus on behavioral patterns and professional development, NOT technical courses
4. coaching_suggestions should be concrete behavioral actions (e.g. "Practice the STAR format for answering conflict questions")

Return ONLY valid JSON:
{{
  "summary": "2-3 sentences addressing {candidate_first} directly. Name their strongest soft skill with evidence. Name the main development area.",
  "went_well": ["Specific strength with behavioral evidence — 2 items"],
  "needs_improvement": ["Specific development area with observed behavior — 2 items"],
  "coaching_suggestions": ["Concrete behavioral action {candidate_first} can practice — 3 items"]
}}"""

        llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
        hr_res = await llm.chat.completions.create(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": hr_prompt}],
            response_format={"type": "json_object"},
            temperature=0.5,
        )
        try:
            from json_repair import repair_json
            hr_data: dict = json.loads(repair_json(hr_res.choices[0].message.content))
        except Exception:
            hr_data = {"summary": "", "went_well": [], "needs_improvement": [], "coaching_suggestions": []}

        existing = await db.table("final_reports").select("id").eq("session_id", session_id).execute()
        if not existing.data:
            await db.table("final_reports").insert({
                "session_id":        session_id,
                "skill_scores":      {k: {"score": v.score, "evidence": v.evidence} for k, v in skill_scores_obj.items()},
                "total_score":       round(total, 2),
                "placement":         "HR_ASSESSED",
                "feedback":          hr_data.get("summary") or "",
                "went_well":         hr_data.get("went_well") or [],
                "needs_improvement": hr_data.get("needs_improvement") or [],
                "recommendations":   hr_data.get("coaching_suggestions") or [],
                "integrity_status":  session.get("integrity_status") or "clean",
            }).execute()

        await db.table("candidate_sessions").update({
            "status":       "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", session_id).execute()

        return {
            "role": "agent",
            "content": (
                f"Thank you, {candidate_first} — your HR assessment is complete. "
                f"You answered {answered_count} questions covering {len(active_skills)} competency area{'s' if len(active_skills) != 1 else ''}. "
                f"Your full behavioral report has been prepared — check your results page for the breakdown."
            ),
            "tool_type":    None,
            "tool_payload": None,
        }

    # ── Build per-question and per-type performance context ──────────────────
    # Default slug → display label map; custom competency names are used as-is
    SKILL_LABELS = {
        "thinking":   "Critical Thinking",
        "soft":       "Communication & Soft Skills",
        "work":       "Technical Execution",
        "digital_ai": "Digital & AI Literacy",
        "growth":     "Learning & Adaptability",
    }
    TOOL_LABELS = {
        "mcq":           "Multiple Choice",
        "voice":         "Verbal Interview",
        "coding":        "Coding Challenge",
        "visualization": "Data Analysis",
        "task":          "Practical Task",
    }

    type_scores: dict[str, list[float]] = {}
    per_q: list[dict] = []
    for a in answers:
        qnum   = a.get("question_number", 0)
        q_meta = gq_map.get(qnum, {})
        tt     = q_meta.get("tool_type", "unknown")
        topic  = q_meta.get("topic") or a.get("skill") or "general"
        score  = float(a.get("score") or 0)
        diff   = q_meta.get("difficulty", "medium")
        type_scores.setdefault(tt, []).append(score)
        per_q.append({"type": tt, "topic": topic, "score": score, "difficulty": diff})

    sorted_q   = sorted(per_q, key=lambda x: x["score"])
    gap_topics = [f"{q['topic']} ({q['score']:.1f}/5)" for q in sorted_q if q["score"] < 2.5][:4]
    strong_topics = [f"{q['topic']} ({q['score']:.1f}/5)" for q in reversed(sorted_q) if q["score"] >= 3.5][:4]

    type_summary = "\n".join(
        f"  {TOOL_LABELS.get(t, t)}: avg {sum(s)/len(s):.1f}/5 ({len(s)} question{'s' if len(s)>1 else ''})"
        for t, s in type_scores.items()
    ) or "  Not available"

    skill_summary = "\n".join(
        f"  {SKILL_LABELS.get(s, s)}: {skill_scores_obj[s].score:.1f}/5  "
        f"({'strong' if skill_scores_obj[s].score >= 3.5 else 'moderate' if skill_scores_obj[s].score >= 2.5 else 'developing' if skill_scores_obj[s].score >= 1.5 else 'significant gap'})"
        for s in active_skills
    )

    assessment_context = (
        template_domain
        or (f"{template_title} — {template_track}" if template_title else "General technical assessment")
    )

    # Pre-compute values used inside the prompt (avoids lambda-in-f-string issues)
    lowest_skill       = min(active_skills, key=lambda s: skill_scores_obj[s].score)
    highest_skill      = max(active_skills, key=lambda s: skill_scores_obj[s].score)
    lowest_skill_label = SKILL_LABELS.get(lowest_skill, lowest_skill)
    highest_skill_label = SKILL_LABELS.get(highest_skill, highest_skill)
    lowest_skill_score = skill_scores_obj[lowest_skill].score
    highest_skill_score = skill_scores_obj[highest_skill].score
    gap_topic_hint     = gap_topics[0] if gap_topics else "core fundamentals"
    score_pct          = total / max_total if max_total > 0 else 0
    score_label        = ("excellent" if score_pct >= 0.80 else "good" if score_pct >= 0.60
                          else "below average" if score_pct >= 0.40 else "low")
    field_label        = template_track or "this technical domain"

    # Sprints.ai course library — passed to LLM so it can pick the most relevant ones
    SPRINTS_LIBRARY = """
Available Sprints.ai courses (recommend 2–3 most relevant based on track and gaps):
- Front End Specialization (341 hrs · HTML, CSS, JS, React) → https://sprints.ai/en-eg/sprint-up/plus/4
- Back End Specialization (434 hrs · Node.js, REST APIs, Databases) → https://sprints.ai/en-eg/sprint-up/plus/8
- AI & Machine Learning Specialization (453 hrs · end-to-end ML pipeline) → https://sprints.ai/en-eg/sprint-up/plus/3
- Data Analytics Specialization (275 hrs · SQL, BI, storytelling with data) → https://sprints.ai/en-eg/sprint-up/plus/9
- Product Management Specialization (351 hrs · strategy, roadmaps, agile) → https://sprints.ai/en-eg/sprint-up/plus/5
- Digital Marketing Specialization (402 hrs · performance, SEO, content) → https://sprints.ai/en-eg/sprint-up/plus/6
- Mobile Development / Flutter (327 hrs · iOS & Android with Flutter) → https://sprints.ai/en-eg/sprint-up/plus/7
- Software Testing Specialization (284 hrs · ISTQB, automation, QA) → https://sprints.ai/en-eg/sprint-up/plus/11
- UI/UX Design Specialization (323 hrs · user research, Figma, interface) → https://sprints.ai/en-eg/sprint-up/plus/10
- Generative AI for Job Seekers (59 hrs · AI-enhanced CV & interviews) → https://sprints.ai/en-eg/sprint-up/plus/16
- Generative AI for Entrepreneurs (60 hrs · build & scale with AI) → https://sprints.ai/en-eg/sprint-up/plus/15
- DevOps Foundations (80 hrs · CI/CD, Docker, cloud basics) → https://sprints.ai/en-eg/journeys/SprintUpDevOpsfoundations/project/1
- Cybersecurity Fundamentals (80 hrs · network security, ethical hacking basics) → https://sprints.ai/en-eg/journeys/SprintUpCyberSecurity-7048868/project/1
- Web Development Fundamentals (80 hrs · beginner HTML/CSS/JS) → https://sprints.ai/en-eg/journeys/WebDevelopmentFundamentals-7022284/project/1
- Programming with Python (80 hrs · Python beginner to intermediate) → https://sprints.ai/en-eg/journeys/Programming-with-Python/project/1"""

    candidate_name: str = session.get("candidate_name") or "Candidate"
    candidate_first: str = candidate_name.split()[0]

    # ── Build the feedback prompt ────────────────────────────────────────────
    feedback_prompt = f"""You are a senior technical assessor writing a personalised, agile-retro-style assessment debrief.

ASSESSMENT CONTEXT
──────────────────
Title:  {template_title or "Technical Assessment"}
Domain: {assessment_context}
Track:  {field_label}

CANDIDATE
─────────
Name:               {candidate_name}
Placement:          {placement}
Total Score:        {total:.1f} / {max_total:.0f}  ({score_label})
Questions answered: {answered_count} of {total_slots}

Skill Scores (each out of 5):
{skill_summary}

Performance by question type:
{type_summary}

Strongest topics:  {strong_topics or profile.get('known_topics', [])[:5]}
Weakest topics:    {gap_topics or profile.get('gap_topics', [])[:5]}

{SPRINTS_LIBRARY}

WRITING RULES
─────────────
1. Address the candidate by their first name "{candidate_first}" throughout — NEVER say "The candidate".
2. Be SPECIFIC to "{assessment_context}". Name actual skills and topics — never generic.
3. Be HONEST. {total:.1f}/{max_total:.0f} is {score_label}. State it plainly.
4. FORBIDDEN phrases: "positive foundation", "essential journey", "robust skill set", "dedicated focus", "great start", "The candidate".
5. "went_well" entries must name a skill label + score from the data above as evidence.
6. "needs_improvement" entries must name a skill label + score, and explain the concrete impact in {field_label}.
7. Each recommendation must include an action verb AND a specific Sprints.ai course link from the library above.
   Format each recommendation exactly as: "ACTION — Course Name (X hrs) → URL"
   Example: "Rebuild React component fundamentals through hands-on projects — Front End Specialization (341 hrs) → https://sprints.ai/en-eg/sprint-up/plus/4"
8. Pick 2–3 courses most relevant to the track "{field_label}" and the weakest skill areas.

Return ONLY valid JSON — no markdown, no extra keys:
{{
  "summary": "2-3 sentences starting with '{candidate_first}, you...'. S1: '{placement}' placement, {total:.1f}/{max_total:.0f} in {assessment_context}. S2: name the strongest skill ({highest_skill_label} at {highest_skill_score:.1f}/5) with a specific observation. S3: name the biggest gap ({lowest_skill_label} at {lowest_skill_score:.1f}/5) and what it means for {field_label}.",
  "went_well": [
    "Skill label (score/5): specific positive observation from the candidate's performance — what they demonstrated and where",
    "Another strength with evidence — 2 items total"
  ],
  "needs_improvement": [
    "Skill label (score/5): specific gap — what was missing and why it matters in {field_label}",
    "Another gap with impact — 2 items total"
  ],
  "recommendations": [
    "ACTION targeting {lowest_skill_label} gap — Relevant Sprints Course (X hrs) → https://sprints.ai/...",
    "ACTION targeting gap topic '{gap_topic_hint}' — Relevant Sprints Course (X hrs) → https://sprints.ai/...",
    "ACTION to build practical {field_label} experience — Relevant Sprints Course (X hrs) → https://sprints.ai/..."
  ]
}}"""

    llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
    feedback_res = await llm.chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": feedback_prompt}],
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    feedback_data: dict = json.loads(feedback_res.choices[0].message.content)

    went_well        = feedback_data.get("went_well") or []
    needs_improvement = feedback_data.get("needs_improvement") or []
    summary          = feedback_data.get("summary") or feedback_data.get("feedback") or ""

    # Guard: only insert report if one doesn't already exist for this session
    existing = await db.table("final_reports").select("id").eq("session_id", session_id).execute()
    if not existing.data:
        await db.table("final_reports").insert({
            "session_id":        session_id,
            "skill_scores":      {k: {"score": v.score, "evidence": v.evidence} for k, v in skill_scores_obj.items()},
            "total_score":       round(total, 2),
            "placement":         placement,
            "feedback":          summary,
            "went_well":         went_well,
            "needs_improvement": needs_improvement,
            "recommendations":   feedback_data.get("recommendations") or [],
            "integrity_status":  session.get("integrity_status") or "clean",
        }).execute()

    await db.table("candidate_sessions").update({
        "status":       "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", session_id).execute()

    background_tasks.add_task(
        send_report_email,
        candidate_name=session.get("candidate_name") or "Candidate",
        candidate_email=session.get("candidate_email") or "",
        admin_email=settings.report_from_email,
        placement=placement,
        total_score=round(total, 2),
        skill_scores={k: {"score": v.score} for k, v in skill_scores_obj.items()},
        feedback=summary,
        went_well=went_well,
        needs_improvement=needs_improvement,
        recommendations=feedback_data.get("recommendations") or [],
        session_id=session_id,
        base_url=settings.base_url,
        template_title=template_title,
        integrity_status=session.get("integrity_status") or "clean",
    )

    if reason == "time":
        note = f" Time limit reached ({int(elapsed_minutes)}/{time_limit} min). {total_slots - answered_count} question(s) not reached."
    elif reason == "submitted":
        note = f" You chose to submit early ({answered_count}/{total_slots} questions answered)."
    else:
        note = ""

    return {
        "role":        "agent",
        "content": (
            f"Well done, {session.get('candidate_name') or 'Candidate'}! "
            f"Your assessment is complete.{note} "
            f"You've been placed as **{placement}** with a total score of "
            f"{round(total, 2):.1f}/{max_total:.0f}. Your full report has been emailed to you."
        ),
        "tool_type":    None,
        "tool_payload": None,
    }


async def _build_set_retro(completed_type: str, scores: list[float], next_type: str | None) -> str:
    """Call the LLM to write a short, human-sounding retro between assessment sections."""
    if not scores:
        return ""

    avg   = sum(scores) / len(scores)
    count = len(scores)
    best  = max(scores)
    worst = min(scores)

    SECTION_NAMES = {
        "mcq":           "multiple-choice questions",
        "voice":         "verbal interview",
        "coding":        "coding challenge",
        "visualization": "data analysis task",
        "task":          "practical task",
    }
    NEXT_NAMES = {
        "mcq":           "multiple-choice section",
        "voice":         "verbal interview",
        "coding":        "coding challenge",
        "visualization": "data analysis task",
        "task":          "a practical task",
    }

    spread_note = (
        f" The scores ranged from {worst:.1f} to {best:.1f}, so some answers were much stronger than others."
        if count > 1 and (best - worst) >= 1.5 else ""
    )
    next_note = f" The next section is the {NEXT_NAMES.get(next_type, 'next section')}." if next_type else ""

    prompt = f"""You are an assessment facilitator giving a quick verbal check-in between sections of a live assessment.

Context:
- Section just finished: {SECTION_NAMES.get(completed_type, completed_type)} ({count} question{"s" if count > 1 else ""})
- Average score: {avg:.1f} / 5{spread_note}{next_note}

Write 2–3 sentences of honest, conversational feedback. Rules:
1. Sound like a real human speaking, not a written report — informal but professional
2. Be truthful about the score: {avg:.1f}/5 is {"excellent" if avg >= 4 else "good" if avg >= 3 else "okay but with gaps" if avg >= 2 else "low and worth noting"}
3. Don't open with "Great job", "Well done", or "Excellent" — vary your phrasing
4. If avg < 2.5, be direct but not harsh — acknowledge gaps without dwelling on them
5. End with a natural transition to the next section if there is one
6. No markdown, no bullet points, no headers — just plain conversational sentences
7. Use "you" throughout

Output only the 2–3 sentences. Nothing else."""

    try:
        llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)
        res = await llm.chat.completions.create(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.75,
            max_tokens=140,
        )
        retro = res.choices[0].message.content.strip()
        return f"\n\n{retro}\n\n"
    except Exception as e:
        print(f"[Retro] LLM call failed: {e}")
        return ""


async def run_turn(
    session: dict,
    user_message: str,
    tool_result: dict | None,
    db: AsyncClient,
    background_tasks: BackgroundTasks,
) -> dict:
    state: dict = session.get("agent_state") or {}
    session_id: str = session["id"]

    # ── Time tracking ────────────────────────────────────────
    time_limit: int = session.get("time_limit_minutes") or 60
    time_started = session.get("time_started") or session.get("started_at")
    elapsed_minutes = 0.0

    if time_started:
        if isinstance(time_started, str):
            started = datetime.fromisoformat(time_started.replace("Z", "+00:00"))
        else:
            started = time_started
        elapsed_minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60.0

    # ── Load plan and context ────────────────────────────────
    plan: dict = state.get("plan") or {}
    slots: list = plan.get("slots") or []
    answered_count: int = state.get("answered_count") or 0
    messages: list = state.get("messages") or []
    current_question: dict | None = state.get("current_question")
    pending_gq_id: str | None = state.get("pending_generated_question_id")
    set_scores: dict = state.get("set_scores") or {}   # tool_type → [scores]
    live_skill_totals: dict = state.get("live_skill_totals") or {}  # skill → [scores]

    if user_message:
        messages.append({"role": "user", "content": user_message})

    set_retro_text = ""        # populated after a set boundary is crossed
    set_retro_meta: dict | None = None  # structured data for StackFeedbackCard
    coding_inline_feedback = ""         # prepended to next question when coding just graded

    # ── Step 1: First turn — load CV, generate plan ──────────
    if not plan or not slots:
        cv_res = (
            await db.table("cv_uploads")
            .select("parsed_json, raw_text")
            .eq("session_id", session_id)
            .execute()
        )
        cv_data: dict = (
            (cv_res.data[0].get("parsed_json") or {}) if cv_res.data else {}
        )

        tpl_res = (
            await db.table("assessment_templates")
            .select("*, workflow_steps(*)")
            .eq("id", session["template_id"])
            .execute()
        )
        template: dict = tpl_res.data[0] if tpl_res.data else {}
        # P1: resolve modes (falls back to legacy assessment_type). Multi-mode runs
        # Career→Technical→Behavioural and scores on the competency axis, so we force
        # assessment_type to the generic 'track' report path when >1 mode is selected.
        _modes: list[str] = resolve_modes(template)
        _is_multi: bool = len(_modes) > 1
        assessment_type: str = "track" if _is_multi else (template.get("assessment_type") or "track")
        state["assessment_type"] = assessment_type
        state["modes"] = _modes
        human_steps: list = sorted(
            template.pop("workflow_steps", []) or [],
            key=lambda s: s.get("position", 0),
        )

        TOOL_TIME = {"mcq": 5, "voice": 8, "coding": 20, "task": 30, "visualization": 7}

        if human_steps:
            slots = [
                {
                    "slot_number": i + 1,
                    "tool_type": s["step_type"],
                    "skill_target": (s.get("config") or {}).get("skill_target", "thinking"),
                    "topic_hint": (s.get("config") or {}).get("topic_hint", ""),
                    "difficulty": (s.get("config") or {}).get("difficulty", "medium"),
                    "time_allocation_minutes": (s.get("config") or {}).get(
                        "time_limit_minutes", TOOL_TIME.get(s["step_type"], 10)
                    ),
                }
                for i, s in enumerate(human_steps)
            ]
            # Build tool_counts for the orientation message
            _manual_counts: dict[str, int] = {}
            for s in human_steps:
                _manual_counts[s["step_type"]] = _manual_counts.get(s["step_type"], 0) + 1
            plan = {
                "total_slots": len(slots),
                "time_limit_minutes": time_limit,
                "slots": slots,
                "tool_counts": _manual_counts,
                "source": "manual",
            }
        else:
            candidate_email: str = session.get("candidate_email") or ""
            past_cards = await memory_store.get_past_cards(candidate_email)

            if _is_multi:
                plan = await generate_multi_mode_plan(
                    cv_data,
                    template,
                    time_limit,
                    _modes,
                    track=session.get("track"),
                    previously_covered_topics=past_cards or None,
                )
            else:
                _single_mode = _modes[0] if _modes else LEGACY_TO_MODE.get(assessment_type, "technical")
                plan = await generate_plan(
                    cv_data,
                    _apply_type_config(template, _single_mode),   # P4 per-type config
                    time_limit,
                    track=session.get("track"),
                    previously_covered_topics=past_cards or None,
                    assessment_type=assessment_type,
                )
            slots = plan.get("slots") or []
            plan["source"] = plan.get("source") or "ai"

        # P1/P2: record adaptivity on the plan so every turn can read it (High adapts
        # difficulty per answer; Medium/Low follow the fixed plan).
        plan["adaptivity_level"] = resolve_adaptivity(template)

        # Guard: plan must have at least 1 slot
        if not slots:
            raise ValueError(
                "Assessment plan has 0 slots — check tool config and time limit."
            )

        asyncio.ensure_future(memory_store.store_cv_chunks(cv_data, session_id))

        bp_res = await db.table("blueprints").insert({
            "template_id": session["template_id"],
            "content": plan,
        }).execute()

        now_iso = datetime.now(timezone.utc).isoformat()
        await db.table("candidate_sessions").update({
            "blueprint_id": bp_res.data[0]["id"],
            "status": "in_progress",
            "time_started": now_iso,
            "started_at": now_iso,   # keep both in sync
        }).eq("id", session_id).execute()

        # Also reset elapsed so the time limit starts NOW, not from session creation
        elapsed_minutes = 0.0

        await get_or_create_profile(db, session_id)
        state["cv_data"] = cv_data

    cv_data = state.get("cv_data") or {}

    # ── Step 2: Grade incoming answer ────────────────────────
    # Grade even if force_complete — we never discard a submitted answer.
    if tool_result and current_question:
        q_tool_type = current_question.get("tool_type", "")
        grading_ok = False
        grading: dict = {}

        try:
            grading = await _grade_answer(q_tool_type, current_question, tool_result)
            grading_ok = True
        except Exception as e:
            print(f"[Orchestrator] Grading error (slot {answered_count + 1}, {q_tool_type}): {e}")
            # Give partial credit of 0 rather than blocking the session
            grading = {
                "overall_score": 0.0,
                "skill_scores": {current_question.get("skill_target", "thinking"): 0.0},
                "skill": current_question.get("skill_target", "thinking"),
                "rationale": f"Grading failed: {str(e)[:200]}",
                "feedback": "",
            }
            grading_ok = False

        score = float(grading.get("overall_score") or 0.0)
        set_scores.setdefault(q_tool_type, []).append(score)   # accumulate for set retro
        skill = grading.get("skill") or current_question.get("skill_target", "thinking")
        live_skill_totals.setdefault(skill, []).append(score)  # accumulate for live snapshot
        topic = current_question.get("topic") or "general"
        verdict = _compute_verdict(score)

        try:
            await create_memory_card(
                db, session_id,
                question_number=answered_count + 1,
                skill=skill,
                topic=topic,
                verdict=verdict,
                evidence=grading.get("rationale") or "",
                score=score,
            )
            await update_profile(db, session_id, skill, score, topic, verdict)

            asyncio.ensure_future(memory_store.store_card(
                candidate_email=session.get("candidate_email") or "",
                topic=topic,
                skill=skill,
                verdict=verdict,
                score=score,
                evidence=grading.get("rationale") or "",
                session_id=session_id,
            ))
        except Exception as e:
            print(f"[Orchestrator] Memory/profile update error: {e}")

        # Update coding_challenges row with submission + scores
        if q_tool_type == "coding" and grading_ok:
            challenge_id = (current_question.get("payload") or {}).get("challenge_id")
            if challenge_id:
                try:
                    await db.table("coding_challenges").update({
                        "submitted_code":    tool_result.get("code", ""),
                        "llm_scores":        grading,
                        "overall_score":     score,
                        "grading_rationale": grading.get("feedback", ""),
                        "submitted_at":      datetime.now(timezone.utc).isoformat(),
                    }).eq("id", challenge_id).execute()
                except Exception as e:
                    print(f"[Orchestrator] coding_challenges update error: {e}")
            # Surface coding feedback to candidate in the next agent message
            fb = (grading.get("feedback") or grading.get("rationale") or "").strip()
            if fb:
                score_label = (
                    "excellent" if score >= 4.5 else
                    "solid"     if score >= 3.5 else
                    "fair"      if score >= 2.5 else
                    "needs work"
                )
                coding_inline_feedback = (
                    f"**Code review — {score_label} ({score:.1f}/5):** "
                    f"{fb[:400]}{'…' if len(fb) > 400 else ''}\n\n"
                )

        # Save candidate answer — always, even on grading failure (score=0)
        try:
            await db.table("candidate_answers").insert({
                "session_id":            session_id,
                "question_number":       answered_count + 1,
                "generated_question_id": pending_gq_id,
                "skill":                 skill,
                "skill_scores":          grading.get("skill_scores") or {skill: score},
                "score":                 score,
                "grading_rationale":     grading.get("rationale") or "",
                "answer_text":           json.dumps(tool_result)[:2000],
            }).execute()
        except Exception as e:
            print(f"[Orchestrator] candidate_answers insert error: {e}")

        answered_count += 1
        state["answered_count"] = answered_count
        state["current_question"] = None
        state["pending_generated_question_id"] = None
        pending_gq_id = None
        current_question = None

        # ── Detect set transition and generate human retro ──────
        if answered_count > 0 and answered_count < len(slots):
            completed_type = slots[answered_count - 1]["tool_type"]
            next_type_     = slots[answered_count]["tool_type"]
            if completed_type != next_type_:
                try:
                    set_retro_text = await _build_set_retro(
                        completed_type,
                        set_scores.get(completed_type, []),
                        next_type_,
                    )
                    if set_retro_text.strip():
                        set_retro_meta = {
                            "text":           set_retro_text.strip(),
                            "completed_type": completed_type,
                            "scores":         set_scores.get(completed_type, []),
                        }
                        set_retro_text = ""  # moved to set_retro field, not inline
                except Exception as e:
                    print(f"[Retro] failed: {e}")

        # Dynamic difficulty for next slot — only in High adaptivity.
        # Medium/Low follow the fixed planned difficulty (no per-answer adaptation).
        if answered_count < len(slots) and (plan.get("adaptivity_level") or "high") == "high":
            next_slot = slots[answered_count]
            next_skill = next_slot.get("skill_target", "thinking")
            try:
                fresh_profile = await get_or_create_profile(db, session_id)
                skill_entry = (fresh_profile.get("skill_scores") or {}).get(next_skill)
                last_score, _ = _read_skill_entry(skill_entry)
                inconsistent = (
                    skill_entry.get("inconsistent", False)
                    if isinstance(skill_entry, dict) else False
                )
                if not inconsistent:
                    next_slot["difficulty"] = _adjust_difficulty(
                        next_slot.get("difficulty", "medium"), last_score
                    )
                slots[answered_count] = next_slot
                plan["slots"] = slots
            except Exception as e:
                print(f"[Orchestrator] Difficulty adjustment error: {e}")

    # ── Step 3: Check completion ──────────────────────────────
    time_up = elapsed_minutes >= time_limit
    all_done = answered_count >= len(slots)
    is_complete = time_up or all_done

    if is_complete:
        reason = "time" if (time_up and not all_done) else ("submitted" if all_done else "time")
        agent_msg = await _finalize_session(
            session=session,
            answered_count=answered_count,
            total_slots=len(slots),
            elapsed_minutes=elapsed_minutes,
            time_limit=time_limit,
            reason=reason,
            db=db,
            background_tasks=background_tasks,
        )

    else:
        # ── Step 4: Generate next question ──────────────────
        slot = slots[answered_count]
        topic_hint: str = slot.get("topic_hint", "")

        (memory_cards, profile, tpl_res_data), (cv_chunks, near_dupes) = await asyncio.gather(
            asyncio.gather(
                get_memory_cards(db, session_id),
                get_or_create_profile(db, session_id),
                db.table("assessment_templates").select("*").eq("id", session["template_id"]).execute(),
            ),
            asyncio.gather(
                memory_store.get_relevant_cv_chunks(topic_hint, session_id),
                memory_store.find_near_duplicates(topic_hint, session_id),
            ),
        )
        template_config: dict = tpl_res_data.data[0] if tpl_res_data.data else {}
        # Multi-mode: generate every slot on the generic 'track' path (not discovery/hr)
        # so technical MCQs get real correct answers and aren't auto-zeroed.
        if len(resolve_modes(template_config)) > 1:
            template_config = {**template_config, "assessment_type": "track"}

        if slot.get("tool_type") == "coding":
            challenge = await generate_coding_challenge(slot, cv_data, template_config, cv_chunks or None)
            ch_res = await db.table("coding_challenges").insert({
                "session_id":        session_id,
                "question_number":   answered_count + 1,
                "topic":             challenge.get("topic", slot.get("topic_hint", "")),
                "skill_target":      challenge.get("skill_target", "work"),
                "difficulty":        challenge.get("difficulty", "medium"),
                "body":              challenge.get("body", ""),
                "starter_code":      challenge.get("starter_code", ""),
                "sample_solution":   challenge.get("sample_solution", ""),
                "expected_approach": challenge.get("expected_approach", ""),
                "constraints":       challenge.get("constraints", ""),
                "test_cases":        challenge.get("test_cases", []),
            }).execute()
            challenge["challenge_id"] = ch_res.data[0]["id"]
            question = {
                "body":         challenge["body"],
                "tool_type":    "coding",
                "payload":      challenge,
                "skill_target": challenge.get("skill_target", "work"),
                "topic":        challenge.get("topic", ""),
                "difficulty":   challenge.get("difficulty", "medium"),
            }
        else:
            question = await generate_question(
                slot, cv_data, memory_cards, profile, template_config,
                cv_chunks=cv_chunks or None,
                extra_known_topics=near_dupes or None,
                db=db,
                used_bank_ids=state.get("used_bank_ids") or [],
                intake_answers=session.get("intake_answers") or None,
            )
            if question.get("bank_question_id"):
                state.setdefault("used_bank_ids", []).append(question["bank_question_id"])

        gq_res = await db.table("generated_questions").insert({
            "session_id":    session_id,
            "question_number": answered_count + 1,
            "tool_type":     question["tool_type"],
            "body":          question["body"],
            "payload":       question.get("payload") or {},
            "skill_target":  question.get("skill_target") or "thinking",
            "topic":         question.get("topic") or "",
            "difficulty":    question.get("difficulty") or "medium",
        }).execute()
        generated_question_id = gq_res.data[0]["id"] if gq_res.data else None

        state["current_question"] = question
        pending_gq_id = generated_question_id

        intros = {
            "mcq":           "Please select the best answer:",
            "voice":         "Please answer this question verbally:",
            "coding":        "Write your solution to this coding challenge:",
            "task":          "This is a practical task. Read carefully, then submit your work as a ZIP file:",
            "visualization": "Analyze this chart and share your insights:",
        }

        # ── Orientation greeting on the very first question ──────────────────
        # Returned as a SEPARATE message so the frontend shows two bubbles.
        orientation_msg: dict | None = None
        if answered_count == 0 and not tool_result:
            first_name = (session.get("candidate_name") or "there").split()[0]
            time_remaining_total = int(time_limit - elapsed_minutes)
            _assessment_type = state.get("assessment_type", "track")

            if _assessment_type == "personality":
                orientation_msg = {
                    "role": "agent",
                    "content": (
                        f"Hi {first_name}! I'm Masar, your AI guide — welcome to your personality assessment.\n\n"
                        f"You'll see **{len(slots)} paired statements**. There are no right or wrong answers — "
                        f"just choose whichever option feels most natural to you. Be honest and go with your gut!\n\n"
                        f"This takes about **{time_remaining_total} minutes**. Let's discover your personality type!"
                    ),
                    "tool_type": None,
                    "tool_payload": None,
                }
            else:
                tool_counts: dict = plan.get("tool_counts") or {}
                SECTION_DISPLAY = {
                    "mcq": "multiple-choice question",
                    "voice": "verbal question",
                    "coding": "coding challenge",
                    "visualization": "data analysis task",
                    "task": "practical task",
                }
                section_parts = []
                for t in ["mcq", "voice", "coding", "visualization", "task"]:
                    cnt = tool_counts.get(t, 0)
                    if cnt:
                        label = SECTION_DISPLAY.get(t, t)
                        section_parts.append(f"**{cnt} {label}{'s' if cnt > 1 else ''}**")
                sections_text = (
                    ", then ".join(section_parts) if section_parts else f"**{len(slots)} question(s)**"
                )
                orientation_msg = {
                    "role": "agent",
                    "content": (
                        f"Hi {first_name}! I'm Masar, your AI interviewer — welcome to your assessment.\n\n"
                        f"I've read through your CV and built a session personalized just for you. "
                        f"Here's what's ahead: {sections_text}. "
                        f"You have **{time_remaining_total} minutes** total — the session auto-submits when time runs out.\n\n"
                        f"Take a breath, go at your own pace, and remember: quality matters more than speed. Let's go!"
                    ),
                    "tool_type": None,
                    "tool_payload": None,
                }

        time_remaining = max(0.0, time_limit - elapsed_minutes)
        agent_msg = {
            "role": "agent",
            "content": (
                coding_inline_feedback +
                f"Question {answered_count + 1} of {len(slots)} — "
                f"{intros.get(question['tool_type'], 'Please answer:')} "
                f"({int(time_remaining)} min remaining)\n\n{question['body']}"
            ),
            "tool_type":    question["tool_type"],
            "tool_payload": question.get("payload"),
        }
        if set_retro_meta:
            agent_msg["set_retro"] = set_retro_meta

        # Attach live skill snapshot (running averages from answered questions so far)
        if live_skill_totals:
            agent_msg["skill_snapshot"] = {
                s: round(sum(v) / len(v), 2)
                for s, v in live_skill_totals.items()
            }

    messages.append(agent_msg)

    # ── Persist state ────────────────────────────────────────
    await db.table("candidate_sessions").update({
        "agent_state": {
            "plan":                          plan,
            "messages":                      messages[-20:],
            "answered_count":                answered_count,
            "current_question":              state.get("current_question"),
            "cv_data":                       cv_data,
            "pending_generated_question_id": pending_gq_id,
            "set_scores":                    set_scores,
            "live_skill_totals":             live_skill_totals,
            "assessment_type":               state.get("assessment_type", "track"),
            "used_bank_ids":                 state.get("used_bank_ids") or [],
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", session_id).execute()

    result: dict = {
        "session_id":       session_id,
        "message":          agent_msg,
        "session_complete": is_complete,
    }

    # First turn: send orientation as a separate preceding message
    # so the frontend can render two distinct chat bubbles.
    if orientation_msg is not None:
        result["messages"] = [orientation_msg, agent_msg]

    return result
