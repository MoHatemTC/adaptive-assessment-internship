export type Skill = "thinking" | "soft" | "work" | "digital_ai" | "growth";
export type AssessmentType = "track" | "discover" | "personality" | "hr";
export type Difficulty = "easy" | "medium" | "hard";
export type MBTIType = "INTJ" | "INTP" | "ENTJ" | "ENTP" | "INFJ" | "INFP" | "ENFJ" | "ENFP" |
  "ISTJ" | "ISFJ" | "ESTJ" | "ESFJ" | "ISTP" | "ISFP" | "ESTP" | "ESFP";
export type Placement = "BEGINNER" | "PRO" | "HR_ASSESSED" | "DISCOVER" | MBTIType;
export type StepType = "mcq" | "interview" | "coding" | "task" | "visualization";
export type SessionStatus = "profile" | "identity" | "in_progress" | "grading" | "completed" | "flagged";
export type ToolType = "mcq" | "voice" | "video" | "coding" | "task" | "visualization" | null;
export type UserRole = "admin" | "candidate";

export interface ToolSlot {
  enabled: boolean;
  count: number | null;
  language?: string;
}

export interface ToolConfig {
  mcq: ToolSlot;
  voice: ToolSlot;
  coding: ToolSlot;
  visualization: ToolSlot;
  task: ToolSlot;
  // Admin-defined competencies used as radar chart axes.
  // When present, these replace the 5 hardcoded default skills.
  // Legacy string[] and P1 [{name, tag}] are both accepted.
  competencies?: (string | { name: string; tag: "technical" | "behavioural" })[];
}

export interface IntakeQuestion {
  type: "short_text" | "long_text" | "radio" | "checkbox";
  label: string;
  options?: string[];
}

export interface IntakeConfig {
  cv_required?: boolean;
  questions?: IntakeQuestion[];
}

export interface TaskConfig {
  brief: string;
  deliverables: string[];
  rubric: { criterion: string; weight: number }[];
  time_limit_minutes: number;
}

export interface Step {
  id: string;
  template_id: string;
  position: number;
  step_type: StepType;
  config: Record<string, unknown>;
  gate_threshold: number | null;
}

export interface Template {
  id: string;
  title: string;
  description: string | null;
  track: string | null;
  admin_prompt: string | null;
  time_limit_minutes: number;
  tool_config: ToolConfig;
  task_config?: TaskConfig | null;
  assessment_type?: AssessmentType;
  modes?: string[];
  adaptivity_level?: "low" | "medium" | "high";
  intake_config?: IntakeConfig;
  is_published: boolean;
  public_link_token: string | null;
  steps: Step[];
  created_at: string;
  updated_at: string;
}

export interface Question {
  id: string;
  question_type: string;
  body: string;
  skill: Skill;
  difficulty: Difficulty;
  track: string | null;
  options: { id: string; text: string }[] | null;
  answer_key: Record<string, unknown> | null;
  image_url: string | null;
  is_active: boolean;
  created_at: string;
}

export interface SetRetroData {
  text: string;
  completed_type: string;
  scores: number[];
}

export interface AgentMessage {
  role: "agent" | "user";
  content: string;
  tool_type: ToolType;
  tool_payload: Record<string, unknown> | null;
  set_retro?: SetRetroData;
  skill_snapshot?: Record<string, number>;
}

export interface ChatTurnResponse {
  session_id: string;
  message: AgentMessage;
  session_complete: boolean;
}

export interface SkillScore {
  score: number;
  evidence: string[];
  feedback?: string;
}

export interface Report {
  id: string;
  session_id: string;
  // Dynamic: keys are either default Skill names or admin-defined competency names
  skill_scores: Record<string, SkillScore>;
  total_score: number;
  placement: Placement;
  feedback: string;
  went_well?: string[];
  needs_improvement?: string[];
  recommendations: string[];
  integrity_status: string;
  email_sent: boolean;
  created_at: string;
}

export interface UserProfile {
  id: string;
  role: UserRole;
  full_name: string | null;
  avatar_url: string | null;
  email: string;
}

export interface CVParseResult {
  cv_upload_id: string;
  parsed_summary: string;
  skills: { name: string; level: string; years: number }[];
}

export interface GeneratedQuestion {
  id: string;
  session_id: string;
  question_number: number;
  tool_type: string;
  skill_target: string;
  topic: string;
  difficulty: string;
  body: string;
  payload: Record<string, unknown> | null;
  created_at: string;
  candidate_sessions?: { candidate_name: string; candidate_email: string } | null;
  candidate_answer?: {
    score: number;
    grading_rationale: string;
    answer_text: string;
    answer_data: Record<string, unknown> | null;
  } | null;
}

export interface MemoryCard {
  id: string;
  session_id: string;
  question_number: number;
  skill: Skill;
  topic: string;
  verdict: "knows" | "partial" | "gap";
  evidence: string;
  score: number;
}

export interface VisualizationPayload {
  // P4: generalized visual-stimulus. The generator picks the kind that best fits the competency.
  visual_kind?: "chart" | "mermaid" | "svg" | "html";
  title?: string;
  // chart kind
  chart_type?: "bar" | "line" | "scatter" | "pie";
  chart_title?: string;
  chart_data?: {
    labels: string[];
    datasets: { label: string; data: number[] }[];
  };
  // other kinds — self-contained artifact source
  mermaid?: string;   // Mermaid diagram source (sequence/flowchart/erd/…)
  svg?: string;       // self-contained <svg>…</svg> mockup
  html?: string;      // self-contained HTML+inline-CSS artifact (post/ad/email/screen)
  question: string;
  follow_up: string;
  expected_insights: string[];
  time_limit_seconds: number;
}

export interface TaskPayload {
  brief: string;
  rubric: { criterion: string; weight: number }[];
  time_limit_minutes: number;
  deliverables: string[];
}

// ── Admin session results ──────────────────────────────────────────────────

export interface AdminSessionSummary {
  id: string;
  candidate_name: string;
  candidate_email: string;
  status: string;
  integrity_status: string;
  started_at: string | null;
  completed_at: string | null;
  time_limit_minutes: number;
  template_id: string;
  template_title: string;
  template_track: string;
  answered_count: number;
  report: {
    total_score: number | null;
    placement: Placement | null;
    feedback: string | null;
  };
}

export interface AdminSessionAnswer {
  question_number: number;
  generated_question_id: string | null;
  skill: string;
  score: number;
  grading_rationale: string;
  answer_text: string;
  skill_scores: Record<string, number> | null;
}

export interface AdminSessionQuestion {
  id: string;
  question_number: number;
  tool_type: string;
  skill_target: string;
  topic: string;
  difficulty: string;
  body: string;
  payload: Record<string, unknown> | null;
  candidate_answer: AdminSessionAnswer | null;
}

export interface AdminSessionDetail {
  session: AdminSessionSummary & { admin_prompt: string };
  report: {
    id: string;
    total_score: number;
    placement: Placement;
    feedback: string;
    went_well: string[];
    needs_improvement: string[];
    recommendations: string[];
    skill_scores: Record<string, { score: number; evidence: string[] }>;
    integrity_status: string;
    created_at: string;
  } | null;
  questions: AdminSessionQuestion[];
}

export interface CodingChallenge {
  id: string;
  session_id: string;
  question_number: number;
  topic: string;
  skill_target: string;
  difficulty: string;
  overall_score: number | null;
  submitted_at: string | null;
  created_at: string;
  candidate_sessions: { candidate_name: string; candidate_email: string } | null;
}

export interface CodingTestCase {
  id: number;
  description: string;
  assert_code: string;
  expected_behavior: string;
}

export interface CodingLLMScores {
  correctness: number;
  code_quality: number;
  edge_case_handling: number;
  overall_score: number;
  feedback: string;
  test_results: { id: number; likely_passes: boolean; reason: string }[];
}

export interface CodingChallengeDetail extends CodingChallenge {
  body: string;
  starter_code: string | null;
  sample_solution: string | null;
  expected_approach: string | null;
  constraints: string | null;
  test_cases: CodingTestCase[];
  submitted_code: string | null;
  llm_scores: CodingLLMScores | null;
  grading_rationale: string | null;
  candidate_sessions: { candidate_name: string; candidate_email: string; status: string } | null;
}

export interface ProctoringSession {
  id: string;
  candidate_name: string;
  candidate_email: string;
  integrity_status: "clean" | "warned" | "flagged";
  status: string;
  started_at: string | null;
  completed_at: string | null;
  template_id: string | null;
  event_counts: { low: number; medium: number; high: number };
  flagged_frames_count: number;
}

export interface ProctoringFrame {
  id: string;
  frame_base64: string;
  gemini_verdict: {
    reasoning: string;
    face_clearly_visible: boolean;
    looking_at_screen: boolean;
    secondary_device_visible: boolean;
    another_person_present: boolean;
    candidate_absent: boolean;
    unanalyzable: boolean;
    identity_matches_reference: boolean | null;
    violation: boolean;
    violation_type: string;
    confidence: "low" | "medium" | "high";
    observations: string;
    low_confidence_identity_concern?: boolean;
  } | null;
  is_flagged: boolean;
  created_at: string;
}

export interface ProctoringEvent {
  id: string;
  event_type: string;
  severity: "low" | "medium" | "high";
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface ProctoringDetail {
  session: Omit<ProctoringSession, "event_counts" | "flagged_frames_count">;
  frames: ProctoringFrame[];
  events: ProctoringEvent[];
}

// ── Invitations ───────────────────────────────────────────────────────────────

export type InvitationStatus = "pending" | "opened" | "started" | "completed" | "expired";

export interface Invitation {
  id: string;
  template_id: string;
  candidate_email: string;
  candidate_name: string | null;
  status: InvitationStatus;
  expires_at: string | null;
  invited_at: string;
  opened_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  notes: string | null;
  invited_by: string | null;
  session_id: string | null;
  assessment_templates?: { title: string } | null;
}

export interface InviteBulkBody {
  template_id: string;
  emails: string[];
  names?: Record<string, string>;
  notes?: string;
  expires_in_days?: number;
}

export interface PipelineEntry {
  id: string;
  candidate_email: string;
  candidate_name: string | null;
  status: InvitationStatus;
  invited_at: string;
  completed_at: string | null;
  template_id: string;
  session_id: string | null;
  assessment_templates: { id: string; title: string; assessment_type: string } | null;
  candidate_sessions: {
    id: string;
    status: string;
    final_reports: { total_score: number; placement: string; skill_scores: Record<string, { score: number }> }[] | null;
  } | null;
}

// ── Question Bank ─────────────────────────────────────────────────────────────

export interface QuestionBankItem {
  id: string;
  template_id: string;
  tool_type: string;
  skill_target: string;
  difficulty: string;
  tags: string[];
  body: string;
  payload: Record<string, unknown>;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
}

export interface QuestionBankCreate {
  template_id: string | null;   // null = global (generic bank)
  tool_type: string;
  skill_target: string;
  difficulty: "easy" | "medium" | "hard";
  tags: string[];
  body: string;
  payload: Record<string, unknown>;
  scope?: string | null;
  created_by?: string;
}

export interface QuestionSet {
  id: string;
  name: string;
  description?: string | null;
  track?: string | null;
  created_at?: string;
  item_count?: number;
}

export interface QuestionSetDetail extends QuestionSet {
  questions: QuestionBankItem[];
}

// ── Email Templates (L2) ────────────────────────────────────────────────────

export interface EmailTemplate {
  key: string;
  subject: string;
  html_body: string;
}

// ── Assessment Pipelines (P1) ───────────────────────────────────────────────

export interface PipelineStage {
  template_id: string;
  title: string;
  stage_order: number;
}

export interface Pipeline {
  id: string;
  name: string;
  description?: string;
  is_active?: boolean;
  created_at?: string;
  stages?: PipelineStage[];
  enrollment_count?: number;
}

export interface PipelineEnrollment {
  candidate_email: string;
  candidate_name?: string;
  current_stage: number;
  status: string;
}

export interface PipelineDetail extends Pipeline {
  stages: PipelineStage[];
  enrollments: PipelineEnrollment[];
}

export interface PipelineStatusStage {
  template_id: string;
  title: string;
  token?: string | null;
  done: boolean;
}

export interface PipelineStatus {
  pipeline_id: string;
  name: string;
  stages: PipelineStatusStage[];
}
