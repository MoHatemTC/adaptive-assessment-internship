import json
import re
from json_repair import repair_json
from openai import AsyncOpenAI
from supabase import AsyncClient
from app.config.settings import settings
from app.services.visualization_generator import generate_visualization
from app.services.question_bank import find_candidate_question, personalize_question


def _repair_json(raw: str) -> dict:
    """Fix Gemini JSON quirks: strip fences, then use json-repair for everything else."""
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    return json.loads(repair_json(text))

_client: AsyncOpenAI | None = None

GENERATOR_SYSTEM = """You are an expert assessment question generator.

PRIORITY ORDER — non-negotiable:
1. ASSESSMENT DOMAIN (admin config) defines WHAT to ask about.
   You must stay strictly within the required topic. Never substitute a CV skill for the required topic.
   Example: required topic = "Agentic AI memory systems" → ask about agent memory.
   If the CV mentions HTML — that is irrelevant. Do NOT drift to HTML.

2. CANDIDATE CV is used ONLY to personalize HOW the question is worded.
   Use their background to make examples feel relevant and the phrasing feel personal.
   It does NOT change the topic.

Question rules:
- MCQ: exactly 4 options, one clearly correct, plausible distractors
- Voice: open-ended, requires the candidate to reason — not just recall
- Coding: practical challenge directly tied to the required topic with runnable test cases
- Visualization: handled by a separate service — do not generate
- Task: keep the admin brief intact; only personalize the framing and examples

Return ONLY valid JSON matching the tool_type schema."""

DISCOVERY_SYSTEM = """You are a career discovery assessment question generator.

Your job is to generate questions that reveal personality traits, aptitude, and work preferences
— NOT to test technical knowledge.

RULES:
1. Questions must be answerable by anyone — technical background or not.
2. NEVER ask domain-specific technical questions (no code, no algorithms, no tools).
3. Focus on: scenarios, preferences, values, decision-making style, what energizes or drains them.
4. MCQ options should represent genuinely different personality orientations (analytical/creative/systematic/social).
   There is no single objectively correct answer — each option signals a different career orientation.
5. Voice questions should invite honest reflection on real experiences or hypothetical scenarios.
6. Use the candidate's CV background ONLY to make the scenario feel personally relevant — not to ask technical questions.

Return ONLY valid JSON matching the tool_type schema."""

DISCOVERY_MCQ_SCHEMA = '{"body": "str", "options": [{"id": "a|b|c|d", "text": "str", "orientation": "brief label of what this option signals e.g. analytical, creative, systematic, social"}], "answer_key": {"correct_id": null, "explanation": "describe what each option signals about career orientation"}, "time_limit_seconds": 60}'
DISCOVERY_VOICE_SCHEMA = '{"body": "str", "evaluation_criteria": ["what to listen for in terms of aptitude and personality signals — NOT knowledge"], "sample_strong_answer": "example of a thoughtful, self-aware answer", "time_limit_seconds": 120}'

MCQ_SCHEMA    = '{"body": "str", "options": [{"id": "a|b|c|d", "text": "str"}], "answer_key": {"correct_id": "str", "explanation": "str"}, "time_limit_seconds": 60}'
VOICE_SCHEMA  = '{"body": "str", "evaluation_criteria": ["str"], "sample_strong_answer": "str", "time_limit_seconds": 120}'
CODING_SCHEMA = '{"body": "str", "starter_code": "str", "test_cases": [{"input": "str", "expected_output": "str"}], "time_limit_seconds": 600, "language": "python"}'

SCHEMAS = {
    "mcq":    MCQ_SCHEMA,
    "voice":  VOICE_SCHEMA,
    "coding": CODING_SCHEMA,
}

# ── HR / behavioral interview system ────────────────────────────────────────
HR_SYSTEM = """You are an experienced HR interviewer generating behavioral assessment questions.

Generate questions using the STAR framework (Situation, Task, Action, Result) where appropriate.

RULES:
1. Questions must probe real past experiences or plausible future scenarios.
2. Focus tightly on the specific competency — do NOT drift to technical topics.
3. Open-ended only — avoid yes/no or one-word answers.
4. Use the candidate's background to make scenarios feel professionally relevant.
5. Evaluation criteria assess: depth of example, clarity of action taken, outcome awareness, communication quality, and self-reflection.
6. Questions should feel like a real HR conversation — warm but probing.

Return ONLY valid JSON matching the schema."""

HR_VOICE_SCHEMA = '{"body": "str", "evaluation_criteria": ["str"], "competency_signals": ["specific behaviors that demonstrate this competency"], "sample_strong_answer": "example of a structured, reflective answer", "time_limit_seconds": 180}'

# ── MBTI personality system ──────────────────────────────────────────────────
MBTI_SYSTEM = """You are an MBTI-style personality assessment question generator.

Generate forced-choice questions that reveal genuine personality preferences.

RULES:
1. NEVER ask questions that have a factual correct answer.
2. Questions must feel natural and relatable — describe real situations or choices.
3. Option A should lean toward one personality pole; option B toward the opposite.
4. Both options must be equally valid — no option should feel obviously "better".
5. Avoid leading language that makes one option seem more socially desirable.
6. Use concrete, everyday scenarios — not abstract trait labels.
7. Do NOT use MBTI jargon (e.g., don't say "intuition" or "judging" in the question).

Return ONLY valid JSON matching the schema."""

MBTI_DIMS_INFO = {
    "EI": {"poles": ("E", "I"), "labels": ("Extraversion", "Introversion"),
           "hint": "energized by external interaction vs internal reflection"},
    "NS": {"poles": ("N", "S"), "labels": ("Intuition", "Sensing"),
           "hint": "drawn to patterns/possibilities vs concrete facts/details"},
    "TF": {"poles": ("T", "F"), "labels": ("Thinking", "Feeling"),
           "hint": "decisions by logical analysis vs personal values/impact"},
    "JP": {"poles": ("J", "P"), "labels": ("Judging", "Perceiving"),
           "hint": "prefers structure/closure vs flexibility/open options"},
}


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


LANG_STARTER: dict[str, str] = {
    "python":     "def solve(...):\n    \"\"\"docstring\"\"\"\n    pass",
    "javascript": "function solve(...) {\n  // your code here\n}",
    "typescript": "function solve(...): ReturnType {\n  // your code here\n}",
    "java":       "public class Solution {\n    public static ReturnType solve(...) {\n        // your code here\n    }\n}",
    "cpp":        "#include <bits/stdc++.h>\nusing namespace std;\n\nReturnType solve(...) {\n    // your code here\n}",
    "csharp":     "public class Solution {\n    public static ReturnType Solve(...) {\n        // your code here\n    }\n}",
    "go":         "package main\n\nfunc solve(...) ReturnType {\n    // your code here\n}",
    "rust":       "fn solve(...) -> ReturnType {\n    // your code here\n}",
    "sql":        "-- Write your SQL query here\nSELECT ...\nFROM ...\nWHERE ...;",
    "kotlin":     "fun solve(...): ReturnType {\n    // your code here\n}",
    "swift":      "func solve(...) -> ReturnType {\n    // your code here\n}",
    "ruby":       "def solve(...)\n  # your code here\nend",
    "php":        "<?php\nfunction solve(...) {\n    // your code here\n}",
    "r":          "solve <- function(...) {\n  # your code here\n}",
}


async def generate_coding_challenge(
    slot: dict,
    cv_data: dict,
    template_config: dict,
    cv_chunks: list[str] | None = None,
) -> dict:
    """2-shot: Shot 1 generates the challenge; Shot 2 generates test cases."""
    topic: str       = slot["topic_hint"]
    skill: str       = slot["skill_target"]
    difficulty: str  = slot["difficulty"]
    admin_context    = (template_config.get("admin_prompt") or "").strip()
    cv_context       = _build_cv_context(cv_chunks, cv_data)

    tool_config      = template_config.get("tool_config") or {}
    language: str    = ((tool_config.get("coding") or {}).get("language") or "python").lower()
    starter_hint     = LANG_STARTER.get(language, LANG_STARTER["python"])

    # ── Shot 1: Challenge body ───────────────────────────────────────────────
    res1 = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": f"""Generate a coding challenge for a technical assessment.

ASSESSMENT DOMAIN: {admin_context or "Software engineering"}
REQUIRED TOPIC: {topic}
DIFFICULTY: {difficulty}
SKILL TARGET: {skill}
LANGUAGE: {language}

CANDIDATE BACKGROUND (personalize phrasing/examples only — do not change the topic):
{cv_context}

The challenge and all code must be written in {language}.
Starter code template style: {starter_hint}

Return JSON:
{{
  "body": "full problem statement with context and requirements",
  "starter_code": "starter code in {language} language",
  "sample_solution": "correct implementation in {language}",
  "expected_approach": "what a good solution does (1-2 sentences)",
  "constraints": "time/space complexity expectations e.g. O(n) time"
}}"""}],
        response_format={"type": "json_object"},
        temperature=0.5,
    )
    challenge = json.loads(res1.choices[0].message.content)

    # ── Shot 2: Test cases ───────────────────────────────────────────────────
    res2 = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": f"""Generate test cases for this coding challenge.

LANGUAGE: {language}
CHALLENGE:
{challenge.get("body", "")}

SAMPLE SOLUTION:
```{language}
{challenge.get("sample_solution", "")}
```

CONSTRAINTS: {challenge.get("constraints", "")}

Generate 5 test cases covering: basic, edge (empty/zero), large input, boundary, error/special case.

For each test case write assert_code as actual runnable {language} that can be used to verify correctness.
For SQL: write a verification query. For Python: use assert statements. Adapt to the language.

Return JSON:
{{"test_cases": [
  {{
    "id": 1,
    "description": "basic case",
    "assert_code": "runnable {language} assertion/verification",
    "expected_behavior": "what the correct output is"
  }}
]}}"""}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    test_data = json.loads(res2.choices[0].message.content)

    return {
        "body":              challenge.get("body", ""),
        "starter_code":      challenge.get("starter_code", starter_hint),
        "sample_solution":   challenge.get("sample_solution", ""),
        "expected_approach": challenge.get("expected_approach", ""),
        "constraints":       challenge.get("constraints", ""),
        "test_cases":        test_data.get("test_cases", []),
        "language":          language,
        "tool_type":         "coding",
        "skill_target":      skill,
        "topic":             topic,
        "difficulty":        difficulty,
    }


def _build_cv_context(cv_chunks: list[str] | None, cv_data: dict) -> str:
    if cv_chunks:
        return "\n".join(f"- {c}" for c in cv_chunks)
    return (cv_data.get("raw_summary") or "")[:600]


def _inconsistency_note(learner_profile: dict) -> str:
    scores = learner_profile.get("skill_scores") or {}
    flagged = [s for s, data in scores.items() if isinstance(data, dict) and data.get("inconsistent")]
    if flagged:
        return f"⚠️ Inconsistent performance on: {flagged} — probe from a different angle; do NOT increase difficulty on these.\n"
    return ""


def _format_intake(intake_answers: dict | None) -> str:
    """Render the learner's pre-assessment intake answers as a context block so the
    generator can tailor questions to what they told us up front."""
    if not intake_answers:
        return ""
    lines = []
    for k, v in intake_answers.items():
        if v in (None, "", [], {}):
            continue
        val = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        lines.append(f"- {k}: {val}")
    if not lines:
        return ""
    return "PRE-ASSESSMENT INTAKE (use to tailor difficulty/framing):\n" + "\n".join(lines)


# Non-repetitive option-quality guide (length is handled separately via question_length).
_STYLE_GUIDE = (
    "For MCQ, keep each option short, mutually exclusive, and non-overlapping; "
    "avoid 'all/none of the above'. Do not reuse the wording or scenario of earlier questions."
)

# Admin-controlled question length → a concrete length instruction for the LLM.
_LENGTH_GUIDE = {
    "short":  "Keep the question to a single short sentence; MCQ options ≤ ~8 words.",
    "medium": "Keep the question concise (1–2 sentences); MCQ options ≤ ~12 words.",
    "long":   "A short scenario (2–4 sentences) is acceptable; MCQ options ≤ ~15 words.",
}


async def generate_question(
    slot: dict,
    cv_data: dict,
    memory_cards: list,
    learner_profile: dict,
    template_config: dict,
    cv_chunks: list[str] | None = None,
    extra_known_topics: list[str] | None = None,
    db: AsyncClient | None = None,
    used_bank_ids: list[str] | None = None,
    intake_answers: dict | None = None,
) -> dict:
    tool_type: str    = slot["tool_type"]
    skill_target: str = slot["skill_target"]
    topic: str        = slot["topic_hint"]
    difficulty: str   = slot["difficulty"]

    # Admin config — the primary signal
    admin_context: str = (template_config.get("admin_prompt") or "").strip()

    # P1: intake answers + admin-controlled language/style/adaptivity
    intake_ctx: str = _format_intake(intake_answers)
    _tcfg: dict = template_config.get("tool_config") or {}
    q_language: str = (_tcfg.get("question_language") or "English").strip() or "English"
    lang_line: str = f"Write the question in {q_language}." if q_language.lower() != "english" else ""
    length_line: str = _LENGTH_GUIDE.get(str(_tcfg.get("question_length") or "medium").lower(), _LENGTH_GUIDE["medium"])
    _adaptivity: str = str(template_config.get("adaptivity_level") or "high").lower()
    adapt_hint: str = {
        "high":   "Adapt tightly to this candidate's background and their earlier answers.",
        "medium": "Use a standard difficulty progression appropriate for the role.",
        "low":    "Keep the question standard and broadly reusable; minimal per-candidate tailoring.",
    }.get(_adaptivity, "")

    known: list = list(set(
        (learner_profile.get("known_topics") or []) + (extra_known_topics or [])
    ))
    gap: list          = learner_profile.get("gap_topics") or []
    cv_context: str    = _build_cv_context(cv_chunks, cv_data)
    inconsistency: str = _inconsistency_note(learner_profile)

    is_discovery   = template_config.get("assessment_type") == "discover"
    is_personality = template_config.get("assessment_type") == "personality"
    is_hr          = template_config.get("assessment_type") == "hr"

    # ── Question bank RAG: retrieve + personalize before generating ────────────
    # Skip for personality (MBTI), discovery, visualization, and task tools
    if (
        db is not None
        and template_config.get("id")
        and not is_personality
        and not is_discovery
        and not is_hr
        and tool_type not in ("visualization", "task")
    ):
        bank_q = await find_candidate_question(
            db=db,
            template_id=template_config["id"],
            tool_type=tool_type,
            skill_target=skill_target,
            difficulty=difficulty,
            topic_hint=topic,
            exclude_ids=used_bank_ids or [],
            # B2: if the admin attached a reusable question set, draw from it.
            question_set_id=(_tcfg.get("question_set_id") or None),
        )
        if bank_q:
            if _adaptivity == "low":
                # Low adaptivity: reuse the Question-Bank item as-is (consistent across
                # learners), no per-candidate LLM rewrite.
                return {
                    "body": bank_q.get("body", ""),
                    "tool_type": bank_q["tool_type"],
                    "payload": bank_q.get("payload") or {},
                    "skill_target": skill_target,
                    "topic": topic,
                    "difficulty": difficulty,
                    "bank_question_id": bank_q.get("id"),
                }
            # Determine candidate level from learner profile for adaptation
            skill_scores = learner_profile.get("skill_scores") or {}
            skill_avg = sum(
                v.get("score", 2.5) if isinstance(v, dict) else float(v)
                for v in skill_scores.values()
            ) / max(len(skill_scores), 1) if skill_scores else 2.5
            level = "beginner" if skill_avg < 2.0 else "advanced" if skill_avg >= 3.5 else "intermediate"
            return await personalize_question(bank_q, cv_context, candidate_level=level)

    # ── HR behavioral questions ───────────────────────────────
    if is_hr:
        competency = skill_target
        hr_prompt = f"""Generate a behavioral HR interview question for the competency: {competency}.

CANDIDATE BACKGROUND (use to make the scenario feel professionally relevant):
{cv_context}
{intake_ctx}

DIFFICULTY: {difficulty}
TOPICS ALREADY COVERED — do NOT repeat: {known[:6]}
STYLE: {length_line} {lang_line}

Requirements:
- The question must probe real past experience or a realistic scenario
- It should be a STAR-style question (Situation, Task, Action, Result) or a situational hypothetical
- Focus tightly on {competency} — do not drift to other competencies
- Competency signals: list 3-4 specific observable behaviors that a strong answer would demonstrate
- Make the scenario feel relevant to the candidate's professional background

Return ONLY valid JSON:
{HR_VOICE_SCHEMA}"""

        res = await _llm().chat.completions.create(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": HR_SYSTEM},
                {"role": "user",   "content": hr_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        data = _repair_json(res.choices[0].message.content)
        return {
            "body":         data.get("body", ""),
            "tool_type":    "voice",
            "payload":      data,
            "skill_target": skill_target,
            "topic":        f"HR {competency}",
            "difficulty":   difficulty,
        }

    # ── MBTI personality questions ────────────────────────────
    if is_personality:
        dim_info = MBTI_DIMS_INFO.get(skill_target, MBTI_DIMS_INFO["EI"])
        pole_a, pole_b = dim_info["poles"]
        label_a, label_b = dim_info["labels"]
        hint = dim_info["hint"]

        mbti_prompt = f"""Generate 1 forced-choice MBTI personality question for the {skill_target} dimension.

DIMENSION: {skill_target} ({label_a} vs {label_b})
What to probe: {hint}

CANDIDATE BACKGROUND (use to make the scenario relatable — never ask technical questions):
{cv_context}

Create a relatable everyday scenario or situation. Write option A to lean toward {label_a}, option B toward {label_b}.
STYLE: {lang_line}

Return ONLY valid JSON:
{{
  "body": "scenario or question text",
  "options": [
    {{"id": "a", "text": "option that leans toward {label_a}"}},
    {{"id": "b", "text": "option that leans toward {label_b}"}}
  ],
  "answer_key": {{
    "correct_id": null,
    "pole_a": "{pole_a}",
    "pole_b": "{pole_b}",
    "dimension": "{skill_target}"
  }},
  "time_limit_seconds": 60
}}"""

        res = await _llm().chat.completions.create(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": MBTI_SYSTEM},
                {"role": "user",   "content": mbti_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        data = _repair_json(res.choices[0].message.content)
        return {
            "body":         data.get("body", ""),
            "tool_type":    "mcq",
            "payload":      data,
            "skill_target": skill_target,
            "topic":        f"MBTI {skill_target}",
            "difficulty":   "easy",
        }

    # ── Visualization ─────────────────────────────────────────
    if tool_type == "visualization":
        viz_topic = topic if not is_discovery else "work and decision-making preferences"
        viz = await generate_visualization(skill_target, viz_topic, "easy" if is_discovery else difficulty, cv_context)
        body = (
            f"Take a look at this. There are no right or wrong answers — "
            f"just share what stands out to you and what questions it raises: {viz.get('question', '')}"
            if is_discovery else
            f"Take a look at the visual below and answer: {viz.get('question', '')}"
        )
        return {
            "body": body,
            "tool_type": "visualization",
            "payload": viz,
            "skill_target": skill_target,
            "topic": topic,
            "difficulty": difficulty,
        }

    # ── Task: admin brief is sacred, CV only personalizes framing ─
    if tool_type == "task":
        task_config: dict  = template_config.get("task_config") or {}
        admin_brief: str   = task_config.get("brief", "Complete the following task:")
        rubric: list       = task_config.get("rubric") or []
        time_limit: int    = task_config.get("time_limit_minutes", 30)

        personalize_prompt = f"""You are personalizing a task brief for a specific candidate.

ADMIN BRIEF (keep all requirements and rubric criteria exactly as specified):
{admin_brief}

CANDIDATE BACKGROUND (use only to personalize examples and phrasing):
{cv_context}

Instructions:
- Keep every task requirement and deliverable intact
- Only adapt the framing, examples, and context sentences to feel relevant to this candidate
- Do NOT simplify, remove, or add requirements
- Do NOT change what they have to produce

Return JSON: {{"body": "str", "personalized_brief": "str"}}"""

        res = await _llm().chat.completions.create(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": personalize_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        personalized = json.loads(res.choices[0].message.content)

        return {
            "body": personalized.get("body", admin_brief),
            "tool_type": "task",
            "payload": {
                "brief": personalized.get("personalized_brief", admin_brief),
                "rubric": rubric,
                "time_limit_minutes": time_limit,
                "deliverables": task_config.get("deliverables") or [],
            },
            "skill_target": skill_target,
            "topic": topic,
            "difficulty": difficulty,
        }

    # ── MCQ / Voice ──────────────────────────────────────────
    if is_discovery:
        schema = DISCOVERY_MCQ_SCHEMA if tool_type == "mcq" else DISCOVERY_VOICE_SCHEMA
        already_covered = known[:8] if known else []
        prompt = f"""Generate a career discovery {tool_type} question.

── PERSONALITY DIMENSION TO PROBE ──────────────────────────────────────────
Topic / dimension: {topic}

── CANDIDATE BACKGROUND (personalize scenario only — do NOT ask technical questions) ──
{cv_context}
{intake_ctx}

Personality dimensions already covered — probe something different: {already_covered}
STYLE: {_STYLE_GUIDE} {length_line} {lang_line}

Requirements:
- The question must reveal the candidate's natural orientation, preferences, or decision-making style.
- It must be answerable by someone with NO technical background.
- Frame it as a real scenario or clear preference choice — not an abstract question.
- MCQ: 4 options that each represent a genuinely different personality orientation.
  Label each option's orientation (e.g. "analytical", "creative", "systematic", "social").
  There is NO single correct answer — options reveal career fit signals.
- Voice: Ask them to reflect on a real experience or describe a hypothetical scenario.
  Evaluation criteria should focus on personality signals, not knowledge.

Return ONLY valid JSON:
{schema}"""
        system_prompt = DISCOVERY_SYSTEM
    else:
        # "video" is a camera-on spoken interview — generate it like a voice question.
        _gen_kind = "voice" if tool_type == "video" else tool_type
        schema = SCHEMAS.get(_gen_kind, SCHEMAS["voice"])
        prompt = f"""Generate a {_gen_kind} assessment question.

── WHAT TO ASSESS (required — do not deviate) ─────────────────────────────
Assessment domain: {admin_context or "As defined by the topic below"}
Required topic:    {topic}
Skill target:      {skill_target}
Difficulty:        {difficulty}
{inconsistency}
Topics already covered — DO NOT repeat (including semantic near-duplicates): {known}
Known gaps — approach from a different angle: {gap}

── HOW TO PERSONALIZE (use CV only for phrasing and examples) ─────────────
Candidate background:
{cv_context}
{intake_ctx}

The question MUST be about "{topic}" within the "{admin_context or skill_target}" domain.
Use the candidate's background to make examples and wording feel personal — not to change the topic.

STYLE: {_STYLE_GUIDE} {length_line} {adapt_hint} {lang_line}

Return ONLY valid JSON:
{schema}"""
        system_prompt = GENERATOR_SYSTEM

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    data = _repair_json(res.choices[0].message.content)

    return {
        "body": data.get("body", ""),
        "tool_type": tool_type,
        "payload": data,
        "skill_target": skill_target,
        "topic": topic,
        "difficulty": difficulty,
    }
