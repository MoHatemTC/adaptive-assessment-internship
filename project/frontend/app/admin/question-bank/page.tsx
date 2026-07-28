"use client";

import { useEffect, useState } from "react";
import { listTemplates, listBankQuestions, createBankQuestion, deleteBankQuestion,
  listQuestionSets, createQuestionSet, getQuestionSet, addQuestionsToSet, removeQuestionFromSet, deleteQuestionSet } from "@/lib/adminApi";
import type { Template, QuestionBankItem, QuestionBankCreate, QuestionSet, QuestionSetDetail } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const GLOBAL_TEMPLATE = "__global__";

const TOOL_TYPES = ["mcq", "voice", "video", "coding", "task", "visualization", "personality"] as const;
const DIFFICULTIES = ["easy", "medium", "hard"] as const;

const TOOL_COLORS: Record<string, string> = {
  mcq:           "bg-primary/10 text-primary",
  voice:         "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  video:         "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  coding:        "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  task:          "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300",
  visualization: "bg-pink-100 text-pink-800 dark:bg-pink-950 dark:text-pink-300",
  personality:   "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300",
};

const DIFF_COLORS: Record<string, string> = {
  easy:   "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  hard:   "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

function parseCsv(text: string, templateId: string | null): QuestionBankCreate[] {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return [];
  const header = lines[0].split(",").map((h) => h.trim().toLowerCase());
  return lines.slice(1).map((line) => {
    const vals = line.split(",").map((v) => v.trim().replace(/^"|"$/g, ""));
    const row: Record<string, string> = {};
    header.forEach((h, i) => { row[h] = vals[i] ?? ""; });

    const payload: Record<string, unknown> = {};
    if (row.option_a) {
      payload.options = [
        { id: "a", text: row.option_a },
        { id: "b", text: row.option_b || "" },
        { id: "c", text: row.option_c || "" },
        { id: "d", text: row.option_d || "" },
      ].filter((o) => o.text);
      payload.answer_key = { correct_id: row.correct_id || "a", explanation: row.explanation || "" };
    }

    return {
      template_id:  templateId,
      tool_type:    row.tool_type || "mcq",
      skill_target: row.skill_target || "",
      difficulty:   (row.difficulty as "easy" | "medium" | "hard") || "medium",
      tags:         row.tags ? row.tags.split(";").map((t) => t.trim()) : [],
      body:         row.body || "",
      payload,
    } satisfies QuestionBankCreate;
  }).filter((q) => q.body && q.skill_target);
}

export default function QuestionBankPage() {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [questions, setQuestions] = useState<QuestionBankItem[]>([]);
  const [loading, setLoading] = useState(false);

  // Filters
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [skillFilter, setSkillFilter] = useState("");
  const [diffFilter, setDiffFilter] = useState("");
  const [search, setSearch] = useState("");

  // Add question form
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<QuestionBankCreate>({
    template_id: "", tool_type: "mcq", skill_target: "", difficulty: "medium",
    tags: [], body: "", payload: {},
  });
  const [tagInput, setTagInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  // MCQ option fields
  const [options, setOptions] = useState([
    { id: "a", text: "" }, { id: "b", text: "" }, { id: "c", text: "" }, { id: "d", text: "" },
  ]);
  const [correctId, setCorrectId] = useState("a");
  const [explanation, setExplanation] = useState("");

  // CSV upload
  const [csvText, setCsvText] = useState("");
  const [csvParsed, setCsvParsed] = useState<QuestionBankCreate[]>([]);
  const [csvUploading, setCsvUploading] = useState(false);
  const [csvSuccess, setCsvSuccess] = useState("");

  // Per-type payload fields (B3)
  const [starterCode, setStarterCode] = useState("");
  const [language, setLanguage] = useState("python");
  const [testCases, setTestCases] = useState<{ input: string; expected_output: string }[]>([{ input: "", expected_output: "" }]);
  const [evalCriteria, setEvalCriteria] = useState<string[]>([""]);
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(5);
  const [brief, setBrief] = useState("");
  const [deliverables, setDeliverables] = useState<string[]>([""]);
  const [rubric, setRubric] = useState<{ criterion: string }[]>([{ criterion: "" }]);
  const [expectedInsights, setExpectedInsights] = useState("");

  // Question Sets (B2)
  const [showSets, setShowSets] = useState(false);
  const [sets, setSets] = useState<QuestionSet[]>([]);
  const [newSetName, setNewSetName] = useState("");
  const [creatingSet, setCreatingSet] = useState(false);
  const [setsError, setSetsError] = useState("");
  const [selectedForSet, setSelectedForSet] = useState<Set<string>>(new Set());
  const [addTargetSet, setAddTargetSet] = useState("");
  const [viewingSet, setViewingSet] = useState<QuestionSetDetail | null>(null);

  // null template_id = global question (usable by all assessments)
  const effectiveTemplateId = selectedTemplate === GLOBAL_TEMPLATE ? null : selectedTemplate;

  const loadSets = async () => {
    try { setSets(await listQuestionSets()); } catch { /* ignore */ }
  };

  useEffect(() => {
    listTemplates().then((t) => setTemplates(t));
    loadSets();
  }, []);

  const loadQuestions = async () => {
    if (!selectedTemplate) { setQuestions([]); return; }
    setLoading(true);
    const isGlobal = selectedTemplate === GLOBAL_TEMPLATE;
    const res = await listBankQuestions({
      template_id: isGlobal ? undefined : selectedTemplate,
      tool_type: toolFilter || undefined,
      skill_target: skillFilter || undefined,
      difficulty: diffFilter || undefined,
      search: search || undefined,
    });
    // "Global" view shows only questions not scoped to a specific assessment.
    setQuestions(isGlobal ? res.filter((q) => !q.template_id) : res);
    setLoading(false);
  };

  useEffect(() => { loadQuestions(); }, [selectedTemplate, toolFilter, skillFilter, diffFilter, search]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError(""); setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (form.tool_type === "mcq") {
        payload.options = options.filter((o) => o.text.trim());
        payload.answer_key = { correct_id: correctId, explanation };
        payload.time_limit_seconds = 60;
      } else if (form.tool_type === "coding") {
        payload.starter_code = starterCode;
        payload.language = language.trim() || "python";
        payload.test_cases = testCases.filter((tc) => tc.input.trim() || tc.expected_output.trim());
      } else if (form.tool_type === "voice" || form.tool_type === "video") {
        payload.evaluation_criteria = evalCriteria.map((c) => c.trim()).filter(Boolean);
        payload.time_limit_minutes = timeLimitMinutes;
      } else if (form.tool_type === "task") {
        payload.brief = brief;
        payload.deliverables = deliverables.map((d) => d.trim()).filter(Boolean);
        payload.rubric = rubric.filter((r) => r.criterion.trim());
      } else if (form.tool_type === "visualization") {
        payload.expected_insights = expectedInsights;
      }
      await createBankQuestion({ ...form, payload, template_id: effectiveTemplateId });
      setShowForm(false);
      setForm({ template_id: "", tool_type: "mcq", skill_target: "", difficulty: "medium", tags: [], body: "", payload: {} });
      setOptions([{ id: "a", text: "" }, { id: "b", text: "" }, { id: "c", text: "" }, { id: "d", text: "" }]);
      setCorrectId("a"); setExplanation(""); setTagInput("");
      setStarterCode(""); setLanguage("python"); setTestCases([{ input: "", expected_output: "" }]);
      setEvalCriteria([""]); setTimeLimitMinutes(5);
      setBrief(""); setDeliverables([""]); setRubric([{ criterion: "" }]);
      setExpectedInsights("");
      await loadQuestions();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : t("admin.bank.saveFailed", "Save failed"));
    } finally {
      setSaving(false);
    }
  };

  const handleCsvUpload = async () => {
    if (!csvParsed.length || !selectedTemplate) return;
    setCsvUploading(true);
    try {
      const { listBankQuestions: _, ...api } = await import("@/lib/adminApi");
      const r = await api.bulkCreateBankQuestions(csvParsed.map((q) => ({ ...q, template_id: effectiveTemplateId })));
      setCsvSuccess(`${t("admin.bank.addedPrefix", "Added")} ${r.created} ${t("admin.bank.addedSuffix", "questions")}`);
      setCsvText(""); setCsvParsed([]);
      await loadQuestions();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : t("admin.bank.bulkImportFailed", "Bulk import failed"));
    } finally {
      setCsvUploading(false);
    }
  };

  // ── Question Sets (B2) ──────────────────────────────────────
  const toggleSelect = (id: string) => {
    setSelectedForSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleCreateSet = async () => {
    if (!newSetName.trim()) return;
    setSetsError(""); setCreatingSet(true);
    try {
      await createQuestionSet({ name: newSetName.trim() });
      setNewSetName("");
      await loadSets();
    } catch (e) {
      setSetsError(e instanceof Error ? e.message : t("admin.bank.setSaveFailed", "Could not save set"));
    } finally {
      setCreatingSet(false);
    }
  };

  const handleDeleteSet = async (id: string) => {
    setSetsError("");
    try {
      await deleteQuestionSet(id);
      if (viewingSet?.id === id) setViewingSet(null);
      await loadSets();
    } catch (e) {
      setSetsError(e instanceof Error ? e.message : t("admin.bank.setDeleteFailed", "Could not delete set"));
    }
  };

  const handleViewSet = async (id: string) => {
    if (viewingSet?.id === id) { setViewingSet(null); return; }
    try { setViewingSet(await getQuestionSet(id)); } catch { /* ignore */ }
  };

  const handleAddToSet = async () => {
    if (!addTargetSet || selectedForSet.size === 0) return;
    setSetsError("");
    try {
      await addQuestionsToSet(addTargetSet, Array.from(selectedForSet));
      setSelectedForSet(new Set());
      await loadSets();
      if (viewingSet?.id === addTargetSet) setViewingSet(await getQuestionSet(addTargetSet));
    } catch (e) {
      setSetsError(e instanceof Error ? e.message : t("admin.bank.setAddFailed", "Could not add to set"));
    }
  };

  const handleRemoveFromSet = async (setId: string, questionId: string) => {
    try {
      await removeQuestionFromSet(setId, questionId);
      setViewingSet(await getQuestionSet(setId));
      await loadSets();
    } catch { /* ignore */ }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{t("admin.bank.title", "Question Bank")}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t("admin.bank.subtitle", "Manage admin-uploaded questions for agentic personalization")}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowSets((v) => !v)}
            className="px-4 py-2 rounded-xl border border-border text-sm font-medium hover:bg-secondary transition-colors"
          >
            {t("admin.bank.questionSets", "Question Sets")}
          </button>
          <button
            onClick={() => setShowForm(!showForm)}
            disabled={!selectedTemplate}
            className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            + {t("admin.bank.addQuestion", "Add Question")}
          </button>
        </div>
      </div>

      {/* Template selector + filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <select
          value={selectedTemplate}
          onChange={(e) => setSelectedTemplate(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          <option value="">{t("admin.bank.selectTemplate", "Select a template…")}</option>
          <option value={GLOBAL_TEMPLATE}>{t("admin.bank.globalTemplate", "Global (all assessments)")}</option>
          {templates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
        </select>
        {selectedTemplate && (
          <>
            <select value={toolFilter} onChange={(e) => setToolFilter(e.target.value)}
              className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30">
              <option value="">{t("admin.bank.allTypes", "All types")}</option>
              {TOOL_TYPES.map((t) => <option key={t} value={t} className="capitalize">{t}</option>)}
            </select>
            <input
              type="text"
              placeholder={t("admin.bank.skillTargetPlaceholder", "Skill target…")}
              value={skillFilter}
              onChange={(e) => setSkillFilter(e.target.value)}
              className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 w-40"
            />
            <select value={diffFilter} onChange={(e) => setDiffFilter(e.target.value)}
              className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30">
              <option value="">{t("admin.bank.allDifficulties", "All difficulties")}</option>
              {DIFFICULTIES.map((d) => <option key={d} value={d} className="capitalize">{d}</option>)}
            </select>
            <input
              type="text"
              placeholder={t("admin.bank.searchPlaceholder", "Search…")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 w-40"
            />
            <span className="text-sm text-muted-foreground ml-auto">{questions.length} {t("admin.bank.questionsCount", `question${questions.length !== 1 ? "s" : ""}`)}</span>
          </>
        )}
      </div>

      {/* Question Sets (B2) */}
      {showSets && (
        <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
          <div>
            <p className="text-sm font-semibold text-foreground">{t("admin.bank.questionSets", "Question Sets")}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{t("admin.bank.setsSubtitle", "Reusable groups of bank questions")}</p>
          </div>
          {setsError && <p className="text-sm text-destructive">{setsError}</p>}

          <div className="flex gap-2">
            <input
              value={newSetName}
              onChange={(e) => setNewSetName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleCreateSet(); } }}
              placeholder={t("admin.bank.newSetPlaceholder", "New set name…")}
              className="flex-1 border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <button type="button" onClick={handleCreateSet} disabled={creatingSet || !newSetName.trim()}
              className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {creatingSet ? t("admin.bank.creating", "Creating…") : t("admin.bank.createSet", "Create Set")}
            </button>
          </div>

          {sets.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("admin.bank.noSets", "No sets yet. Create one above.")}</p>
          ) : (
            <div className="flex flex-col gap-2">
              {sets.map((s) => (
                <div key={s.id} className="flex flex-col gap-2 border border-border rounded-xl p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{s.name}</span>
                    <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">{s.item_count ?? 0} {t("admin.bank.itemsCount", "items")}</span>
                    <div className="ml-auto flex items-center gap-3">
                      <button type="button" onClick={() => handleViewSet(s.id)} className="text-xs text-primary hover:text-primary/70">
                        {viewingSet?.id === s.id ? t("admin.bank.hideSet", "Hide") : t("admin.bank.viewSet", "View")}
                      </button>
                      <button type="button" onClick={() => handleDeleteSet(s.id)} className="text-xs text-destructive hover:text-destructive/70">
                        {t("admin.bank.delete", "Delete")}
                      </button>
                    </div>
                  </div>
                  {viewingSet && viewingSet.id === s.id && (
                    <div className="flex flex-col gap-1.5 pt-1">
                      {viewingSet.questions.length === 0 ? (
                        <p className="text-xs text-muted-foreground">{t("admin.bank.setEmpty", "This set has no questions yet.")}</p>
                      ) : viewingSet.questions.map((sq) => (
                        <div key={sq.id} className="flex items-start gap-2 text-xs bg-secondary/50 rounded-lg px-2.5 py-1.5">
                          <span className={`font-medium px-1.5 py-0.5 rounded-full capitalize ${TOOL_COLORS[sq.tool_type] ?? "bg-secondary text-muted-foreground"}`}>{sq.tool_type}</span>
                          <span className="flex-1 text-foreground leading-snug">{sq.body}</span>
                          <button type="button" onClick={() => handleRemoveFromSet(s.id, sq.id)} className="text-destructive hover:text-destructive/70 shrink-0">
                            {t("admin.bank.remove", "Remove")}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Add question form */}
      {showForm && selectedTemplate && (
        <form onSubmit={handleSave} className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
          <p className="text-sm font-semibold text-foreground">{t("admin.bank.addQuestion", "Add Question")}</p>
          {saveError && <p className="text-sm text-destructive">{saveError}</p>}

          <div className="grid grid-cols-3 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.typeLabel", "Type")}</label>
              <select value={form.tool_type} onChange={(e) => setForm({ ...form, tool_type: e.target.value })}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                {TOOL_TYPES.map((t) => <option key={t} value={t} className="capitalize">{t}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.skillTargetLabel", "Skill Target")}</label>
              <input value={form.skill_target} onChange={(e) => setForm({ ...form, skill_target: e.target.value })}
                placeholder={t("admin.bank.skillTargetExample", "e.g. Communication")} required
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.difficultyLabel", "Difficulty")}</label>
              <select value={form.difficulty} onChange={(e) => setForm({ ...form, difficulty: e.target.value as "easy" | "medium" | "hard" })}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
                {DIFFICULTIES.map((d) => <option key={d} value={d} className="capitalize">{d}</option>)}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.questionBodyLabel", "Question Body")}</label>
            <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })}
              rows={4} required placeholder={t("admin.bank.questionBodyPlaceholder", "Write the question here…")}
              className="border border-border rounded-xl px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30" />
          </div>

          {/* MCQ-specific options */}
          {form.tool_type === "mcq" && (
            <div className="flex flex-col gap-3">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.optionsLabel", "Options")}</label>
              {options.map((opt, i) => (
                <div key={opt.id} className="flex items-center gap-2">
                  <input type="radio" name="correct" value={opt.id} checked={correctId === opt.id}
                    onChange={() => setCorrectId(opt.id)} className="accent-primary shrink-0" />
                  <span className="text-xs font-bold w-5 text-muted-foreground">{opt.id.toUpperCase()}.</span>
                  <input value={opt.text} onChange={(e) => {
                    const newOpts = [...options]; newOpts[i] = { ...opt, text: e.target.value };
                    setOptions(newOpts);
                  }}
                    placeholder={`${t("admin.bank.optionPlaceholder", "Option")} ${opt.id.toUpperCase()}`}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                </div>
              ))}
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.explanationLabel", "Explanation (correct answer)")}</label>
                <input value={explanation} onChange={(e) => setExplanation(e.target.value)}
                  placeholder={t("admin.bank.explanationPlaceholder", "Why is this option correct?")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
            </div>
          )}

          {/* Coding-specific */}
          {form.tool_type === "coding" && (
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2 flex flex-col gap-1">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.starterCodeLabel", "Starter Code")}</label>
                  <textarea value={starterCode} onChange={(e) => setStarterCode(e.target.value)} rows={4}
                    placeholder={t("admin.bank.starterCodePlaceholder", "Starter code the candidate begins with…")}
                    className="border border-border rounded-xl px-3 py-2 text-sm font-mono bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30" />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.languageLabel", "Language")}</label>
                  <input value={language} onChange={(e) => setLanguage(e.target.value)}
                    className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                </div>
              </div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.testCasesLabel", "Test Cases")}</label>
              {testCases.map((tc, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={tc.input} onChange={(e) => { const n = [...testCases]; n[i] = { ...tc, input: e.target.value }; setTestCases(n); }}
                    placeholder={t("admin.bank.testCaseInputPlaceholder", "Input")}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                  <input value={tc.expected_output} onChange={(e) => { const n = [...testCases]; n[i] = { ...tc, expected_output: e.target.value }; setTestCases(n); }}
                    placeholder={t("admin.bank.testCaseOutputPlaceholder", "Expected output")}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                  <button type="button" onClick={() => setTestCases(testCases.filter((_, j) => j !== i))}
                    className="text-destructive hover:text-destructive/70 text-sm shrink-0" title={t("admin.bank.remove", "Remove")}>×</button>
                </div>
              ))}
              <button type="button" onClick={() => setTestCases([...testCases, { input: "", expected_output: "" }])}
                className="self-start px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20">{t("admin.bank.addTestCase", "+ Add test case")}</button>
            </div>
          )}

          {/* Voice / Video-specific */}
          {(form.tool_type === "voice" || form.tool_type === "video") && (
            <div className="flex flex-col gap-3">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.evaluationCriteriaLabel", "Evaluation Criteria")}</label>
              {evalCriteria.map((c, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={c} onChange={(e) => { const n = [...evalCriteria]; n[i] = e.target.value; setEvalCriteria(n); }}
                    placeholder={t("admin.bank.criterionPlaceholder", "e.g. Clarity of explanation")}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                  <button type="button" onClick={() => setEvalCriteria(evalCriteria.filter((_, j) => j !== i))}
                    className="text-destructive hover:text-destructive/70 text-sm shrink-0" title={t("admin.bank.remove", "Remove")}>×</button>
                </div>
              ))}
              <button type="button" onClick={() => setEvalCriteria([...evalCriteria, ""])}
                className="self-start px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20">{t("admin.bank.addCriterion", "+ Add criterion")}</button>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.timeLimitMinutesLabel", "Time Limit (minutes)")}</label>
                <input type="number" min={1} value={timeLimitMinutes} onChange={(e) => setTimeLimitMinutes(Number(e.target.value) || 0)}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background w-32 focus:outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
            </div>
          )}

          {/* Task-specific */}
          {form.tool_type === "task" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.briefLabel", "Brief")}</label>
                <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={3}
                  placeholder={t("admin.bank.briefPlaceholder", "Describe the task…")}
                  className="border border-border rounded-xl px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30" />
              </div>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.deliverablesLabel", "Deliverables")}</label>
              {deliverables.map((d, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={d} onChange={(e) => { const n = [...deliverables]; n[i] = e.target.value; setDeliverables(n); }}
                    placeholder={t("admin.bank.deliverablePlaceholder", "e.g. A written summary")}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                  <button type="button" onClick={() => setDeliverables(deliverables.filter((_, j) => j !== i))}
                    className="text-destructive hover:text-destructive/70 text-sm shrink-0" title={t("admin.bank.remove", "Remove")}>×</button>
                </div>
              ))}
              <button type="button" onClick={() => setDeliverables([...deliverables, ""])}
                className="self-start px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20">{t("admin.bank.addDeliverable", "+ Add deliverable")}</button>
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.rubricLabel", "Rubric")}</label>
              {rubric.map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={r.criterion} onChange={(e) => { const n = [...rubric]; n[i] = { criterion: e.target.value }; setRubric(n); }}
                    placeholder={t("admin.bank.rubricCriterionPlaceholder", "e.g. Depth of analysis")}
                    className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
                  <button type="button" onClick={() => setRubric(rubric.filter((_, j) => j !== i))}
                    className="text-destructive hover:text-destructive/70 text-sm shrink-0" title={t("admin.bank.remove", "Remove")}>×</button>
                </div>
              ))}
              <button type="button" onClick={() => setRubric([...rubric, { criterion: "" }])}
                className="self-start px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20">{t("admin.bank.addCriterion", "+ Add criterion")}</button>
            </div>
          )}

          {/* Visualization-specific */}
          {form.tool_type === "visualization" && (
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.expectedInsightsLabel", "Expected Insights")}</label>
              <textarea value={expectedInsights} onChange={(e) => setExpectedInsights(e.target.value)} rows={3}
                placeholder={t("admin.bank.expectedInsightsPlaceholder", "What insights should the candidate surface?")}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
          )}

          {/* Tags */}
          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("admin.bank.tagsLabel", "Tags")}</label>
            <div className="flex flex-wrap gap-1.5">
              {form.tags.map((t) => (
                <span key={t} className="flex items-center gap-1 text-xs bg-primary/10 text-primary border border-primary/20 rounded-full px-2.5 py-1">
                  {t}
                  <button type="button" onClick={() => setForm({ ...form, tags: form.tags.filter((x) => x !== t) })} className="hover:text-destructive">×</button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    const t = tagInput.trim().replace(/,$/, "");
                    if (t && !form.tags.includes(t)) setForm({ ...form, tags: [...form.tags, t] });
                    setTagInput("");
                  }
                }}
                placeholder={t("admin.bank.tagsPlaceholder", "e.g. negotiation — press Enter")}
                className="flex-1 border border-border rounded-xl px-3 py-1.5 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30" />
              <button type="button" onClick={() => {
                const t = tagInput.trim();
                if (t && !form.tags.includes(t)) setForm({ ...form, tags: [...form.tags, t] });
                setTagInput("");
              }} className="px-3 py-1.5 rounded-xl bg-primary/10 text-primary text-xs font-medium hover:bg-primary/20">{t("admin.bank.addTagButton", "Add")}</button>
            </div>
          </div>

          <div className="flex gap-2 pt-2">
            <button type="submit" disabled={saving || !form.body || !form.skill_target}
              className="px-4 py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? t("admin.bank.saving", "Saving…") : t("admin.bank.saveQuestion", "Save Question")}
            </button>
            <button type="button" onClick={() => { setShowForm(false); setSaveError(""); }}
              className="px-4 py-2.5 rounded-xl border border-border text-sm hover:bg-secondary transition-colors">
              {t("admin.bank.cancel", "Cancel")}
            </button>
          </div>
        </form>
      )}

      {/* CSV Upload */}
      {selectedTemplate && (
        <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
          <p className="text-sm font-semibold text-foreground">{t("admin.bank.bulkUploadTitle", "Bulk Upload (CSV)")}</p>
          <p className="text-xs text-muted-foreground">
            {t("admin.bank.columnsLabel", "Columns:")} <code className="bg-secondary px-1 rounded">tool_type, skill_target, difficulty, tags, body, option_a, option_b, option_c, option_d, correct_id, explanation</code>
          </p>
          <textarea
            value={csvText}
            onChange={(e) => {
              setCsvText(e.target.value);
              setCsvParsed(e.target.value.trim() ? parseCsv(e.target.value, effectiveTemplateId) : []);
              setCsvSuccess("");
            }}
            rows={6}
            placeholder={"tool_type,skill_target,difficulty,tags,body,option_a,...\nvoice,Communication,medium,\"negotiation\",\"Describe a time...\",,,,,"}
            className="border border-border rounded-xl px-3 py-2 text-sm font-mono bg-background resize-none focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          {csvParsed.length > 0 && (
            <p className="text-xs text-primary font-medium">{csvParsed.length} {t("admin.bank.questionsParsed", `question${csvParsed.length !== 1 ? "s" : ""} parsed — ready to import`)}</p>
          )}
          {csvSuccess && <p className="text-sm text-green-600">{csvSuccess}</p>}
          <button
            onClick={handleCsvUpload}
            disabled={!csvParsed.length || csvUploading}
            className="self-start px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {csvUploading ? t("admin.bank.importing", "Importing…") : `${t("admin.bank.importPrefix", "Import")} ${csvParsed.length || ""} ${t("admin.bank.importSuffix", "Questions")}`}
          </button>
        </div>
      )}

      {/* Add-to-set toolbar (B2) */}
      {selectedTemplate && selectedForSet.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 bg-primary/5 border border-primary/20 rounded-xl px-4 py-2.5">
          <span className="text-sm font-medium text-foreground">{selectedForSet.size} {t("admin.bank.selected", "selected")}</span>
          <select value={addTargetSet} onChange={(e) => setAddTargetSet(e.target.value)}
            className="border border-border rounded-xl px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30">
            <option value="">{t("admin.bank.chooseSet", "Choose a set…")}</option>
            {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <button type="button" onClick={handleAddToSet} disabled={!addTargetSet}
            className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors">
            {t("admin.bank.addToSetPrefix", "Add")} {selectedForSet.size} {t("admin.bank.addToSetSuffix", "to set")}
          </button>
          <button type="button" onClick={() => setSelectedForSet(new Set())}
            className="text-sm text-muted-foreground hover:text-foreground">{t("admin.bank.clearSelection", "Clear")}</button>
        </div>
      )}

      {/* Questions list */}
      {!selectedTemplate ? (
        <div className="text-center py-16 text-muted-foreground text-sm">
          {t("admin.bank.emptySelectTemplate", "Select a template above to view and manage its question bank.")}
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : questions.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground text-sm">{t("admin.bank.emptyNoQuestions", "No questions yet. Add one above or upload a CSV.")}</div>
      ) : (
        <div className="flex flex-col gap-3">
          {questions.map((q) => (
            <div key={q.id} className="bg-card border border-border rounded-xl p-4 flex flex-col gap-2">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <input type="checkbox" checked={selectedForSet.has(q.id)} onChange={() => toggleSelect(q.id)}
                    className="accent-primary shrink-0" title={t("admin.bank.selectForSet", "Select for set")} />
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${TOOL_COLORS[q.tool_type] ?? "bg-secondary text-muted-foreground"}`}>{q.tool_type}</span>
                  {!q.template_id && <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{t("admin.bank.globalBadge", "Global")}</span>}
                  <span className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${DIFF_COLORS[q.difficulty] ?? "bg-secondary"}`}>{q.difficulty}</span>
                  <span className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">{q.skill_target}</span>
                  {q.tags.map((t) => <span key={t} className="text-xs text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">{t}</span>)}
                </div>
                <button
                  onClick={async () => { await deleteBankQuestion(q.id); await loadQuestions(); }}
                  className="text-xs text-destructive hover:text-destructive/70 shrink-0"
                >
                  {t("admin.bank.delete", "Delete")}
                </button>
              </div>
              <p className="text-sm text-foreground leading-relaxed">{q.body}</p>
              {(q.payload as { options?: unknown[] })?.options && (
                <div className="flex flex-col gap-1 mt-1">
                  {((q.payload as { options: { id: string; text: string }[] }).options).map((opt) => (
                    <div key={opt.id} className={`flex items-center gap-2 text-xs px-2 py-1 rounded-lg ${
                      (q.payload as { answer_key?: { correct_id: string } })?.answer_key?.correct_id === opt.id
                        ? "bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300 font-medium" : "text-muted-foreground"
                    }`}>
                      <span className="font-bold">{opt.id.toUpperCase()}.</span>
                      <span>{opt.text}</span>
                      {(q.payload as { answer_key?: { correct_id: string } })?.answer_key?.correct_id === opt.id && <span className="ml-auto">✓</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
