import json
import time
from openai import AsyncOpenAI
from app.config.settings import settings
from app.services.proctoring import handle_event

_client: AsyncOpenAI | None = None

# Module-level ref photo cache — avoids 225+ DB reads per session
# Invalidated explicitly when a new reference photo is uploaded
_ref_cache: dict[str, str] = {}           # user_id → base64

# Debounce tracker — same violation within DEBOUNCE_SECONDS won't fire again
# Key: "{session_id}:{violation_type}" → last_fired_timestamp
_last_violation: dict[str, float] = {}

DEBOUNCE_SECONDS = 32   # 4 × 8s frame intervals


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


def invalidate_ref_cache(user_id: str) -> None:
    """Call this after a new reference photo is uploaded so the next frame fetch is fresh."""
    _ref_cache.pop(user_id, None)


# ── Prompts ────────────────────────────────────────────────────────────────────

CAMERA_PROMPT_NO_REF = """You are a proctoring AI for an online skills assessment platform.

The candidate is completing an AI-powered skills assessment on their computer. A small PiP camera overlay is visible to them.

Analyze this webcam frame for integrity issues. Work through these checks in order:

1. CAMERA BLOCKED — Is the frame completely or almost completely black/dark with no visible content?
   This means the camera lens was physically covered, taped, or pointed at a dark surface.
   If yes: set violation=true, violation_type="camera_off", unanalyzable=false, and stop.
   This is distinct from a low-quality frame — a covered camera is a deliberate act.

2. FRAME QUALITY — Is the image too blurry, distorted, or otherwise unreadable (but NOT just dark)?
   If yes, set unanalyzable=true and stop.

3. CANDIDATE PRESENCE — Is a person visible? Is their face clearly visible?
4. SCREEN FOCUS — Is the candidate generally oriented toward their screen? Brief or incidental glances away are normal — only flag consistent or sustained inattention.
5. SECONDARY DEVICES — Is a phone, tablet, second laptop, earpiece, or smartwatch with visible on-screen content present?
6. OTHER PEOPLE — Is another person clearly visible in the frame or reflected in a nearby surface?

Confidence guide:
  "high"   — completely clear and unambiguous
  "medium" — reasonably confident, minor uncertainty
  "low"    — unclear or borderline

Be conservative. When in doubt, choose the lower confidence. Do not flag anything unless clearly visible.

Return ONLY valid JSON with no markdown:
{
  "reasoning": "one sentence explaining what you see and why you reached this conclusion",
  "face_clearly_visible": true,
  "looking_at_screen": true,
  "secondary_device_visible": false,
  "another_person_present": false,
  "candidate_absent": false,
  "unanalyzable": false,
  "identity_matches_reference": null,
  "violation": false,
  "violation_type": "secondary_device|another_person|candidate_absent|camera_off|looking_away|null",
  "confidence": "low|medium|high",
  "observations": "describe what is visible: setting, lighting, candidate position, anything notable"
}"""


CAMERA_PROMPT_WITH_REF = """You are a proctoring AI for an online skills assessment platform.

IMAGE 1 — REFERENCE PHOTO: captured automatically 3 seconds after the session started with the verified candidate present.
IMAGE 2 — CURRENT FRAME: taken during the live assessment. This is what you are analyzing.

Work through these checks in order:

1. CAMERA BLOCKED — Is Image 2 completely or almost completely black with no visible content?
   If yes: set violation=true, violation_type="camera_off", unanalyzable=false, and stop.
   A covered camera is a deliberate act — do not mark it as merely unanalyzable.

2. FRAME QUALITY — Is Image 2 too blurry or distorted (but NOT just dark)? If yes, set unanalyzable=true and stop.
2. CANDIDATE PRESENCE — Is a person visible in Image 2?
3. FACE VISIBILITY — Is the face in Image 2 clearly visible and well-lit enough for comparison? If not, set face_clearly_visible=false and identity_matches_reference=null (inconclusive — do not flag).
4. IDENTITY CHECK (only when face_clearly_visible=true) — Compare the person in Image 2 to Image 1:
     Match on: face shape, facial bone structure, skin tone, hair color and general style
     Be LENIENT on: camera angle differences, lighting changes, slight position shifts, glasses on/off, different clothing
     Be STRICT on: clearly different face structure, clearly different skin tone, clearly different hair color
     Only set identity_matches_reference=false when you are at least medium confidence it is a different person.
     Identity mismatch is a serious accusation — default to null (inconclusive) when uncertain.
5. SCREEN FOCUS — Is the candidate generally oriented toward their screen?
6. SECONDARY DEVICES — Is a phone, tablet, second laptop, earpiece, or smartwatch with visible on-screen content present?
7. OTHER PEOPLE — Is another person clearly visible in the frame?

Confidence guide:
  "high"   — completely clear and unambiguous
  "medium" — reasonably confident, minor uncertainty
  "low"    — unclear or borderline — default here when uncertain

Return ONLY valid JSON with no markdown:
{
  "reasoning": "one sentence explaining what you see across both images and why you reached this conclusion",
  "face_clearly_visible": true,
  "looking_at_screen": true,
  "secondary_device_visible": false,
  "another_person_present": false,
  "candidate_absent": false,
  "unanalyzable": false,
  "identity_matches_reference": true,
  "violation": false,
  "violation_type": "identity_mismatch|secondary_device|another_person|candidate_absent|camera_off|looking_away|null",
  "confidence": "low|medium|high",
  "observations": "describe both images: who you see in each, how they compare visually, any specific concerns"
}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _fetch_reference_photo(user_id: str, db) -> str | None:
    """Return reference photo base64, using module-level cache to avoid repeat DB reads."""
    if user_id in _ref_cache:
        return _ref_cache[user_id]
    try:
        res = await db.table("user_profiles") \
            .select("reference_photo_base64") \
            .eq("id", user_id) \
            .execute()
        if res.data and res.data[0].get("reference_photo_base64"):
            photo = res.data[0]["reference_photo_base64"]
            _ref_cache[user_id] = photo
            return photo
    except Exception:
        pass
    return None


async def _store_flagged_frame(
    session_id: str,
    user_id: str,
    base64_image: str,
    verdict: dict,
    db,
) -> None:
    try:
        await db.table("proctoring_frames").insert({
            "session_id": session_id,
            "user_id": user_id,
            "frame_base64": base64_image,
            "gemini_verdict": verdict,
            "is_flagged": True,
        }).execute()
    except Exception:
        pass


def _is_debounced(session_id: str, violation_type: str) -> bool:
    """Return True if this exact violation type was already fired within DEBOUNCE_SECONDS."""
    key = f"{session_id}:{violation_type}"
    last = _last_violation.get(key)
    return last is not None and (time.time() - last) < DEBOUNCE_SECONDS


def _record_violation(session_id: str, violation_type: str) -> None:
    _last_violation[f"{session_id}:{violation_type}"] = time.time()


# ── Main entry point ───────────────────────────────────────────────────────────

async def analyze_frame(
    base64_image: str,
    session_id: str,
    user_id: str,
    db,
) -> dict:
    ref_photo = await _fetch_reference_photo(user_id, db)
    has_ref = bool(ref_photo)

    if has_ref:
        content = [
            {"type": "text",      "text": CAMERA_PROMPT_WITH_REF},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_photo}"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]
    else:
        content = [
            {"type": "text",      "text": CAMERA_PROMPT_NO_REF},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]

    try:
        res = await _llm().chat.completions.create(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=500,
        )
        result = json.loads(res.choices[0].message.content)

        # ── Gate 1: unanalyzable frame → skip, UNLESS it's a camera_off violation ──
        # A completely black/covered camera is intentional — it must be flagged even
        # though there's nothing to analyze visually.
        if result.get("unanalyzable") and result.get("violation_type") != "camera_off":
            return {**result, "violation": False, "skipped": True, "skip_reason": "unanalyzable"}

        # ── Gate 2: face not clearly visible → nullify identity comparison ──
        if not result.get("face_clearly_visible", True) and has_ref:
            result["identity_matches_reference"] = None
            if result.get("violation_type") == "identity_mismatch":
                result["violation"] = False
                result["violation_type"] = None

        # ── Gate 3: identity mismatch — only escalate at medium/high conf ───
        if has_ref and result.get("identity_matches_reference") is False:
            confidence = result.get("confidence", "low")
            if confidence in ("medium", "high"):
                result["violation"] = True
                result["violation_type"] = "identity_mismatch"
            else:
                # Low-confidence suspicion — note it but do NOT flag or store
                result["violation"] = False
                result["violation_type"] = None
                result["low_confidence_identity_concern"] = True

        # ── Gate 3b: sustained look-away from the screen ─────────────────────
        # The model computes looking_at_screen (conservatively — brief glances stay
        # true). Escalate a clear, confident look-away to a violation so the learner
        # gets a warning. Never override a higher-priority violation already set.
        if (not result.get("violation")
                and result.get("looking_at_screen") is False
                and result.get("confidence") in ("medium", "high")
                and not result.get("candidate_absent")):
            result["violation"] = True
            result["violation_type"] = "looking_away"

        # ── Gate 4: evaluate final violation state ───────────────────────────
        is_violation  = result.get("violation", False)
        confidence    = result.get("confidence", "low")
        vtype         = (result.get("violation_type") or "").strip()

        if is_violation and confidence in ("medium", "high") and vtype and vtype != "null":
            # ── Gate 5: debounce — skip if same violation fired recently ────
            if not _is_debounced(session_id, vtype):
                _record_violation(session_id, vtype)
                await _store_flagged_frame(session_id, user_id, base64_image, result, db)
                await handle_event(
                    db=db,
                    session_id=session_id,
                    event_type=vtype,
                    metadata=result,
                )

        return result

    except Exception as e:
        return {"violation": False, "error": str(e)}
