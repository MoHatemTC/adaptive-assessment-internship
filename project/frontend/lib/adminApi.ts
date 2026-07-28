import { Template, Step, Question, GeneratedQuestion, ProctoringSession, ProctoringDetail, CodingChallenge, CodingChallengeDetail, AdminSessionSummary, AdminSessionDetail, Invitation, InviteBulkBody, PipelineEntry, QuestionBankItem, QuestionBankCreate, QuestionSet, QuestionSetDetail, EmailTemplate, Pipeline, PipelineDetail, PipelineStatus } from "./types";
import { supabase } from "./supabaseClient";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const headers: Record<string, string> = {};
  if (method !== "GET" && method !== "DELETE") {
    headers["Content-Type"] = "application/json";
  }
  // Attach the Supabase session token so the backend can authorize /admin/* calls.
  try {
    const { data: { session } } = await supabase.auth.getSession();
    if (session?.access_token) headers["Authorization"] = `Bearer ${session.access_token}`;
  } catch { /* no session — request proceeds unauthenticated (public endpoints only) */ }
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Request failed");
  }
  return res.json();
}

// ── Templates ──────────────────────────────────────────────
export const listTemplates = () => req<Template[]>("/admin/templates");
export const getTemplate = (id: string) => req<Template>(`/admin/templates/${id}`);
export const createTemplate = (body: Partial<Template>) =>
  req<Template>("/admin/templates", { method: "POST", body: JSON.stringify(body) });
export const updateTemplate = (id: string, body: Partial<Template>) =>
  req<Template>(`/admin/templates/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteTemplate = (id: string) =>
  req<{ message: string }>(`/admin/templates/${id}`, { method: "DELETE" });
export const publishTemplate = (id: string) =>
  req<{ public_link_token: string; public_url: string }>(`/admin/templates/${id}/publish`, { method: "POST" });
export const unpublishTemplate = (id: string) =>
  req<{ message: string }>(`/admin/templates/${id}/unpublish`, { method: "POST" });

// ── Steps ──────────────────────────────────────────────────
export const addStep = (templateId: string, body: Partial<Step>) =>
  req<Step>(`/admin/templates/${templateId}/steps`, { method: "POST", body: JSON.stringify(body) });
export const deleteStep = (templateId: string, stepId: string) =>
  req<{ message: string }>(`/admin/templates/${templateId}/steps/${stepId}`, { method: "DELETE" });

// ── AI task config generator ───────────────────────────────
export const generateTaskConfig = (body: { title: string; description?: string; admin_prompt?: string }) =>
  req<{ brief: string; deliverables: string[]; rubric: { criterion: string; weight: number }[]; time_limit_minutes: number }>(
    "/admin/generate-task-config",
    { method: "POST", body: JSON.stringify(body) }
  );

// ── AI competency suggester ────────────────────────────────
export const suggestCompetencies = (body: { title: string; track?: string; admin_prompt?: string; tag?: "technical" | "behavioural" | null }) =>
  req<{ competencies: { name: string; tag: "technical" | "behavioural" }[] }>(
    "/admin/suggest-competencies",
    { method: "POST", body: JSON.stringify(body) }
  );

// ── AI assessment planner (prompt → full config) ───────────
export interface GeneratedAssessment {
  title: string;
  description?: string;
  admin_prompt?: string;
  track?: string;
  modes: string[];
  adaptivity_level: "low" | "medium" | "high";
  time_limit_minutes: number;
  competencies?: { name: string; tag: "technical" | "behavioural" }[];
  tools?: Record<string, boolean>;
}
export const generateAssessment = (prompt: string) =>
  req<GeneratedAssessment>("/admin/generate-assessment", { method: "POST", body: JSON.stringify({ prompt }) });

// ── Platform settings (global on/off) ──────────────────────
export const getPlatformSettings = () =>
  req<{ accepting_sessions: boolean }>("/admin/settings");
export const updatePlatformSettings = (patch: { accepting_sessions?: boolean }) =>
  req<{ accepting_sessions: boolean }>("/admin/settings", { method: "PATCH", body: JSON.stringify(patch) });

// ── Proctoring review ──────────────────────────────────────
export const listProctoringSessionss = (status?: "warned" | "flagged") => {
  const qs = status ? `?status=${status}` : "";
  return req<ProctoringSession[]>(`/admin/proctoring${qs}`);
};
export const getProctoringDetail = (sessionId: string) =>
  req<ProctoringDetail>(`/admin/proctoring/${sessionId}`);
// P3 P2: per-assessment analytics (omit templateId for latest integrity logs)
export const getAnalytics = (templateId?: string) =>
  req<Record<string, unknown>>(`/admin/analytics${templateId ? `?template_id=${templateId}` : ""}`);

// ── Coding Challenges ──────────────────────────────────────────────────────
export const listCodingChallenges = () =>
  req<CodingChallenge[]>("/admin/coding");
export const getCodingChallenge = (id: string) =>
  req<CodingChallengeDetail>(`/admin/coding/${id}`);

// ── Session Results ────────────────────────────────────────────────────────
export const listAdminSessions = (filters?: { template_id?: string; status?: string }) => {
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters ?? {}).filter(([, v]) => v)) as Record<string, string>
  ).toString();
  return req<AdminSessionSummary[]>(`/admin/sessions${params ? `?${params}` : ""}`);
};
export const getAdminSession = (sessionId: string) =>
  req<AdminSessionDetail>(`/admin/sessions/${sessionId}`);

// ── Generated Questions (AI-created on-the-fly, stored for tracking) ──
export const listGeneratedQuestions = (filters?: { session_id?: string; tool_type?: string; skill_target?: string }) => {
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters ?? {}).filter(([, v]) => v)) as Record<string, string>
  ).toString();
  return req<GeneratedQuestion[]>(`/admin/generated-questions${params ? `?${params}` : ""}`);
};

// ── Invitations ────────────────────────────────────────────────────────────────
export const listInvitations = (filters?: { template_id?: string; status?: string }) => {
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters ?? {}).filter(([, v]) => v)) as Record<string, string>
  ).toString();
  return req<Invitation[]>(`/admin/invitations${params ? `?${params}` : ""}`);
};
export const createInvitation = (body: {
  template_id: string; candidate_email: string; candidate_name?: string;
  notes?: string; expires_in_days?: number;
}) => req<{ id: string; invitation_token: string; invite_url: string }>(
  "/admin/invitations", { method: "POST", body: JSON.stringify(body) }
);
export const bulkInvite = (body: InviteBulkBody) =>
  req<{ created: number; emails_sent: number; emails_failed: number }>(
    "/admin/invitations/bulk", { method: "POST", body: JSON.stringify(body) }
  );
export const cancelInvitation = (id: string) =>
  req<{ message: string }>(`/admin/invitations/${id}`, { method: "DELETE" });
export const resendInvitation = (id: string) =>
  req<{ message: string }>(`/admin/invitations/${id}/resend`, { method: "POST" });
// NOTE: renamed from `getPipeline` to free that name for the P1 assessment-pipelines
// contract (`getPipeline(id): Promise<PipelineDetail>` below). This is the OLD
// invitations-pipeline (candidate journey) view. Callers must import this new name.
export const getInvitationsPipeline = (email?: string) => {
  const qs = email ? `?email=${encodeURIComponent(email)}` : "";
  return req<PipelineEntry[]>(`/admin/invitations/pipeline${qs}`);
};
export const openInvitation = (token: string) =>
  req<{
    invitation_id: string; candidate_email: string; candidate_name: string | null;
    template_id: string; template_title: string; description: string | null;
    time_limit_minutes: number; assessment_type: string;
  }>(`/invitations/open/${token}`);

// ── Question Bank ──────────────────────────────────────────────────────────────
export const listBankQuestions = (filters?: {
  template_id?: string; tool_type?: string; skill_target?: string; difficulty?: string; search?: string;
}) => {
  const params = new URLSearchParams(
    Object.fromEntries(Object.entries(filters ?? {}).filter(([, v]) => v)) as Record<string, string>
  ).toString();
  return req<QuestionBankItem[]>(`/admin/question-bank${params ? `?${params}` : ""}`);
};
export const createBankQuestion = (body: QuestionBankCreate) =>
  req<QuestionBankItem>("/admin/question-bank", { method: "POST", body: JSON.stringify(body) });
export const bulkCreateBankQuestions = (questions: QuestionBankCreate[]) =>
  req<{ created: number }>("/admin/question-bank/bulk", { method: "POST", body: JSON.stringify(questions) });
export const updateBankQuestion = (id: string, body: Partial<QuestionBankCreate & { is_active: boolean }>) =>
  req<QuestionBankItem>(`/admin/question-bank/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteBankQuestion = (id: string) =>
  req<{ message: string }>(`/admin/question-bank/${id}`, { method: "DELETE" });

// Question sets (P3 B2)
export const listQuestionSets = () => req<QuestionSet[]>("/admin/question-sets");
export const createQuestionSet = (body: { name: string; description?: string; track?: string }) =>
  req<QuestionSet>("/admin/question-sets", { method: "POST", body: JSON.stringify(body) });
export const getQuestionSet = (id: string) => req<QuestionSetDetail>(`/admin/question-sets/${id}`);
export const addQuestionsToSet = (id: string, question_ids: string[]) =>
  req<{ added: number }>(`/admin/question-sets/${id}/items`, { method: "POST", body: JSON.stringify({ question_ids }) });
export const removeQuestionFromSet = (id: string, questionId: string) =>
  req<{ message: string }>(`/admin/question-sets/${id}/items/${questionId}`, { method: "DELETE" });
export const deleteQuestionSet = (id: string) =>
  req<{ message: string }>(`/admin/question-sets/${id}`, { method: "DELETE" });

// ── Email Templates (L2) ─────────────────────────────────────────────────────
export const getEmailTemplate = (key: string) =>
  req<EmailTemplate>(`/admin/email-templates/${key}`);
export const saveEmailTemplate = (key: string, body: { subject: string; html_body: string }) =>
  req<EmailTemplate>(`/admin/email-templates/${key}`, { method: "PUT", body: JSON.stringify(body) });

// ── Assessment Pipelines (P1) ─────────────────────────────────────────────────
export const listPipelines = () => req<Pipeline[]>("/admin/pipelines");
export const createPipeline = (body: { name: string; description?: string }) =>
  req<Pipeline>("/admin/pipelines", { method: "POST", body: JSON.stringify(body) });
export const getPipeline = (id: string) =>
  req<PipelineDetail>(`/admin/pipelines/${id}`);
export const setPipelineStages = (id: string, template_ids: string[]) =>
  req<{ stages_set: number }>(`/admin/pipelines/${id}/stages`, { method: "PUT", body: JSON.stringify({ template_ids }) });
export const enrollInPipeline = (id: string, body: { emails: string[]; names?: Record<string, string> }) =>
  req<{ enrolled: number }>(`/admin/pipelines/${id}/enroll`, { method: "POST", body: JSON.stringify(body) });
export const deletePipeline = (id: string) =>
  req<{ message: string }>(`/admin/pipelines/${id}`, { method: "DELETE" });
export const getMyPipelines = (email: string) =>
  req<PipelineStatus[]>(`/pipelines/status?email=${encodeURIComponent(email)}`);
