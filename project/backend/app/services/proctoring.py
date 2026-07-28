from supabase import AsyncClient

SEVERITY_MAP = {
    "tab_switch":        "low",
    "window_blur":       "low",
    "copy_paste":        "medium",
    "screenshot":        "medium",
    "camera_off":        "medium",
    "secondary_device":  "high",
    "phone_detected":    "high",    # legacy alias kept for old records
    "another_person":    "high",
    "identity_mismatch": "high",
    "ai_tool_detected":  "high",
}

FLAG_THRESHOLDS = {
    "low":    5,
    "medium": 3,
    "high":   1,
}


async def handle_event(
    db: AsyncClient,
    session_id: str,
    event_type: str,
    metadata: dict,
) -> dict:
    severity = SEVERITY_MAP.get(event_type, "low")

    await db.table("proctoring_events").insert({
        "session_id": session_id,
        "event_type": event_type,
        "severity":   severity,
        "metadata":   metadata,
    }).execute()

    events_res = await db.table("proctoring_events") \
        .select("severity") \
        .eq("session_id", session_id) \
        .execute()

    counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    for e in events_res.data:
        sev = e["severity"]
        counts[sev] = counts.get(sev, 0) + 1

    new_status = "clean"
    if counts["high"] >= FLAG_THRESHOLDS["high"]:
        new_status = "flagged"
    elif counts["medium"] >= FLAG_THRESHOLDS["medium"]:
        new_status = "flagged"
    elif counts["low"] >= FLAG_THRESHOLDS["low"] or counts["medium"] >= 1:
        new_status = "warned"

    await db.table("candidate_sessions").update({
        "integrity_status": new_status,
    }).eq("id", session_id).execute()

    return {"integrity_status": new_status, "severity": severity}
