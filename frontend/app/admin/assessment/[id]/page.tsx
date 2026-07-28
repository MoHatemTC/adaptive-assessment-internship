"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getTemplate, updateTemplate, addStep, deleteStep, publishTemplate, unpublishTemplate, listInvitations, bulkInvite, cancelInvitation, resendInvitation, listQuestionSets } from "@/lib/adminApi";
import type { Template, Invitation, QuestionSet } from "@/lib/types";
import { publicUrl } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

const TOOL_LABELS: Record<string, { label: string; desc: string; color: string }> = {
  mcq:           { label: "MCQ",           desc: "Multiple choice questions",            color: "border-primary/30 bg-primary/5" },
  voice:         { label: "Voice",          desc: "Spoken interview",                     color: "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950" },
  video:         { label: "Video",          desc: "Camera-on interview",                  color: "border-sky-300 bg-sky-50 dark:border-sky-800 dark:bg-sky-950" },
  coding:        { label: "Coding",         desc: "Live coding + sandbox",                color: "border-orange-300 bg-orange-50 dark:border-orange-800 dark:bg-orange-950" },
  task:          { label: "Task",           desc: "Practical task (ZIP submission)",       color: "border-purple-300 bg-purple-50 dark:border-purple-800 dark:bg-purple-950" },
  visualization: { label: "Visualization",  desc: "AI chart analysis",                    color: "border-pink-300 bg-pink-50 dark:border-pink-800 dark:bg-pink-950" },
};

const STEP_TYPES = ["mcq", "voice", "coding", "task", "visualization"] as const;
type StepType = typeof STEP_TYPES[number];
type CountableTool = "mcq" | "voice" | "video" | "coding" | "visualization";

const TOOL_TIME: Record<string, number> = { mcq: 3, voice: 6, video: 8, coding: 15, visualization: 5, task: 25 };

const DEFAULT_STEP_CONFIGS: Record<StepType, object> = {
  mcq:           { skill_target: "thinking", difficulty: "medium", topic_hint: "" },
  voice:         { skill_target: "soft", difficulty: "medium", topic_hint: "", time_limit_minutes: 8 },
  coding:        { skill_target: "work", difficulty: "medium", topic_hint: "", time_limit_minutes: 20 },
  task:          { skill_target: "work", difficulty: "hard", topic_hint: "", time_limit_minutes: 30 },
  visualization: { skill_target: "digital_ai", difficulty: "medium", topic_hint: "" },
};

const MODE_OPTIONS = [
  { value: "career", label: "Career Guidance" },
  { value: "technical", label: "Technical" },
  { value: "behavioural", label: "Behavioural" },
  { value: "psychometric", label: "Psychometric" },
] as const;
const MODE_CANON_ORDER = ["career", "technical", "behavioural", "psychometric"];
const MODE_TO_LEGACY: Record<string, string> = { career: "discover", technical: "track", behavioural: "hr", psychometric: "personality" };
const LEGACY_TO_MODE: Record<string, string> = { discover: "career", track: "technical", hr: "behavioural", personality: "psychometric" };
const ADAPTIVITY_OPTS = [
  { value: "low", label: "Low", desc: "Question Bank + personalize" },
  { value: "medium", label: "Medium", desc: "Generate up front" },
  { value: "high", label: "High", desc: "Adapt per question" },
] as const;

type Competency = { name: string; tag: "technical" | "behavioural" };

function normComps(raw: unknown): Competency[] {
  if (!Array.isArray(raw)) return [];
  const out: Competency[] = [];
  for (const c of raw) {
    if (typeof c === "string" && c.trim()) out.push({ name: c.trim(), tag: "behavioural" });
    else if (c && typeof c === "object" && "name" in c) {
      const name = String((c as { name: unknown }).name);
      const tag = (c as { tag?: string }).tag === "technical" ? "technical" : "behavioural";
      if (name) out.push({ name, tag });
    }
  }
  return out;
}

export default function TemplateDetailPage() {
  const { t } = useI18n();
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [template, setTemplate] = useState<Template | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [copied, setCopied] = useState(false);

  // Edit mode state
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editPrompt, setEditPrompt] = useState("");
  const [editTimeLimit, setEditTimeLimit] = useState(60);
  const [editTools, setEditTools] = useState<Record<string, boolean>>({});
  const [editCounts, setEditCounts] = useState<Record<CountableTool, number | null>>({ mcq: null, voice: null, video: null, coding: null, visualization: null });
  const [editError, setEditError] = useState("");
  const [editCompetencies, setEditCompetencies] = useState<Competency[]>([]);
  const [editCompetencyInput, setEditCompetencyInput] = useState("");
  const [editCompetencyTag, setEditCompetencyTag] = useState<"technical" | "behavioural">("technical");
  const [editModes, setEditModes] = useState<string[]>([]);
  const [editAdaptivity, setEditAdaptivity] = useState<"low" | "medium" | "high">("high");
  const [editQuestionLanguage, setEditQuestionLanguage] = useState("English");
  const [editQuestionLength, setEditQuestionLength] = useState<"short" | "medium" | "long">("medium");
  const [questionSets, setQuestionSets] = useState<QuestionSet[]>([]);
  const [editQuestionSetId, setEditQuestionSetId] = useState("");
  // P4: per-assessment-type overrides
  type TypeCfg = { adaptivity?: string; question_set_id?: string; language?: string; length?: string; competencies?: string[] };
  const [editTypeConfigs, setEditTypeConfigs] = useState<Record<string, TypeCfg>>({});
  const patchTypeCfg = (typeVal: string, patch: Partial<TypeCfg>) =>
    setEditTypeConfigs((prev) => ({ ...prev, [typeVal]: { ...prev[typeVal], ...patch } }));
  // P4 D3: optional per-assessment invite email
  const [editEmailSubject, setEditEmailSubject] = useState("");
  const [editEmailBody, setEditEmailBody] = useState("");

  // Invite panel state
  const [showInvite, setShowInvite] = useState(false);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [inviteInput, setInviteInput] = useState("");
  const [inviteExpiry, setInviteExpiry] = useState<number | "">("");
  const [inviteNotes, setInviteNotes] = useState("");
  const [inviteSending, setInviteSending] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [inviteSuccess, setInviteSuccess] = useState("");

  // Step form state
  const [showAddStep, setShowAddStep] = useState(false);
  const [stepType, setStepType] = useState<StepType>("mcq");
  const [configText, setConfigText] = useState(JSON.stringify(DEFAULT_STEP_CONFIGS.mcq, null, 2));
  const [addingStep, setAddingStep] = useState(false);
  const [stepError, setStepError] = useState("");

  const TOOL_NAMES = ["mcq", "voice", "video", "coding", "visualization", "task"] as const;

  const load = async () => {
    setLoading(true);
    const t = await getTemplate(id);
    setTemplate(t);
    setLoading(false);
  };

  const loadInvitations = async () => {
    const list = await listInvitations({ template_id: id });
    setInvitations(list);
  };

  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { listQuestionSets().then(setQuestionSets).catch(() => {}); }, []);

  useEffect(() => {
    if (showInvite) loadInvitations();
  }, [showInvite]); // eslint-disable-line react-hooks/exhaustive-deps

  const openEdit = () => {
    if (!template) return;
    setEditTitle(template.title ?? "");
    setEditDescription(template.description ?? "");
    setEditPrompt(template.admin_prompt ?? "");
    setEditTimeLimit(template.time_limit_minutes ?? 60);
    const tools: Record<string, boolean> = {};
    const counts: Record<CountableTool, number | null> = { mcq: null, voice: null, video: null, coding: null, visualization: null };
    for (const t of TOOL_NAMES) {
      tools[t] = Boolean(template.tool_config?.[t]?.enabled);
      if (t !== "task") {
        const c = template.tool_config?.[t]?.count;
        counts[t as CountableTool] = c != null ? Number(c) : null;
      }
    }
    setEditTools(tools);
    setEditCounts(counts);
    setEditCompetencies(normComps(template.tool_config?.competencies));
    setEditCompetencyInput("");
    // Modes: prefer stored modes[]; else derive from legacy assessment_type.
    const modes = (template.modes && template.modes.length)
      ? [...template.modes]
      : (template.assessment_type ? [LEGACY_TO_MODE[template.assessment_type] ?? template.assessment_type] : ["technical"]);
    setEditModes(MODE_CANON_ORDER.filter((m) => modes.includes(m)).concat(modes.filter((m) => !MODE_CANON_ORDER.includes(m))));
    setEditAdaptivity((template.adaptivity_level as "low" | "medium" | "high") ?? "high");
    setEditQuestionLanguage(template.tool_config?.question_language ?? "English");
    setEditQuestionLength(template.tool_config?.question_length ?? "medium");
    setEditQuestionSetId(template.tool_config?.question_set_id ?? "");
    setEditTypeConfigs(((template as { type_configs?: Record<string, TypeCfg> }).type_configs) ?? {});
    setEditEmailSubject((template.tool_config?.email_subject as string) ?? "");
    setEditEmailBody((template.tool_config?.email_body as string) ?? "");
    setEditError("");
    setEditing(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setEditError("");
    try {
      const tool_config: Record<string, object> = {};
      for (const t of TOOL_NAMES) {
        if (t === "task") {
          tool_config[t] = { ...(template?.tool_config?.[t] ?? {}), enabled: editTools[t] ?? false };
        } else {
          tool_config[t] = {
            ...(template?.tool_config?.[t] ?? {}),
            enabled: editTools[t] ?? false,
            count: editCounts[t as CountableTool] ?? null,
          };
        }
      }
      const orderedModes = MODE_CANON_ORDER.filter((m) => editModes.includes(m));
      const primaryMode = orderedModes[0] ?? "technical";
      await updateTemplate(id, {
        title: editTitle,
        description: editDescription,
        admin_prompt: editPrompt,
        time_limit_minutes: editTimeLimit,
        assessment_type: orderedModes.length > 1 ? "track" : (MODE_TO_LEGACY[primaryMode] ?? "track"),
        modes: orderedModes,
        adaptivity_level: editAdaptivity,
        tool_config: {
          ...tool_config,
          competencies: editCompetencies.length > 0 ? editCompetencies : undefined,
          question_language: editQuestionLanguage,
          question_length: editQuestionLength,
          question_set_id: editQuestionSetId,
          email_subject: editEmailSubject || undefined,
          email_body: editEmailBody || undefined,
        },
        // P4: per-type overrides — only customized types are sent.
        type_configs: Object.fromEntries(
          orderedModes
            .map((m) => [m, editTypeConfigs[m]] as const)
            .filter(([, v]) => v && Object.keys(v).length > 0)
        ),
      });
      await load();
      setEditing(false);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : t("admin.edit.saveFailed", "Save failed"));
    } finally {
      setSaving(false);
    }
  };

  // Time budget preview for edit mode
  const timeBudget = (() => {
    let total = 0;
    for (const t of TOOL_NAMES) {
      if (!editTools[t]) continue;
      const cnt = t === "task" ? 1 : (editCounts[t as CountableTool] ?? 1);
      total += TOOL_TIME[t] * cnt;
    }
    return total;
  })();

  const handlePublish = async () => {
    if (!template) return;
    setPublishing(true);
    try {
      template.is_published ? await unpublishTemplate(id) : await publishTemplate(id);
      await load();
    } finally { setPublishing(false); }
  };

  const copyLink = () => {
    if (!template?.public_link_token) return;
    navigator.clipboard.writeText(publicUrl(`/assess/${template.public_link_token}`));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleStepTypeChange = (t: StepType) => {
    setStepType(t);
    setConfigText(JSON.stringify(DEFAULT_STEP_CONFIGS[t], null, 2));
  };

  const handleAddStep = async (e: React.FormEvent) => {
    e.preventDefault();
    setStepError("");
    let config: object;
    try { config = JSON.parse(configText); } catch { setStepError(t("admin.edit.invalidJson", "Config is not valid JSON")); return; }
    setAddingStep(true);
    try {
      await addStep(id, { position: template?.steps.length ?? 0, step_type: stepType, config });
      setShowAddStep(false);
      await load();
    } catch (e: unknown) {
      setStepError(e instanceof Error ? e.message : t("admin.edit.addStepFailed", "Failed to add step"));
    } finally { setAddingStep(false); }
  };

  if (loading) return <p className="text-muted-foreground text-sm">{t("admin.edit.loading", "Loading…")}</p>;
  if (!template) return null;

  const sorted = [...(template.steps || [])].sort((a, b) => a.position - b.position);
  const enabledTools = TOOL_NAMES.filter((t) => template.tool_config?.[t]?.enabled);

  return (
    <div className="max-w-2xl flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <button onClick={() => router.push("/admin")} className="text-xs text-muted-foreground hover:text-foreground mb-1 block">← {t("admin.edit.templatesBack", "Templates")}</button>
          <h1 className="text-2xl font-bold text-foreground">{template.title}</h1>
          {template.description && <p className="text-sm text-muted-foreground mt-1">{template.description}</p>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={openEdit}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-border text-foreground hover:bg-secondary transition-colors"
          >
            {t("admin.edit.editBtn", "Edit")}
          </button>
          <button
            onClick={() => router.push(`/admin/sessions?template_id=${id}`)}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-border text-foreground hover:bg-secondary transition-colors"
          >
            {t("admin.edit.candidates", "Candidates")}
          </button>
          <button
            onClick={() => setShowInvite(!showInvite)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              showInvite ? "bg-primary/10 text-primary border border-primary/30" : "border border-border text-foreground hover:bg-secondary"
            }`}
          >
            {t("admin.edit.invite", "Invite")}
          </button>
          <button
            onClick={handlePublish}
            disabled={publishing}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
              template.is_published ? "border border-border text-foreground hover:bg-secondary" : "bg-primary text-white hover:bg-primary/90"
            }`}
          >
            {publishing ? "…" : template.is_published ? t("admin.edit.unpublish", "Unpublish") : t("admin.edit.publish", "Publish")}
          </button>
        </div>
      </div>

      {/* Edit modal overlay */}
      {editing && (
        <div className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center p-4" onClick={(e) => { if (e.target === e.currentTarget) setEditing(false); }}>
          <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-lg flex flex-col gap-4 p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold text-foreground">{t("admin.edit.editTemplate", "Edit Template")}</h2>
              <button onClick={() => setEditing(false)} className="text-muted-foreground hover:text-foreground text-sm">✕</button>
            </div>

            {editError && <p className="text-sm text-destructive">{editError}</p>}

            {/* Title */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.title", "Title")}</label>
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {/* Description */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.description", "Description")}</label>
              <input
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {/* Admin prompt */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.adminPrompt", "Admin Prompt")}</label>
              <textarea
                value={editPrompt}
                onChange={(e) => setEditPrompt(e.target.value)}
                rows={3}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
              />
            </div>

            {/* Time limit */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.timeLimitMinutes", "Time Limit (minutes)")}</label>
              <input
                type="number"
                min={10}
                max={180}
                value={editTimeLimit}
                onChange={(e) => setEditTimeLimit(Math.max(10, parseInt(e.target.value) || 60))}
                className="w-28 border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {/* Assessment modes (multi-select) */}
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.assessmentMode", "Assessment Mode (one or more)")}</label>
              <div className="grid grid-cols-2 gap-2">
                {MODE_OPTIONS.map((opt) => {
                  const on = editModes.includes(opt.value);
                  return (
                    <button key={opt.value} type="button"
                      onClick={() => setEditModes(on ? editModes.filter((m) => m !== opt.value) : [...editModes, opt.value])}
                      className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium transition-all text-left ${on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>
                      <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${on ? "bg-primary border-primary text-white" : "border-muted-foreground/40"}`}>{on ? "✓" : ""}</span>
                      {t("admin.edit.mode_" + opt.value, opt.label)}
                    </button>
                  );
                })}
              </div>
              {MODE_CANON_ORDER.filter((m) => editModes.includes(m)).length > 1 && (
                <p className="text-xs text-muted-foreground">{t("admin.edit.runsInOrder", "Runs in order")}: {MODE_CANON_ORDER.filter((m) => editModes.includes(m)).map((m) => t("admin.edit.mode_" + m, MODE_OPTIONS.find((o) => o.value === m)?.label ?? m)).join(" → ")}</p>
              )}
            </div>

            {/* Adaptivity level */}
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.adaptivityLevel", "Adaptivity Level")}</label>
              <div className="grid grid-cols-3 gap-2">
                {ADAPTIVITY_OPTS.map((opt) => (
                  <button key={opt.value} type="button" onClick={() => setEditAdaptivity(opt.value)}
                    className={`flex flex-col items-start gap-0.5 rounded-xl border px-3 py-2 text-xs transition-all text-left ${editAdaptivity === opt.value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>
                    <span className="font-semibold">{t("admin.edit.adapt_" + opt.value, opt.label)}</span>
                    <span className="font-normal opacity-70">{t("admin.edit.adaptDesc_" + opt.value, opt.desc)}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Question language + length */}
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.qlang.label", "Question language")}</label>
                <input
                  type="text"
                  value={editQuestionLanguage}
                  onChange={(e) => setEditQuestionLanguage(e.target.value)}
                  placeholder={t("admin.qlang.placeholder", "English / العربية")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.qlen.label", "Question length")}</label>
                <select
                  value={editQuestionLength}
                  onChange={(e) => setEditQuestionLength(e.target.value as "short" | "medium" | "long")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                >
                  <option value="short">{t("admin.qlen.short", "Short")}</option>
                  <option value="medium">{t("admin.qlen.medium", "Medium")}</option>
                  <option value="long">{t("admin.qlen.long", "Long")}</option>
                </select>
              </div>
            </div>

            {/* Question set (optional) */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.qset.label", "Question set (optional)")}</label>
              <select
                value={editQuestionSetId}
                onChange={(e) => setEditQuestionSetId(e.target.value)}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                <option value="">{t("admin.qset.none", "None")}</option>
                {questionSets.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}{s.item_count != null ? ` (${s.item_count} ${t("admin.qset.items", "items")})` : ""}</option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{t("admin.qset.help", "Only used when Adaptivity = Low (pulls from the chosen set).")}</p>
            </div>

            {/* P4: per-assessment-type overrides */}
            {MODE_CANON_ORDER.filter((m) => editModes.includes(m)).length > 0 && (
              <div className="flex flex-col gap-2">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("typecfg.heading", "Per-type settings (optional)")}</label>
                <p className="text-xs text-muted-foreground -mt-1">{t("typecfg.help", "Override adaptivity, question set, language, length, and linked competencies per assessment type. Defaults to the settings above.")}</p>
                {MODE_CANON_ORDER.filter((m) => editModes.includes(m)).map((m) => {
                  const cfg = editTypeConfigs[m] || {};
                  const linked = cfg.competencies ?? editCompetencies.map((c) => c.name);
                  return (
                    <div key={m} className="rounded-xl border border-border p-3 flex flex-col gap-3 bg-secondary/20">
                      <p className="text-sm font-semibold text-foreground">{t("admin.edit.mode_" + m, MODE_OPTIONS.find((o) => o.value === m)?.label ?? m)}</p>
                      <div className="grid grid-cols-3 gap-2">
                        {ADAPTIVITY_OPTS.map((opt) => {
                          const on = (cfg.adaptivity ?? editAdaptivity) === opt.value;
                          return (
                            <button key={opt.value} type="button" onClick={() => patchTypeCfg(m, { adaptivity: opt.value })}
                              className={`rounded-lg border px-2 py-1.5 text-xs ${on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>{t("admin.edit.adapt_" + opt.value, opt.label)}</button>
                          );
                        })}
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <input type="text" value={cfg.language ?? editQuestionLanguage} onChange={(e) => patchTypeCfg(m, { language: e.target.value })}
                          className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                        <select value={cfg.length ?? editQuestionLength} onChange={(e) => patchTypeCfg(m, { length: e.target.value })}
                          className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                          <option value="short">{t("admin.qlen.short", "Short")}</option>
                          <option value="medium">{t("admin.qlen.medium", "Medium")}</option>
                          <option value="long">{t("admin.qlen.long", "Long")}</option>
                        </select>
                      </div>
                      <select value={cfg.question_set_id ?? ""} onChange={(e) => patchTypeCfg(m, { question_set_id: e.target.value })}
                        className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                        <option value="">{t("admin.qset.none", "None")}</option>
                        {questionSets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select>
                      {editCompetencies.length > 0 && (
                        <div>
                          <p className="text-xs text-muted-foreground mb-1">{t("typecfg.competencies", "Linked competencies")}</p>
                          <div className="flex flex-wrap gap-1.5">
                            {editCompetencies.map((c) => {
                              const on = linked.includes(c.name);
                              return (
                                <button key={c.name} type="button"
                                  onClick={() => patchTypeCfg(m, { competencies: on ? linked.filter((n) => n !== c.name) : [...linked, c.name] })}
                                  className={`text-xs rounded-full px-2.5 py-1 border ${on ? "bg-primary/10 text-primary border-primary/30" : "border-border text-muted-foreground"}`}>{c.name}</button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* P4 D3: per-assessment invite email (optional) */}
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("emailcfg.heading", "Invite email (optional)")}</label>
              <p className="text-xs text-muted-foreground -mt-1">{t("emailcfg.help", "Override the invitation email for this assessment. Leave blank to use the default. Placeholders: {{candidate_name}}, {{assessment_title}}, {{invite_url}}, {{time_limit}}.")}</p>
              <input type="text" value={editEmailSubject} onChange={(e) => setEditEmailSubject(e.target.value)} placeholder={t("emailcfg.subjectPlaceholder", "Subject (optional)")}
                className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
              <textarea value={editEmailBody} onChange={(e) => setEditEmailBody(e.target.value)} rows={4} placeholder={t("emailcfg.bodyPlaceholder", "HTML body (optional)")}
                className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>

            {/* Competencies */}
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.competencies", "Competencies (radar axes)")}</label>
              <div className="flex flex-wrap gap-1.5">
                {editCompetencies.map((c) => (
                  <span key={c.name} className={`flex items-center gap-1.5 text-xs border rounded-full px-2.5 py-1 font-medium ${c.tag === "technical" ? "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20" : "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/20"}`}>
                    {c.name}
                    <button type="button" title={t("admin.edit.toggleTag", "Toggle technical/behavioural")} onClick={() => setEditCompetencies(editCompetencies.map((x) => x.name === c.name ? { ...x, tag: x.tag === "technical" ? "behavioural" : "technical" } : x))} className="text-[10px] uppercase opacity-70 hover:opacity-100">{c.tag === "technical" ? "T" : "B"}</button>
                    <button type="button" onClick={() => setEditCompetencies(editCompetencies.filter((x) => x.name !== c.name))} className="hover:text-destructive transition-colors">×</button>
                  </span>
                ))}
                {editCompetencies.length === 0 && <p className="text-xs text-muted-foreground italic">{t("admin.edit.usingAiDefaults", "Using AI defaults")}</p>}
              </div>
              <div className="flex gap-2">
                <select value={editCompetencyTag} onChange={(e) => setEditCompetencyTag(e.target.value as "technical" | "behavioural")} className="border border-border rounded-xl px-2 py-1.5 text-xs bg-background">
                  <option value="technical">{t("admin.edit.technical", "Technical")}</option>
                  <option value="behavioural">{t("admin.edit.behavioural", "Behavioural")}</option>
                </select>
                <input
                  type="text"
                  value={editCompetencyInput}
                  onChange={(e) => setEditCompetencyInput(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.key === "Enter" || e.key === ",") && editCompetencyInput.trim()) {
                      e.preventDefault();
                      const name = editCompetencyInput.trim().replace(/,$/, "");
                      if (name && !editCompetencies.some((x) => x.name.toLowerCase() === name.toLowerCase())) setEditCompetencies([...editCompetencies, { name, tag: editCompetencyTag }]);
                      setEditCompetencyInput("");
                    }
                  }}
                  className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                  placeholder={t("admin.edit.competencyPlaceholder", "e.g. Leadership — press Enter")}
                />
                <button
                  type="button"
                  onClick={() => {
                    const name = editCompetencyInput.trim();
                    if (name && !editCompetencies.some((x) => x.name.toLowerCase() === name.toLowerCase())) setEditCompetencies([...editCompetencies, { name, tag: editCompetencyTag }]);
                    setEditCompetencyInput("");
                  }}
                  className="px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition-colors"
                >
                  {t("admin.edit.add", "Add")}
                </button>
              </div>
            </div>

            {/* Tools + counts */}
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.toolsAndCounts", "Tools & Question Counts")}</label>
              <p className="text-xs text-muted-foreground">{t("admin.edit.countHint", "Leave count blank → AI Planner decides. Set a number → locks it.")}</p>
              <div className="flex flex-col gap-2">
                {TOOL_NAMES.map((tool) => (
                  <div key={tool} className="flex items-center gap-3 bg-secondary/30 rounded-xl px-3 py-2">
                    <input
                      type="checkbox"
                      id={`tool-${tool}`}
                      checked={editTools[tool] ?? false}
                      onChange={(e) => setEditTools({ ...editTools, [tool]: e.target.checked })}
                      className="accent-primary"
                    />
                    <label htmlFor={`tool-${tool}`} className="text-sm font-medium text-foreground flex-1 cursor-pointer capitalize">
                      {t("admin.tool_" + tool, TOOL_LABELS[tool]?.label ?? tool)}
                      <span className="text-xs text-muted-foreground ml-2">{TOOL_TIME[tool]} {t("admin.edit.minEach", "min each")}</span>
                    </label>
                    {tool !== "task" && editTools[tool] && (
                      <input
                        type="number"
                        min={1}
                        max={20}
                        placeholder={t("admin.edit.aiPlaceholder", "AI")}
                        value={editCounts[tool as CountableTool] ?? ""}
                        onChange={(e) => setEditCounts({ ...editCounts, [tool]: e.target.value ? parseInt(e.target.value) : null })}
                        className="w-16 border border-border rounded-lg px-2 py-1 text-xs text-center bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                      />
                    )}
                  </div>
                ))}
              </div>
              {/* Budget preview */}
              <div className={`flex items-center justify-between text-xs px-3 py-2 rounded-lg ${
                timeBudget > editTimeLimit
                  ? "bg-destructive/10 text-destructive"
                  : "bg-green-500/10 text-green-700 dark:text-green-400"
              }`}>
                <span>{t("admin.edit.estimatedTime", "Estimated time")}: <strong>{timeBudget} {t("admin.edit.min", "min")}</strong></span>
                <span>{t("admin.edit.limit", "Limit")}: {editTimeLimit} {t("admin.edit.min", "min")}</span>
              </div>
            </div>

            <div className="flex gap-2 pt-2">
              <button
                onClick={handleSave}
                disabled={saving || !editTitle.trim()}
                className="flex-1 py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {saving ? t("admin.edit.saving", "Saving…") : t("admin.edit.saveChanges", "Save Changes")}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="px-4 py-2.5 rounded-xl border border-border text-sm hover:bg-secondary transition-colors"
              >
                {t("admin.edit.cancel", "Cancel")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Assessment link */}
      {template.is_published && template.public_link_token && (
        <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-green-700 mb-0.5">{t("admin.edit.shareWithCandidates", "Share with candidates")}</p>
            <p className="text-sm font-mono text-green-800 truncate">
              {publicUrl(`/assess/${template.public_link_token}`)}
            </p>
          </div>
          <button onClick={copyLink} className="shrink-0 px-3 py-1.5 rounded-lg border border-green-500/30 text-green-700 text-xs font-medium hover:bg-green-500/10 transition-colors">
            {copied ? t("admin.edit.copied", "Copied ✓") : t("admin.edit.copy", "Copy")}
          </button>
        </div>
      )}

      {/* Invite panel */}
      {showInvite && (
        <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-4">
          <p className="text-sm font-semibold text-foreground">{t("admin.edit.inviteCandidates", "Invite Candidates")}</p>

          {/* Send invites */}
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.emailsLabel", "Emails (comma or newline separated)")}</label>
              <textarea
                value={inviteInput}
                onChange={(e) => setInviteInput(e.target.value)}
                rows={3}
                placeholder={t("admin.edit.emailsPlaceholder", "alice@company.com, bob@company.com")}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>
            <div className="flex gap-3">
              <div className="flex flex-col gap-1 flex-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.expiresInDays", "Expires in (days)")}</label>
                <input
                  type="number"
                  min={1}
                  placeholder={t("admin.edit.never", "Never")}
                  value={inviteExpiry}
                  onChange={(e) => setInviteExpiry(e.target.value ? parseInt(e.target.value) : "")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div className="flex flex-col gap-1 flex-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.edit.notesOptional", "Notes (optional)")}</label>
                <input
                  type="text"
                  value={inviteNotes}
                  onChange={(e) => setInviteNotes(e.target.value)}
                  placeholder={t("admin.edit.notesPlaceholder", "e.g. Batch 3")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
            </div>
            {inviteError && <p className="text-sm text-destructive">{inviteError}</p>}
            {inviteSuccess && <p className="text-sm text-green-600">{inviteSuccess}</p>}
            <button
              disabled={inviteSending || !inviteInput.trim()}
              onClick={async () => {
                setInviteError(""); setInviteSuccess("");
                const emails = inviteInput.split(/[\n,]+/).map((e) => e.trim()).filter(Boolean);
                if (!emails.length) { setInviteError(t("admin.edit.enterOneEmail", "Enter at least one email")); return; }
                setInviteSending(true);
                try {
                  const r = await bulkInvite({
                    template_id: id,
                    emails,
                    notes: inviteNotes || undefined,
                    expires_in_days: inviteExpiry || undefined,
                  });
                  setInviteSuccess(`${t("admin.edit.sent", "Sent")} ${r.emails_sent} ${r.emails_sent !== 1 ? t("admin.edit.invitationsPlural", "invitations") : t("admin.edit.invitationSingular", "invitation")}${r.emails_failed ? ` (${r.emails_failed} ${t("admin.edit.failedSuffix", "failed")})` : ""}`);
                  // Surface why sends failed (e.g. unverified Resend sender)
                  if (r.emails_failed && Array.isArray(r.errors) && r.errors.length) {
                    setInviteError(r.errors[0]);
                  }
                  setInviteInput("");
                  await loadInvitations();
                } catch (e) {
                  setInviteError(e instanceof Error ? e.message : t("admin.edit.sendInvitationsFailed", "Failed to send invitations"));
                } finally {
                  setInviteSending(false);
                }
              }}
              className="self-start px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {inviteSending ? t("admin.edit.sending", "Sending…") : t("admin.edit.sendInvitations", "Send Invitations")}
            </button>
          </div>

          {/* Invited list */}
          {invitations.length > 0 && (
            <div className="flex flex-col gap-2 mt-2">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{invitations.length} {t("admin.edit.invitedCount", "invited")}</p>
              <div className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[520px]">
                  <thead>
                    <tr className="border-b border-border bg-secondary/40">
                      <th className="text-left px-3 py-2 text-xs font-semibold text-muted-foreground">{t("admin.edit.emailHeader", "Email")}</th>
                      <th className="text-left px-3 py-2 text-xs font-semibold text-muted-foreground">{t("admin.edit.statusHeader", "Status")}</th>
                      <th className="text-left px-3 py-2 text-xs font-semibold text-muted-foreground">{t("admin.edit.invitedHeader", "Invited")}</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {invitations.map((inv) => (
                      <tr key={inv.id} className="hover:bg-secondary/20 transition-colors">
                        <td className="px-3 py-2">
                          <p className="font-medium text-foreground text-xs">{inv.candidate_name || inv.candidate_email}</p>
                          {inv.candidate_name && <p className="text-xs text-muted-foreground">{inv.candidate_email}</p>}
                        </td>
                        <td className="px-3 py-2">
                          <span className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${
                            inv.status === "completed" ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" :
                            inv.status === "started" ? "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300" :
                            inv.status === "opened" ? "bg-amber-100 text-amber-800" :
                            inv.status === "expired" ? "bg-red-100 text-red-800" :
                            "bg-secondary text-muted-foreground"
                          }`}>{inv.status}</span>
                        </td>
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {new Date(inv.invited_at).toLocaleDateString()}
                        </td>
                        <td className="px-3 py-2 flex gap-2 justify-end">
                          {inv.status === "pending" && (
                            <button
                              onClick={async () => {
                                setInviteError(""); setInviteSuccess("");
                                try { await resendInvitation(inv.id); setInviteSuccess(t("admin.edit.resent", "Resent!")); }
                                catch (e) { setInviteError(e instanceof Error ? e.message : t("admin.edit.sendInvitationsFailed", "Failed to send invitations")); }
                              }}
                              className="text-xs text-primary hover:text-primary/70"
                            >
                              {t("admin.edit.resend", "Resend")}
                            </button>
                          )}
                          <button
                            onClick={async () => { await cancelInvitation(inv.id); await loadInvitations(); }}
                            className="text-xs text-destructive hover:text-destructive/70"
                          >
                            {t("admin.edit.cancel", "Cancel")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Admin prompt */}
      <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-foreground">{t("admin.edit.adminPrompt", "Assessment Instructions")}</p>
        </div>
        <p className="text-sm text-foreground bg-secondary/40 rounded-lg p-3 leading-relaxed">
          {template.admin_prompt || <span className="italic text-muted-foreground">{t("admin.edit.noPrompt", "None — the assessment uses the CV + enabled tools only.")}</span>}
        </p>
      </div>

      {/* Enabled tools with counts */}
      <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-3">
        <p className="text-sm font-semibold text-foreground">{t("admin.edit.enabledTools", "Enabled Tools")}</p>
        <div className="flex flex-wrap gap-2">
          {enabledTools.length === 0
            ? <p className="text-sm text-muted-foreground italic">{t("admin.edit.noneEnabled", "None enabled.")}</p>
            : enabledTools.map((tool) => {
                const cnt = tool !== "task" ? template.tool_config?.[tool]?.count : null;
                return (
                  <span key={tool} className="text-xs px-3 py-1 rounded-full bg-primary/10 text-primary font-medium capitalize flex items-center gap-1">
                    {t("admin.tool_" + tool, TOOL_LABELS[tool]?.label ?? tool)}
                    {cnt != null && <span className="bg-primary/20 rounded-full px-1.5">×{cnt}</span>}
                  </span>
                );
              })
          }
        </div>
      </div>

      {/* Task config */}
      {template.tool_config?.task?.enabled && template.task_config && (
        <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-3">
          <p className="text-sm font-semibold text-foreground">{t("admin.edit.taskConfig", "Task Tool Configuration")}</p>
          <p className="text-sm text-foreground bg-secondary/40 rounded-lg p-3 leading-relaxed">
            {(template.task_config as { brief?: string }).brief || "—"}
          </p>
          <p className="text-xs text-muted-foreground">
            {t("admin.edit.taskTimeLimit", "Time limit")}: {(template.task_config as { time_limit_minutes?: number }).time_limit_minutes ?? 30} {t("admin.edit.min", "min")} ·{" "}
            {((template.task_config as { rubric?: unknown[] }).rubric ?? []).length} {t("admin.edit.rubricCriteria", "rubric criteria")}
          </p>
        </div>
      )}
    </div>
  );
}
