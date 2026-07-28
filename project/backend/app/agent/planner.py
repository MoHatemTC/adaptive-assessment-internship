import asyncio
import json
from openai import AsyncOpenAI
from fastapi import HTTPException
from app.config.settings import settings
from app.services.template_config import competency_names

# Canonical mode -> legacy assessment_type the single-mode planner understands.
MODE_TO_LEGACY = {
    "career": "discover", "technical": "track",
    "behavioural": "hr", "psychometric": "personality",
}
# Reverse: legacy assessment_type -> canonical mode (for per-type config lookup).
LEGACY_TO_MODE = {v: k for k, v in MODE_TO_LEGACY.items()}

_client: AsyncOpenAI | None = None

TOOL_TIME = {
    "mcq": 3,
    "voice": 6,
    "video": 8,
    "coding": 15,
    "task": 25,
    "visualization": 5,
}

# Grouping order is always respected — all slots of a type are contiguous
TOOL_ORDER = ["mcq", "voice", "video", "coding", "visualization", "task"]


def check_feasibility(tool_config: dict, time_limit_minutes: int) -> dict:
    """
    Pure-math pre-flight check. Runs before any LLM call.
    Returns {"ok": True} or {"ok": False, "reason": str, "breakdown": [...]}
    Raises HTTPException(400) if config is impossible.
    """
    enabled = [t for t in TOOL_ORDER if (tool_config.get(t) or {}).get("enabled")]
    if not enabled:
        raise HTTPException(400, "No tools are enabled. Enable at least one tool.")

    breakdown = []
    minimum_required = 0

    for t in enabled:
        if t == "task":
            count = 1
        else:
            explicit = (tool_config.get(t) or {}).get("count")
            count = int(explicit) if explicit else 1
        mins = TOOL_TIME[t] * count
        minimum_required += mins
        breakdown.append({
            "tool":    t,
            "count":   count,
            "minutes": mins,
            "locked":  bool((tool_config.get(t) or {}).get("count")) or t == "task",
        })

    if minimum_required > time_limit_minutes:
        lines = "\n".join(
            f"  • {b['tool']}: {b['count']} × {TOOL_TIME[b['tool']]} min = {b['minutes']} min"
            f"{'  [admin-locked]' if b['locked'] else '  [minimum 1]'}"
            for b in breakdown
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Assessment config is not feasible.\n"
                f"Time budget: {time_limit_minutes} min\n"
                f"Minimum required: {minimum_required} min\n\n"
                f"Breakdown:\n{lines}\n\n"
                f"Fix: increase the time limit to at least {minimum_required} min, "
                f"reduce question counts, or disable some tools."
            ),
        )

    return {
        "ok": True,
        "minimum_required": minimum_required,
        "slack_minutes": time_limit_minutes - minimum_required,
        "breakdown": breakdown,
    }


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


async def _planner_propose_counts(
    tools_needing_count: list[str],
    time_remaining_minutes: int,
    admin_prompt: str,
) -> dict[str, int]:
    """Ask the Planner to propose counts for tools the admin left unspecified."""
    time_per_tool = {t: TOOL_TIME[t] for t in tools_needing_count}
    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": f"""You are an assessment planner.
The admin has not specified how many of each tool to use. Propose realistic counts.

Assessment domain: {admin_prompt or "general"}
Available time:    {time_remaining_minutes} minutes
Time per question: {json.dumps(time_per_tool)} minutes each

Tools that need a count: {tools_needing_count}

Rules:
- Total time across ALL proposed tools must not exceed {time_remaining_minutes} minutes
- At least 1 of each enabled tool type
- Be realistic — a 30-min slot shouldn't have 8 voice questions

Return ONLY valid JSON with a count for each tool:
{json.dumps({t: 0 for t in tools_needing_count})}"""}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    proposed = json.loads(res.choices[0].message.content)
    # Validate: ensure at least 1 per tool, clamp to what fits in time
    result: dict[str, int] = {}
    budget = time_remaining_minutes
    for t in tools_needing_count:
        n = max(1, int(proposed.get(t, 1)))
        max_fits = max(1, budget // TOOL_TIME[t])
        n = min(n, max_fits)
        result[t] = n
        budget -= n * TOOL_TIME[t]
    return result


async def generate_plan(
    cv_data: dict,
    template: dict,
    time_limit_minutes: int = 60,
    track: str | None = None,
    previously_covered_topics: list | None = None,
    assessment_type: str = "track",
) -> dict:
    tool_config: dict = template.get("tool_config") or {}
    admin_prompt: str = template.get("admin_prompt") or ""
    effective_track = track or template.get("track") or "general"
    # Admin-defined competency names replace the hardcoded 5-skill default.
    # competency_names() tolerates both legacy list[str] and P1 [{name, tag}].
    competencies: list[str] = competency_names(tool_config)
    skill_target_options = competencies if competencies else ["thinking", "soft", "work", "digital_ai", "growth"]

    HR_DEFAULT_COMPETENCIES = [
        "Communication", "Leadership", "Problem Solving",
        "Teamwork", "Adaptability", "Conflict Resolution",
        "Initiative", "Customer Focus",
    ]

    # ── HR assessment branch — voice-only, soft skills ───────────────────────
    if assessment_type == "hr":
        soft_skills = competencies if competencies else HR_DEFAULT_COMPETENCIES
        minutes_per_q = 6
        total_possible = time_limit_minutes // minutes_per_q
        total_slots = max(1, min(total_possible, len(soft_skills) * 2))
        hr_slots: list[dict] = []
        # Cycle through soft skills until we fill slots
        for i in range(total_slots):
            skill = soft_skills[i % len(soft_skills)]
            hr_slots.append({
                "slot_number":             len(hr_slots) + 1,
                "tool_type":               "voice",
                "skill_target":            skill,
                "topic_hint":              f"{skill} — behavioral question",
                "difficulty":              "medium",
                "time_allocation_minutes": minutes_per_q,
            })
        return {
            "total_slots":        len(hr_slots),
            "time_limit_minutes": time_limit_minutes,
            "slots":              hr_slots,
            "enabled_tools":      ["voice"],
            "admin_prompt":       admin_prompt or "HR soft skills assessment",
            "tool_counts":        {"voice": len(hr_slots)},
            "count_source":       {"voice": "planner"},
            "competencies":       soft_skills,
            "source":             "ai",
        }

    # ── Personality (MBTI) branch — no feasibility check needed ─────────────
    if assessment_type == "personality":
        MBTI_DIMS = ["EI", "NS", "TF", "JP"]
        mbti_slots: list[dict] = []
        for round_i in range(4):
            for dim in MBTI_DIMS:
                mbti_slots.append({
                    "slot_number":              len(mbti_slots) + 1,
                    "tool_type":                "mcq",
                    "skill_target":             dim,
                    "topic_hint":               f"MBTI {dim} dimension — question {round_i + 1}",
                    "difficulty":               "easy",
                    "time_allocation_minutes":  3,
                })
        return {
            "total_slots":        16,
            "time_limit_minutes": time_limit_minutes,
            "slots":              mbti_slots,
            "enabled_tools":      ["mcq"],
            "admin_prompt":       "MBTI personality assessment",
            "tool_counts":        {"mcq": 16},
            "count_source":       {"mcq": "admin"},
            "competencies":       [],
            "source":             "ai",
        }

    # ── Pre-flight: fail fast before any LLM call ───────────────────────────
    check_feasibility(tool_config, time_limit_minutes)

    # ── Step 1: Resolve tool counts ─────────────────────────────────────────
    # Rule:
    #   admin set {tool}_count (non-None, > 0) → locked, Planner cannot change it
    #   admin left it as None                  → Planner proposes based on time
    #   task                                   → always exactly 1

    admin_locked: dict[str, int] = {}   # admin explicitly set these
    planner_needed: list[str] = []      # admin left these to the Planner

    for t in TOOL_ORDER:
        slot = tool_config.get(t) or {}
        if not slot.get("enabled"):
            continue  # tool disabled
        if t == "task":
            admin_locked[t] = 1
            continue
        explicit = slot.get("count")
        if explicit is not None and int(explicit) > 0:
            admin_locked[t] = int(explicit)
        else:
            planner_needed.append(t)

    # Ask Planner to propose counts for unspecified tools
    planner_proposed: dict[str, int] = {}
    if planner_needed:
        used_time = sum(TOOL_TIME[t] * n for t, n in admin_locked.items())
        remaining = max(10, time_limit_minutes - used_time)
        planner_proposed = await _planner_propose_counts(planner_needed, remaining, admin_prompt)

    tool_counts = {**admin_locked, **planner_proposed}

    # ── Step 2: Build skeleton deterministically ────────────────────────────
    # tool_type, count, and ordering are Python — LLM cannot change them.
    skeleton: list[dict] = []
    for tool in TOOL_ORDER:
        for _ in range(tool_counts.get(tool, 0)):
            skeleton.append({
                "slot_number":          len(skeleton) + 1,
                "tool_type":            tool,
                "time_allocation_minutes": TOOL_TIME[tool],
            })

    if not skeleton:
        return {
            "total_slots": 0, "time_limit_minutes": time_limit_minutes,
            "slots": [], "enabled_tools": [], "admin_prompt": admin_prompt,
            "count_source": {},
        }

    # ── Step 3: Cross-session memory block ──────────────────────────────────
    past_topics_block = ""
    if previously_covered_topics:
        known   = [t["topic"] for t in previously_covered_topics if t.get("verdict") == "knows"]
        partial = [t["topic"] for t in previously_covered_topics if t.get("verdict") == "partial"]
        gap     = [t["topic"] for t in previously_covered_topics if t.get("verdict") == "gap"]
        past_topics_block = f"""
Cross-session memory (previous assessments with this candidate):
- Already knows well — do NOT reassess: {known[:15]}
- Partial understanding — revisit from a different angle: {partial[:10]}
- Known gaps — worth retesting for improvement: {gap[:10]}
"""

    # ── Step 4: Ask LLM to fill topic_hint / skill_target / difficulty only ─
    slots_preview = json.dumps(
        [{"slot_number": s["slot_number"], "tool_type": s["tool_type"]} for s in skeleton],
        indent=2,
    )

    if assessment_type == "discover":
        slot_fill_prompt = f"""You are planning a career discovery assessment. The slot structure is fixed — do NOT change it.
Your only job: fill in topic_hint, skill_target, and difficulty for each slot.

This is a DISCOVERY assessment — NOT a knowledge test. Questions will probe personality,
aptitude, and preferences to determine which career track best suits the candidate.

── CANDIDATE CV (use to understand background — NOT to pick technical topics) ──
Skills:     {json.dumps(cv_data.get('skills', [])[:10])}
Experience: {json.dumps(cv_data.get('experience', [])[:3])}
Summary:    {(cv_data.get('raw_summary') or '')[:400]}
{past_topics_block}
── FIXED SLOTS (fill topic_hint, skill_target, difficulty for each) ────────────
{slots_preview}

Rules for discovery mode:
- topic_hint: a personality/aptitude dimension to probe. Choose from dimensions like:
  "analytical vs intuitive problem-solving", "structured vs open-ended work preference",
  "individual vs collaborative orientation", "data-driven vs narrative decision making",
  "creative expression vs systematic execution", "big-picture vs detail focus",
  "people-impact vs system-impact preference", "risk tolerance and ambiguity comfort"
  Each slot should probe a DIFFERENT dimension.
- skill_target: one of {json.dumps(skill_target_options)}
- difficulty: always "easy" — these are preference questions, not difficulty-tiered knowledge
- Cover varied skill_target values across slots — spread them out
- Cover varied personality dimensions — avoid repeating the same dimension

Return ONLY valid JSON:
{{"slots": [{{"slot_number": 1, "topic_hint": "...", "skill_target": "...", "difficulty": "easy"}}]}}"""
    else:
        slot_fill_prompt = f"""You are an assessment content planner.

The slot structure (counts and order) is already fixed — do NOT change it.
Your only job: fill in topic_hint, skill_target, and difficulty for each slot.

── ASSESSMENT DOMAIN (all topic_hints must come from this domain) ──────────
Admin instruction: {admin_prompt}
Track / domain:    {effective_track}
{past_topics_block}
── CANDIDATE CV (use only to calibrate difficulty — NOT to choose topics) ──
Skills:     {json.dumps(cv_data.get('skills', [])[:10])}
Experience: {json.dumps(cv_data.get('experience', [])[:3])}
Summary:    {(cv_data.get('raw_summary') or '')[:500]}

── FIXED SLOTS (fill the 3 fields for each) ────────────────────────────────
{slots_preview}

Rules:
- topic_hint: specific sub-topic within the admin domain (e.g. "RAG pipeline design", not "AI")
- skill_target: one of {json.dumps(skill_target_options)}
- Cover all skill_target values across the full slot list — spread them evenly
- difficulty: start easy for the first slot of each tool type, progress to harder

Return ONLY valid JSON:
{{"slots": [{{"slot_number": 1, "topic_hint": "...", "skill_target": "...", "difficulty": "easy|medium|hard"}}]}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": slot_fill_prompt}],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    filled = json.loads(res.choices[0].message.content)
    fill_map = {s["slot_number"]: s for s in (filled.get("slots") or [])}

    # ── Step 5: Merge fill-ins into skeleton ────────────────────────────────
    for slot in skeleton:
        fill = fill_map.get(slot["slot_number"], {})
        slot["topic_hint"]    = fill.get("topic_hint", f"{effective_track} fundamentals")
        slot["skill_target"]  = fill.get("skill_target", "thinking")
        slot["difficulty"]    = fill.get("difficulty", "medium")

    # Record where counts came from (useful for admin transparency)
    count_source = {
        **{t: "admin" for t in admin_locked},
        **{t: "planner" for t in planner_proposed},
    }

    return {
        "total_slots":   len(skeleton),
        "time_limit_minutes": time_limit_minutes,
        "slots":         skeleton,
        "enabled_tools": list(tool_counts.keys()),
        "admin_prompt":  admin_prompt,
        "tool_counts":   tool_counts,
        "count_source":  count_source,
        "competencies":  competencies,
        "source":        "ai",
    }


def _auto_counts_template(template: dict) -> dict:
    """Template variant used for multi-mode planning. An admin-set count (non-None,
    > 0) is a HARD LOCK and is preserved so the Planner cannot change it — this is
    the count the admin chose. Only counts the admin left blank are nulled so the
    Planner can size them to each mode's (smaller) per-mode time share."""
    tc = dict(template.get("tool_config") or {})
    new_tc: dict = {}
    for k, v in tc.items():
        if isinstance(v, dict) and "count" in v:
            c = v.get("count")
            if c is not None and int(c) > 0:
                new_tc[k] = v                       # admin-locked → keep exactly
            else:
                new_tc[k] = {**v, "count": None}    # unset → Planner sizes it
        else:
            new_tc[k] = v
    return {**template, "tool_config": new_tc}


def _apply_type_config(template: dict, mode: str) -> dict:
    """P4: overlay one assessment type's per-type config (adaptivity / question set /
    language / length / linked competencies) onto the template. Any field absent falls
    back to the template's global value. `mode` is the canonical type key."""
    tc = ((template.get("type_configs") or {}).get(mode)) or {}
    if not tc:
        return template
    tpl = {**template}
    tool_cfg = {**(template.get("tool_config") or {})}
    if tc.get("adaptivity"):
        tpl["adaptivity_level"] = tc["adaptivity"]
    if tc.get("language"):
        tool_cfg["question_language"] = tc["language"]
    if tc.get("length"):
        tool_cfg["question_length"] = tc["length"]
    if tc.get("question_set_id"):
        tool_cfg["question_set_id"] = tc["question_set_id"]
    if tc.get("competencies"):
        tool_cfg["competencies"] = [
            c if isinstance(c, dict) else {"name": c, "tag": "behavioural"}
            for c in tc["competencies"]
        ]
    tpl["tool_config"] = tool_cfg
    return tpl


async def generate_multi_mode_plan(
    cv_data: dict,
    template: dict,
    time_limit_minutes: int,
    modes: list[str],
    track: str | None = None,
    previously_covered_topics: list | None = None,
) -> dict:
    """Build one plan spanning several modes, run in the given (canonical) order.
    Each mode is planned with a proportional share of the time budget and its slots
    are tagged with `mode`, then concatenated and re-numbered. Scoring/report use the
    competency axis (assessment_type is forced to 'track' by the caller for multi-mode)."""
    share = max(10, time_limit_minutes // max(1, len(modes)))
    _GENERIC = {"track", "discover"}
    _first_generic = next((m for m in modes if MODE_TO_LEGACY.get(m, "track") in _GENERIC), None)

    def _null_counts(tpl: dict) -> dict:
        return {**tpl, "tool_config": {
            k: ({**v, "count": None} if isinstance(v, dict) and "count" in v else v)
            for k, v in (tpl.get("tool_config") or {}).items()
        }}

    # Per-mode planning is independent → run modes CONCURRENTLY to cut first-turn
    # latency. Each mode gets its own P4 per-type config (adaptivity/set/language/
    # length/competencies). Admin-locked counts must NOT multiply across modes, so
    # they're kept only on the FIRST generic (track/discover) mode; other generic
    # modes get counts nulled (hr/personality ignore counts). Try the proportional
    # share first, then the full budget so a mode still contributes rather than drop.
    async def _plan_one(mode: str) -> dict | None:
        legacy = MODE_TO_LEGACY.get(mode, "track")
        mtpl = _apply_type_config(template, mode)   # P4 per-type overrides
        tpl = _auto_counts_template(mtpl) if mode == _first_generic else _null_counts(mtpl)
        for budget in (share, time_limit_minutes):
            try:
                return await generate_plan(
                    cv_data, tpl, budget,
                    track=track, previously_covered_topics=previously_covered_topics,
                    assessment_type=legacy,
                )
            except HTTPException:
                continue
        return None

    subs = await asyncio.gather(*[_plan_one(m) for m in modes])

    # Assemble in canonical mode order (zip preserves it) so C → T → B ordering holds.
    combined: list[dict] = []
    enabled: list[str] = []
    for mode, sub in zip(modes, subs):
        if not sub:
            continue  # genuinely infeasible even at full budget — skip this mode
        for s in (sub.get("slots") or []):
            combined.append({**s, "mode": mode, "slot_number": len(combined) + 1})
        for t in (sub.get("enabled_tools") or []):
            if t not in enabled:
                enabled.append(t)
    return {
        "total_slots":        len(combined),
        "time_limit_minutes": time_limit_minutes,
        "slots":              combined,
        "enabled_tools":      enabled,
        "admin_prompt":       template.get("admin_prompt") or "",
        "modes":              modes,
        "competencies":       competency_names(template.get("tool_config") or {}),
        "source":             "ai-multimode",
    }
