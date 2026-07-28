import { ChatTurnResponse, Report } from "./types";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const headers: Record<string, string> = {};
  if (method !== "GET" && method !== "DELETE") {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Request failed");
  }
  return res.json();
}

export interface IntakeQuestion {
  type: "short_text" | "long_text" | "radio" | "checkbox";
  label: string;
  options?: string[];
}

export const getAssessmentConfig = (token: string) =>
  req<{ title: string; description: string | null; cv_required: boolean; questions: IntakeQuestion[] }>(
    `/session/config/${token}`,
  );

export const startSession = (body: {
  token: string;
  candidate_name: string;
  candidate_email: string;
  cv_upload_id?: string | null;
  track?: string;
  consent_camera: boolean;
  consent_voice: boolean;
  consent_data: boolean;
  intake_answers?: Record<string, unknown>;
}) => req<{ session_id: string; template_title: string; track: string; status: string; time_limit_minutes: number; assessment_type: string }>("/session/start", {
  method: "POST",
  body: JSON.stringify(body),
});

export const sendChatTurn = (body: {
  session_id: string;
  message: string;
  tool_result?: Record<string, unknown>;
}) => req<ChatTurnResponse>("/chat/turn", { method: "POST", body: JSON.stringify(body) });

export const getReport = (sessionId: string) =>
  req<Report>(`/report/${sessionId}`);

export interface DiscoveryResult {
  recommended_track: string;
  recommended_track_name: string;
  personality_archetype: string;
  top_tracks: { slug: string; name: string; fit_score: number; reasoning: string }[];
  personality_signals: string[];
  riasec_signals: Record<string, number>;
  summary: string;
}

export const submitAssessment = (sessionId: string) =>
  req<{ session_id: string; message: import("./types").AgentMessage; session_complete: boolean }>(
    "/chat/submit",
    { method: "POST", body: JSON.stringify({ session_id: sessionId }) },
  );

export const getSessionHistory = (email: string) =>
  req<
    Array<{
      id: string;
      status: string;
      started_at: string;
      assessment_templates: { title: string } | null;
      final_reports: { total_score: number; placement: string } | null;
    }>
  >(`/session/history?email=${encodeURIComponent(email)}`);

export const sendVoiceProbe = (body: {
  session_id: string;
  question: string;
  answer: string;
  turn_number: number;
}) => req<{ follow_up: string }>("/chat/voice-probe", { method: "POST", body: JSON.stringify(body) });

export const sendProctoringEvent = (body: {
  session_id: string;
  event_type: string;
  metadata?: Record<string, unknown>;
}) => fetch(`${BASE}/proctor/event`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).catch(() => null); // fire-and-forget, never throw
