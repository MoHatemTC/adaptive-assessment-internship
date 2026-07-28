"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createTemplate, generateTaskConfig, suggestCompetencies, generateAssessment, listQuestionSets } from "@/lib/adminApi";
import type { QuestionSet } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

// Values are i18n keys resolved via t() at render time.
const TOOL_DESCRIPTIONS: Record<string, string> = {
  mcq:           "adminNew.toolMcqDesc",
  voice:         "adminNew.toolVoiceDesc",
  video:         "adminNew.toolVideoDesc",
  coding:        "adminNew.toolCodingDesc",
  task:          "adminNew.toolTaskDesc",
  visualization: "adminNew.toolVisualizationDesc",
};

const SUPPORTED_LANGUAGES = [
  { value: "python", label: "Python" }, { value: "javascript", label: "JavaScript" },
  { value: "typescript", label: "TypeScript" }, { value: "java", label: "Java" },
  { value: "cpp", label: "C++" }, { value: "csharp", label: "C#" },
  { value: "go", label: "Go" }, { value: "rust", label: "Rust" },
  { value: "sql", label: "SQL" }, { value: "kotlin", label: "Kotlin" },
  { value: "swift", label: "Swift" }, { value: "ruby", label: "Ruby" },
  { value: "php", label: "PHP" }, { value: "r", label: "R" },
];

type CountableTool = "mcq" | "voice" | "video" | "coding" | "visualization";
type Competency = { name: string; tag: "technical" | "behavioural" };
type IntakeQuestion = { type: "short_text" | "long_text" | "radio" | "checkbox"; label: string; options?: string[] };

const DEFAULT_COMPETENCIES: Competency[] = [
  { name: "Critical Thinking", tag: "behavioural" },
  { name: "Communication", tag: "behavioural" },
  { name: "Problem Solving", tag: "behavioural" },
];

// Assessment modes. Multiple can be selected; they run Career → Technical → Behavioural.
// label/desc are i18n keys resolved via t() at render time.
const MODE_OPTIONS = [
  { value: "career",       label: "adminNew.modeCareerLabel", desc: "adminNew.modeCareerDesc" },
  { value: "technical",    label: "adminNew.technical",       desc: "adminNew.modeTechnicalDesc" },
  { value: "behavioural",  label: "adminNew.behavioural",     desc: "adminNew.modeBehaviouralDesc" },
  { value: "psychometric", label: "adminNew.modePsychometricLabel",    desc: "adminNew.modePsychometricDesc" },
] as const;
const MODE_CANON_ORDER = ["career", "technical", "behavioural", "psychometric"];
const MODE_TO_LEGACY: Record<string, string> = {
  career: "discover", technical: "track", behavioural: "hr", psychometric: "personality",
};

// label/desc are i18n keys resolved via t() at render time.
const ADAPTIVITY = [
  { value: "low",    label: "adminNew.adaptivityLowLabel",    desc: "adminNew.adaptivityLowDesc" },
  { value: "medium", label: "adminNew.adaptivityMediumLabel", desc: "adminNew.adaptivityMediumDesc" },
  { value: "high",   label: "adminNew.adaptivityHighLabel",   desc: "adminNew.adaptivityHighDesc" },
] as const;

// label is an i18n key resolved via t() at render time.
const INTAKE_TYPES = [
  { value: "short_text", label: "adminNew.intakeShortText" },
  { value: "long_text",  label: "adminNew.intakeLongText" },
  { value: "radio",      label: "adminNew.intakeSingleChoice" },
  { value: "checkbox",   label: "adminNew.intakeMultipleChoice" },
] as const;

const TOOL_TIME: Record<string, number> = { mcq: 3, voice: 6, video: 8, coding: 15, visualization: 5, task: 25 };

function calcBudget(tools: Record<string, boolean>, toolCounts: Record<string, number | null>, timeLimitMinutes: number) {
  const rows = Object.entries(tools).filter(([, on]) => on).map(([tool]) => {
    const count = tool === "task" ? 1 : (toolCounts[tool as CountableTool] ?? 1);
    const mins  = TOOL_TIME[tool] * count;
    const locked = tool === "task" || toolCounts[tool as CountableTool] !== null;
    return { tool, count, mins, locked };
  });
  const total = rows.reduce((s, r) => s + r.mins, 0);
  return { rows, total, slack: timeLimitMinutes - total, ok: total <= timeLimitMinutes };
}

export default function NewAssessmentPage() {
  const router = useRouter();
  const { t } = useI18n();
  const [form, setForm] = useState({ title: "", description: "", admin_prompt: "", time_limit_minutes: 60 });
  const [modes, setModes] = useState<string[]>(["technical"]);
  const [adaptivity, setAdaptivity] = useState<"low" | "medium" | "high">("high");
  const [questionLanguage, setQuestionLanguage] = useState("English");
  const [questionLength, setQuestionLength] = useState<"short" | "medium" | "long">("medium");
  const [questionSets, setQuestionSets] = useState<QuestionSet[]>([]);
  const [questionSetId, setQuestionSetId] = useState("");
  // P4: per-assessment-type overrides (adaptivity/set/language/length/competencies).
  // Only types the admin explicitly customizes get an entry; others fall back to the globals above.
  type TypeCfg = { adaptivity?: string; question_set_id?: string; language?: string; length?: string; competencies?: string[] };
  const [typeConfigs, setTypeConfigs] = useState<Record<string, TypeCfg>>({});
  const patchTypeCfg = (typeVal: string, patch: Partial<TypeCfg>) =>
    setTypeConfigs((prev) => ({ ...prev, [typeVal]: { ...prev[typeVal], ...patch } }));
  // P4 D3: optional per-assessment invite email (overrides the global template)
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [tools, setTools] = useState({ mcq: true, voice: true, video: false, coding: true, task: false, visualization: true });
  const [toolCounts, setToolCounts] = useState<Record<CountableTool, number | null>>({ mcq: 5, voice: 3, video: 2, coding: 1, visualization: 1 });
  const [codingLanguage, setCodingLanguage] = useState("python");
  const [taskConfig, setTaskConfig] = useState({ brief: "", deliverables: "", rubric: "", time_limit_minutes: 30 });

  const [competencies, setCompetencies] = useState<Competency[]>(DEFAULT_COMPETENCIES);
  const [competencyInput, setCompetencyInput] = useState("");
  const [competencyTag, setCompetencyTag] = useState<"technical" | "behavioural">("technical");
  const [competencyFilter, setCompetencyFilter] = useState<"all" | "technical" | "behavioural">("all");
  const [suggesting, setSuggesting] = useState(false);

  const [cvRequired, setCvRequired] = useState(true);
  const [intakeQuestions, setIntakeQuestions] = useState<IntakeQuestion[]>([]);

  const [saving, setSaving] = useState(false);
  const [generatingTask, setGeneratingTask] = useState(false);
  const [error, setError] = useState("");
  const [planPrompt, setPlanPrompt] = useState("");
  const [planning, setPlanning] = useState(false);

  useEffect(() => {
    listQuestionSets().then(setQuestionSets).catch(() => {});
  }, []);

  const handlePlanWithAI = async () => {
    if (!planPrompt.trim()) return;
    setPlanning(true);
    setError("");
    try {
      const cfg = await generateAssessment(planPrompt.trim());
      setForm({
        title: cfg.title ?? "",
        description: cfg.description ?? "",
        admin_prompt: cfg.admin_prompt ?? planPrompt.trim(),
        time_limit_minutes: cfg.time_limit_minutes ?? 60,
      });
      if (cfg.modes?.length) setModes(cfg.modes);
      if (cfg.adaptivity_level) setAdaptivity(cfg.adaptivity_level);
      if (cfg.competencies?.length) {
        setCompetencies(cfg.competencies.map((c) => ({ name: c.name, tag: c.tag === "technical" ? "technical" : "behavioural" })));
      }
      if (cfg.tools) {
        setTools({
          mcq: cfg.tools.mcq ?? true, voice: cfg.tools.voice ?? false, video: cfg.tools.video ?? false,
          coding: cfg.tools.coding ?? false, task: cfg.tools.task ?? false,
          visualization: cfg.tools.visualization ?? false,
        });
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("adminNew.errorPlanFailed"));
    } finally {
      setPlanning(false);
    }
  };

  // Quick preset: a single short MCQ (B5).
  const applyQuickPreset = () => {
    setTools({ mcq: true, voice: false, video: false, coding: false, task: false, visualization: false });
    setToolCounts({ mcq: 1, voice: null, video: null, coding: null, visualization: null });
    setForm((f) => ({ ...f, time_limit_minutes: 10 }));
  };

  const orderedModes = MODE_CANON_ORDER.filter((m) => modes.includes(m));
  const primaryMode  = orderedModes[0] ?? "technical";
  // Hide tool/budget config only when the single selected mode is behavioural or psychometric (auto-configured).
  const showTools = !(modes.length === 1 && (modes[0] === "behavioural" || modes[0] === "psychometric"));

  const toggleMode = (m: string) =>
    setModes((prev) => (prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m]));

  const addCompetency = () => {
    const name = competencyInput.trim().replace(/,$/, "");
    if (name && !competencies.some((c) => c.name.toLowerCase() === name.toLowerCase())) {
      setCompetencies([...competencies, { name, tag: competencyTag }]);
    }
    setCompetencyInput("");
  };

  const handleSuggestCompetencies = async () => {
    setSuggesting(true);
    setError("");
    try {
      const res = await suggestCompetencies({ title: form.title || "Assessment", admin_prompt: form.admin_prompt });
      const seen = new Set(competencies.map((c) => c.name.toLowerCase()));
      const merged = [...competencies];
      for (const c of res.competencies) {
        if (c.name && !seen.has(c.name.toLowerCase())) { merged.push(c); seen.add(c.name.toLowerCase()); }
      }
      setCompetencies(merged);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("adminNew.errorSuggestFailed"));
    } finally {
      setSuggesting(false);
    }
  };

  const handleGenerateTask = async () => {
    setGeneratingTask(true);
    setError("");
    try {
      const result = await generateTaskConfig({ title: form.title || "Assessment", description: form.description, admin_prompt: form.admin_prompt });
      setTaskConfig({
        brief: result.brief ?? "",
        deliverables: (result.deliverables ?? []).join("\n"),
        rubric: (result.rubric ?? [])
          .map((r: string | { criterion?: string }) => (typeof r === "string" ? r : r?.criterion ?? ""))
          .filter(Boolean).join("\n"),
        time_limit_minutes: result.time_limit_minutes ?? 30,
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("adminNew.errorAiGenerationFailed"));
    } finally {
      setGeneratingTask(false);
    }
  };

  const addIntakeQuestion = () =>
    setIntakeQuestions([...intakeQuestions, { type: "short_text", label: "" }]);
  const updateIntakeQuestion = (i: number, patch: Partial<IntakeQuestion>) =>
    setIntakeQuestions(intakeQuestions.map((q, idx) => (idx === i ? { ...q, ...patch } : q)));
  const removeIntakeQuestion = (i: number) =>
    setIntakeQuestions(intakeQuestions.filter((_, idx) => idx !== i));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (modes.length === 0) { setError(t("adminNew.errorPickMode")); return; }
    setSaving(true);
    setError("");
    try {
      const parsed_task_config = tools.task ? {
        brief: taskConfig.brief,
        deliverables: taskConfig.deliverables.split("\n").map((s) => s.trim()).filter(Boolean),
        rubric: taskConfig.rubric.split("\n").map((s) => s.trim()).filter(Boolean).map((c) => ({ criterion: c, weight: 1 })),
        time_limit_minutes: taskConfig.time_limit_minutes,
      } : null;

      const cleanedIntake = intakeQuestions
        .filter((q) => q.label.trim())
        .map((q) => ({ type: q.type, label: q.label.trim(), ...(q.options?.length ? { options: q.options } : {}) }));

      const t = await createTemplate({
        title: form.title,
        description: form.description,
        admin_prompt: form.admin_prompt,
        time_limit_minutes: primaryMode === "psychometric" ? 60 : form.time_limit_minutes,
        assessment_type: orderedModes.length > 1 ? "track" : (MODE_TO_LEGACY[primaryMode] ?? "track"),   // multi-mode → generic competency path
        modes: orderedModes,
        adaptivity_level: adaptivity,
        intake_config: { cv_required: cvRequired, questions: cleanedIntake },
        tool_config: {
          mcq:           { enabled: tools.mcq,           count: toolCounts.mcq },
          voice:         { enabled: tools.voice,         count: toolCounts.voice },
          video:         { enabled: tools.video,         count: toolCounts.video },
          coding:        { enabled: tools.coding,        count: toolCounts.coding, language: codingLanguage },
          visualization: { enabled: tools.visualization, count: toolCounts.visualization },
          task:          { enabled: tools.task,          count: 1 },
          competencies:  competencies.length > 0 ? competencies : undefined,
          question_language: questionLanguage,
          question_length:   questionLength,
          question_set_id:   questionSetId,
          email_subject:     emailSubject || undefined,
          email_body:        emailBody || undefined,
        },
        // P4: per-type overrides — only include types that were actually customized.
        type_configs: Object.fromEntries(
          orderedModes
            .map((m) => [m, typeConfigs[m]] as const)
            .filter(([, v]) => v && Object.keys(v).length > 0)
        ),
        ...(parsed_task_config ? { task_config: parsed_task_config } : {}),
      });
      router.push(`/admin/assessment/${t.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("adminNew.errorCreateFailed"));
      setSaving(false);
    }
  };

  const COUNTABLE: CountableTool[] = ["mcq", "voice", "video", "coding", "visualization"];
  const shownComps = competencies.filter((c) => competencyFilter === "all" || c.tag === competencyFilter);
  const budgetOk = !showTools || calcBudget(tools, toolCounts, form.time_limit_minutes).ok;

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold text-foreground mb-6">{t("adminNew.pageTitle")}</h1>
      {error && <div className="bg-destructive/10 text-destructive rounded-lg px-4 py-3 mb-4 text-sm">{error}</div>}

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {/* Plan with AI */}
        <div className="bg-primary/5 border border-primary/20 rounded-xl p-6 flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">{t("adminNew.planWithAi")}</h2>
              <p className="text-xs text-muted-foreground mt-0.5">{t("adminNew.planWithAiDesc")}</p>
            </div>
            <button type="button" onClick={applyQuickPreset} className="text-xs font-medium text-primary hover:text-primary/80 whitespace-nowrap shrink-0">{t("adminNew.quickOneQuestion")}</button>
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={planPrompt}
              onChange={(e) => setPlanPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handlePlanWithAI(); } }}
              placeholder={t("adminNew.planPromptPlaceholder")}
              className="flex-1 border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <button type="button" onClick={handlePlanWithAI} disabled={planning || !planPrompt.trim()}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 shrink-0">
              {planning ? (<><div className="w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent animate-spin" />{t("adminNew.planning")}</>) : t("adminNew.planWithAi")}
            </button>
          </div>
        </div>

        {/* Basic info */}
        <div className="bg-card border border-border rounded-xl p-6 flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-foreground">{t("adminNew.basicInformation")}</h2>

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.titleLabel")}</label>
            <input required className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder={t("adminNew.titlePlaceholder")} value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.descriptionLabel")} <span className="normal-case font-normal">{t("adminNew.descriptionHint")}</span></label>
            <textarea className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[70px] resize-none"
              placeholder={t("adminNew.descriptionPlaceholder")} value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.adminPromptLabel")}</label>
            <textarea className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[90px] resize-none"
              placeholder={t("adminNew.adminPromptPlaceholder")} value={form.admin_prompt}
              onChange={(e) => setForm({ ...form, admin_prompt: e.target.value })} />
          </div>

          {/* Assessment modes — multi-select */}
          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1.5">{t("adminNew.assessmentMode")} <span className="normal-case font-normal">{t("adminNew.selectOneOrMore")}</span></label>
            <div className="grid grid-cols-2 gap-2">
              {MODE_OPTIONS.map((opt) => {
                const on = modes.includes(opt.value);
                return (
                  <button key={opt.value} type="button" onClick={() => toggleMode(opt.value)}
                    className={`flex items-start gap-2 rounded-xl border px-3 py-2.5 text-xs font-medium transition-all text-left ${on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>
                    <span className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 ${on ? "bg-primary border-primary text-white" : "border-muted-foreground/40"}`}>{on ? "✓" : ""}</span>
                    <span className="flex flex-col">
                      <span className="font-semibold">{t(opt.label)}</span>
                      <span className="font-normal opacity-70">{t(opt.desc)}</span>
                    </span>
                  </button>
                );
              })}
            </div>
            {orderedModes.length > 1 && (
              <p className="text-xs text-muted-foreground mt-2 bg-primary/5 border border-primary/20 rounded-lg px-3 py-2">
                {t("adminNew.runsInOrder")} <span className="font-medium text-foreground">{orderedModes.map((m) => t(MODE_OPTIONS.find((o) => o.value === m)?.label ?? "")).join(" → ")}</span>
              </p>
            )}
            {modes.includes("psychometric") && (
              <p className="text-xs text-muted-foreground mt-2 bg-violet-50 dark:bg-violet-950 border border-violet-200 dark:border-violet-800 rounded-lg px-3 py-2">
                {t("adminNew.psychometricInfo")}
              </p>
            )}
            {modes.includes("behavioural") && (
              <p className="text-xs text-muted-foreground mt-2 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2">
                {t("adminNew.behaviouralInfo")}
              </p>
            )}
          </div>

          {/* Adaptivity level */}
          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1.5">{t("adminNew.adaptivityLevel")}</label>
            <div className="grid grid-cols-3 gap-2">
              {ADAPTIVITY.map((opt) => (
                <button key={opt.value} type="button" onClick={() => setAdaptivity(opt.value)}
                  className={`flex flex-col items-start gap-0.5 rounded-xl border px-3 py-2.5 text-xs transition-all text-left ${adaptivity === opt.value ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>
                  <span className="font-semibold">{t(opt.label)}</span>
                  <span className="font-normal opacity-70">{t(opt.desc)}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Question language + length */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("admin.qlang.label", "Question language")}</label>
              <input type="text" value={questionLanguage} onChange={(e) => setQuestionLanguage(e.target.value)}
                placeholder={t("admin.qlang.placeholder", "English / العربية")}
                className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("admin.qlen.label", "Question length")}</label>
              <select value={questionLength} onChange={(e) => setQuestionLength(e.target.value as "short" | "medium" | "long")}
                className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                <option value="short">{t("admin.qlen.short", "Short")}</option>
                <option value="medium">{t("admin.qlen.medium", "Medium")}</option>
                <option value="long">{t("admin.qlen.long", "Long")}</option>
              </select>
            </div>
          </div>

          {/* Question set (optional) */}
          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("admin.qset.label", "Question set (optional)")}</label>
            <select value={questionSetId} onChange={(e) => setQuestionSetId(e.target.value)}
              className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
              <option value="">{t("admin.qset.none", "None")}</option>
              {questionSets.map((s) => (
                <option key={s.id} value={s.id}>{s.name}{s.item_count != null ? ` (${s.item_count} ${t("admin.qset.items", "items")})` : ""}</option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground mt-1">{t("admin.qset.help", "Only used when Adaptivity = Low (pulls from the chosen set).")}</p>
          </div>

          {/* P4: per-assessment-type overrides */}
          {orderedModes.length > 0 && (
            <div className="flex flex-col gap-2">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block">{t("typecfg.heading", "Per-type settings (optional)")}</label>
              <p className="text-xs text-muted-foreground -mt-1">{t("typecfg.help", "Override adaptivity, question set, language, length, and linked competencies per assessment type. Defaults to the settings above.")}</p>
              {orderedModes.map((m) => {
                const cfg = typeConfigs[m] || {};
                const linked = cfg.competencies ?? competencies.map((c) => c.name);
                return (
                  <div key={m} className="rounded-xl border border-border p-3 flex flex-col gap-3 bg-secondary/20">
                    <p className="text-sm font-semibold text-foreground">{t(MODE_OPTIONS.find((o) => o.value === m)?.label ?? "", m)}</p>
                    <div className="grid grid-cols-3 gap-2">
                      {ADAPTIVITY.map((opt) => {
                        const on = (cfg.adaptivity ?? adaptivity) === opt.value;
                        return (
                          <button key={opt.value} type="button" onClick={() => patchTypeCfg(m, { adaptivity: opt.value })}
                            className={`rounded-lg border px-2 py-1.5 text-xs ${on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>{t(opt.label)}</button>
                        );
                      })}
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <input type="text" value={cfg.language ?? questionLanguage} onChange={(e) => patchTypeCfg(m, { language: e.target.value })}
                        placeholder={t("admin.qlang.placeholder", "English / العربية")}
                        className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                      <select value={cfg.length ?? questionLength} onChange={(e) => patchTypeCfg(m, { length: e.target.value })}
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
                    {competencies.length > 0 && (
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">{t("typecfg.competencies", "Linked competencies")}</p>
                        <div className="flex flex-wrap gap-1.5">
                          {competencies.map((c) => {
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
            <input type="text" value={emailSubject} onChange={(e) => setEmailSubject(e.target.value)} placeholder={t("emailcfg.subjectPlaceholder", "Subject (optional)")}
              className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
            <textarea value={emailBody} onChange={(e) => setEmailBody(e.target.value)} rows={4} placeholder={t("emailcfg.bodyPlaceholder", "HTML body (optional)")}
              className="w-full border border-border rounded-xl px-3 py-2 text-sm bg-background font-mono focus:outline-none focus:ring-2 focus:ring-primary/30" />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.totalTimeLabel")}</label>
            <div className="flex items-center gap-3">
              <input type="number" min={10} max={180} className="w-24 border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                value={form.time_limit_minutes} onChange={(e) => setForm({ ...form, time_limit_minutes: Math.max(10, parseInt(e.target.value) || 60) })} />
              <span className="text-xs text-muted-foreground">{t("adminNew.totalTimeHint")}</span>
            </div>
          </div>
        </div>

        {/* Competencies */}
        <div className="bg-card border border-border rounded-xl p-6 flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">{t("adminNew.competencies")}</h2>
              <p className="text-xs text-muted-foreground mt-0.5">{t("adminNew.competenciesDesc")}</p>
            </div>
            <button type="button" onClick={handleSuggestCompetencies} disabled={suggesting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50 shrink-0">
              {suggesting ? (<><div className="w-3 h-3 rounded-full border-2 border-primary border-t-transparent animate-spin" />{t("adminNew.suggesting")}</>)
                : (<><svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>{t("adminNew.generateWithAi")}</>)}
            </button>
          </div>

          {/* Filter */}
          <div className="flex gap-1.5">
            {(["all", "technical", "behavioural"] as const).map((f) => (
              <button key={f} type="button" onClick={() => setCompetencyFilter(f)}
                className={`text-xs px-2.5 py-1 rounded-lg border capitalize transition-colors ${competencyFilter === f ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/40"}`}>
                {f === "all" ? t("adminNew.filterAll") : f === "technical" ? t("adminNew.technical") : t("adminNew.behavioural")}{f !== "all" ? ` (${competencies.filter((c) => c.tag === f).length})` : ` (${competencies.length})`}
              </button>
            ))}
          </div>

          {/* Chips */}
          <div className="flex flex-wrap gap-2">
            {shownComps.map((c) => (
              <span key={c.name} className={`flex items-center gap-1.5 text-xs border rounded-full px-3 py-1 font-medium ${c.tag === "technical" ? "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20" : "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/20"}`}>
                {c.name}
                <button type="button" title={t("adminNew.toggleTag")}
                  onClick={() => setCompetencies(competencies.map((x) => x.name === c.name ? { ...x, tag: x.tag === "technical" ? "behavioural" : "technical" } : x))}
                  className="text-[10px] uppercase tracking-wide opacity-70 hover:opacity-100">{c.tag === "technical" ? t("adminNew.tagTechnicalAbbr") : t("adminNew.tagBehaviouralAbbr")}</button>
                <button type="button" onClick={() => setCompetencies(competencies.filter((x) => x.name !== c.name))} className="hover:text-destructive transition-colors">×</button>
              </span>
            ))}
            {shownComps.length === 0 && <p className="text-xs text-muted-foreground italic">{t("adminNew.noCompetencies")}</p>}
          </div>

          {/* Add input + tag picker */}
          <div className="flex gap-2">
            <select value={competencyTag} onChange={(e) => setCompetencyTag(e.target.value as "technical" | "behavioural")}
              className="border border-border rounded-lg px-2 py-2 text-xs bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
              <option value="technical">{t("adminNew.technical")}</option>
              <option value="behavioural">{t("adminNew.behavioural")}</option>
            </select>
            <input type="text" value={competencyInput} onChange={(e) => setCompetencyInput(e.target.value)}
              onKeyDown={(e) => { if ((e.key === "Enter" || e.key === ",") && competencyInput.trim()) { e.preventDefault(); addCompetency(); } }}
              className="flex-1 border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder={t("adminNew.competencyPlaceholder")} />
            <button type="button" onClick={addCompetency} className="px-3 py-2 rounded-lg bg-primary/10 text-primary text-sm font-medium hover:bg-primary/20 transition-colors">{t("adminNew.add")}</button>
          </div>
        </div>

        {/* Pre-assessment intake */}
        <div className="bg-card border border-border rounded-xl p-6 flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-foreground">{t("adminNew.intakeTitle")}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{t("adminNew.intakeDesc")}</p>
          </div>

          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" className="accent-primary" checked={cvRequired} onChange={(e) => setCvRequired(e.target.checked)} />
            <span className="text-sm text-foreground">{t("adminNew.requireCv")}</span>
          </label>

          <div className="flex flex-col gap-2">
            {intakeQuestions.map((q, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 border border-border rounded-lg p-2.5">
                <select value={q.type} onChange={(e) => updateIntakeQuestion(i, { type: e.target.value as IntakeQuestion["type"] })}
                  className="border border-border rounded-lg px-2 py-1.5 text-xs bg-background">
                  {INTAKE_TYPES.map((it) => <option key={it.value} value={it.value}>{t(it.label)}</option>)}
                </select>
                <input type="text" value={q.label} onChange={(e) => updateIntakeQuestion(i, { label: e.target.value })}
                  placeholder={t("adminNew.questionLabelPlaceholder")} className="flex-1 min-w-[160px] border border-border rounded-lg px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                {(q.type === "radio" || q.type === "checkbox") && (
                  <input type="text" value={(q.options ?? []).join(", ")} onChange={(e) => updateIntakeQuestion(i, { options: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                    placeholder={t("adminNew.optionsPlaceholder")} className="flex-1 min-w-[160px] border border-border rounded-lg px-3 py-1.5 text-xs bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                )}
                <button type="button" onClick={() => removeIntakeQuestion(i)} className="text-muted-foreground hover:text-destructive px-1">×</button>
              </div>
            ))}
          </div>
          <button type="button" onClick={addIntakeQuestion} className="self-start text-xs font-medium text-primary hover:text-primary/80">{t("adminNew.addIntakeQuestion")}</button>
        </div>

        {/* Tool config — hidden only for single behavioural/psychometric mode */}
        {showTools && <div className="bg-card border border-border rounded-xl p-6 flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold text-foreground">{t("adminNew.assessmentTools")}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{t("adminNew.assessmentToolsDesc")}</p>
          </div>

          <div className="grid gap-2">
            {(Object.keys(tools) as (keyof typeof tools)[]).map((tool) => {
              const isCountable = COUNTABLE.includes(tool as CountableTool);
              const countKey = tool as CountableTool;
              const count = toolCounts[countKey];
              const plannerMode = count === null;
              return (
                <div key={tool} className={`p-3 rounded-xl border transition-all ${tools[tool] ? "border-primary/40 bg-primary/5" : "border-border"}`}>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input type="checkbox" className="accent-primary shrink-0" checked={tools[tool]} onChange={(e) => setTools({ ...tools, [tool]: e.target.checked })} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground capitalize">{tool}</p>
                      <p className="text-xs text-muted-foreground">{t(TOOL_DESCRIPTIONS[tool])}</p>
                    </div>
                    {tool === "task" && <span className="text-xs text-muted-foreground shrink-0">1×</span>}
                  </label>
                  {tools[tool] && isCountable && (
                    <div className="mt-2.5 ml-7 flex flex-wrap items-center gap-3">
                      <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground select-none">
                        <input type="checkbox" className="accent-primary" checked={plannerMode}
                          onChange={(e) => setToolCounts({ ...toolCounts, [countKey]: e.target.checked ? null : (countKey === "mcq" ? 5 : countKey === "voice" ? 3 : 1) })} />
                        {t("adminNew.letPlannerDecide")}
                      </label>
                      {!plannerMode && (
                        <div className="flex items-center gap-1.5">
                          <input type="number" min={1} max={20} value={count ?? 1}
                            onChange={(e) => setToolCounts({ ...toolCounts, [countKey]: Math.max(1, parseInt(e.target.value) || 1) })}
                            className="w-14 border border-border rounded-lg px-2 py-1 text-sm text-center bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                          <span className="text-xs text-muted-foreground">{t("adminNew.questions")}</span>
                        </div>
                      )}
                      {tool === "coding" && (
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-muted-foreground">{t("adminNew.languageLabel")}</span>
                          <select value={codingLanguage} onChange={(e) => setCodingLanguage(e.target.value)}
                            className="border border-border rounded-lg px-2 py-1 text-xs bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                            {SUPPORTED_LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
                          </select>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>}

        {/* Task config */}
        {showTools && tools.task && (
          <div className="bg-card border border-primary/30 rounded-xl p-6 flex flex-col gap-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-foreground">{t("adminNew.taskConfigTitle")}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">{t("adminNew.taskConfigDesc")}</p>
              </div>
              <button type="button" onClick={handleGenerateTask} disabled={generatingTask}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20 transition-colors disabled:opacity-50 shrink-0">
                {generatingTask ? (<><div className="w-3 h-3 rounded-full border-2 border-primary border-t-transparent animate-spin" />{t("adminNew.generating")}</>)
                  : (<><svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>{t("adminNew.generateWithAi")}</>)}
              </button>
            </div>
            {taskConfig.brief && (
              <div className="flex items-center gap-2 text-xs text-green-700 bg-green-50 dark:bg-green-950 dark:text-green-300 border border-green-200 dark:border-green-800 rounded-lg px-3 py-2">
                <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                {t("adminNew.aiGeneratedNotice")}
              </div>
            )}
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.taskBriefLabel")}</label>
              <textarea required={tools.task} className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[100px] resize-none"
                placeholder={t("adminNew.taskBriefPlaceholder")}
                value={taskConfig.brief} onChange={(e) => setTaskConfig({ ...taskConfig, brief: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.deliverablesLabel")} <span className="normal-case font-normal">{t("adminNew.onePerLine")}</span></label>
              <textarea className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[70px] resize-none font-mono"
                placeholder={"post.txt\nREADME.md"} value={taskConfig.deliverables} onChange={(e) => setTaskConfig({ ...taskConfig, deliverables: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.rubricLabel")} <span className="normal-case font-normal">{t("adminNew.rubricHint")}</span></label>
              <textarea className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[90px] resize-none font-mono"
                placeholder={t("adminNew.rubricPlaceholder")} value={taskConfig.rubric} onChange={(e) => setTaskConfig({ ...taskConfig, rubric: e.target.value })} />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1">{t("adminNew.timeLimitLabel")}</label>
              <input type="number" min={10} max={60} className="w-32 border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
                value={taskConfig.time_limit_minutes} onChange={(e) => setTaskConfig({ ...taskConfig, time_limit_minutes: parseInt(e.target.value) || 30 })} />
            </div>
          </div>
        )}

        {/* Time budget preview */}
        {showTools && (() => {
          const budget = calcBudget(tools, toolCounts, form.time_limit_minutes);
          const pct = Math.min(100, Math.round((budget.total / form.time_limit_minutes) * 100));
          return (
            <div className={`rounded-xl border p-4 flex flex-col gap-3 ${budget.ok ? "border-border bg-card" : "border-destructive/50 bg-destructive/5"}`}>
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold text-foreground">{t("adminNew.timeBudgetPreview")}</p>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${budget.ok ? (budget.slack < 5 ? "bg-yellow-500/10 text-yellow-700 dark:text-yellow-300" : "bg-green-500/10 text-green-700 dark:text-green-300") : "bg-destructive/10 text-destructive"}`}>
                  {budget.ok ? (budget.slack < 5 ? `${t("adminNew.budgetTightPrefix")} ${budget.slack} ${t("adminNew.minToSpare")}` : `${t("adminNew.budgetOkPrefix")} ${budget.slack} ${t("adminNew.minSlack")}`) : `${t("adminNew.budgetOverPrefix")} ${-budget.slack} ${t("adminNew.min")}`}
                </span>
              </div>
              <div className="h-2 bg-secondary rounded-full overflow-hidden">
                <div className={`h-full rounded-full transition-all ${budget.ok ? "bg-primary" : "bg-destructive"}`} style={{ width: `${pct}%` }} />
              </div>
              <div className="flex flex-col gap-1">
                {budget.rows.map((r) => (
                  <div key={r.tool} className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="capitalize flex items-center gap-1.5">{r.tool}{!r.locked && <span className="text-[10px] text-primary/70 italic">{t("adminNew.auto")}</span>}</span>
                    <span>{r.count} × {TOOL_TIME[r.tool]} {t("adminNew.min")} = <span className="text-foreground font-medium">{r.mins} {t("adminNew.min")}</span></span>
                  </div>
                ))}
                <div className="border-t border-border mt-1 pt-1 flex justify-between text-xs font-medium">
                  <span className="text-muted-foreground">{t("adminNew.totalNeeded")}</span>
                  <span className={budget.ok ? "text-foreground" : "text-destructive"}>{budget.total} / {form.time_limit_minutes} {t("adminNew.min")}</span>
                </div>
              </div>
              {!budget.ok && <p className="text-xs text-destructive">{t("adminNew.budgetOverflow")}</p>}
            </div>
          );
        })()}

        <div className="flex gap-3">
          <button type="submit" disabled={saving || !budgetOk}
            className="px-6 py-2.5 rounded-lg bg-primary text-white font-medium text-sm hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {saving ? t("adminNew.creating") : t("adminNew.createAssessment")}
          </button>
          <button type="button" onClick={() => router.back()} className="px-6 py-2.5 rounded-lg border border-border text-foreground font-medium text-sm hover:bg-secondary transition-colors">{t("adminNew.cancel")}</button>
        </div>
      </form>
    </div>
  );
}
