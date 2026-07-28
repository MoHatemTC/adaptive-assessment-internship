const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export interface SelfStartResponse {
  session_id: string;
  template_title: string;
  track: string;
  time_limit_minutes: number;
  assessment_type: "track" | "discover";
}

export async function selfStartSession(body: {
  track_slug: string;
  candidate_name: string;
  candidate_email: string;
  cv_upload_id: string;
  consent_camera?: boolean;
  consent_voice?: boolean;
  consent_data?: boolean;
}): Promise<SelfStartResponse> {
  const res = await fetch(`${BASE}/onboarding/self-start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((err as { detail?: string }).detail ?? "Failed to start assessment");
  }
  return res.json();
}
