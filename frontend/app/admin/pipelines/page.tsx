"use client";

import { useEffect, useState } from "react";
import { ArrowUp, ArrowDown, Plus, X } from "lucide-react";
import {
  listPipelines,
  createPipeline,
  getPipeline,
  setPipelineStages,
  enrollInPipeline,
  deletePipeline,
  listTemplates,
} from "@/lib/adminApi";
import type { Pipeline, PipelineDetail, Template } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

export default function AdminPipelinesPage() {
  const { t } = useI18n();

  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // New-pipeline form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  // Selected pipeline detail panel
  const [detail, setDetail] = useState<PipelineDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Stage editor
  const [orderedStageIds, setOrderedStageIds] = useState<string[]>([]);
  const [savingStages, setSavingStages] = useState(false);
  const [stagesSaved, setStagesSaved] = useState(false);

  // Enrollment
  const [emailsText, setEmailsText] = useState("");
  const [enrolling, setEnrolling] = useState(false);
  const [enrollMsg, setEnrollMsg] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setPipelines(await listPipelines());
      setError("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.loadError", "Failed to load pipelines"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    listTemplates().then(setTemplates).catch(() => {});
  }, []);

  const titleFor = (id: string) =>
    templates.find((tp) => tp.id === id)?.title ??
    detail?.stages.find((s) => s.template_id === id)?.title ??
    id;

  const openDetail = async (id: string) => {
    setDetailLoading(true);
    setStagesSaved(false);
    setEnrollMsg("");
    setEmailsText("");
    try {
      const d = await getPipeline(id);
      setDetail(d);
      setOrderedStageIds(
        [...d.stages].sort((a, b) => a.stage_order - b.stage_order).map((s) => s.template_id),
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.loadError", "Failed to load pipelines"));
    } finally {
      setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    setDetail(null);
    setOrderedStageIds([]);
    setEmailsText("");
    setEnrollMsg("");
    setStagesSaved(false);
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setError("");
    try {
      await createPipeline({ name: newName.trim(), description: newDesc.trim() || undefined });
      setNewName("");
      setNewDesc("");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.createError", "Failed to create pipeline"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(t("pipeline.deleteConfirm", 'Delete pipeline "{name}"? This cannot be undone.').replace("{name}", name)))
      return;
    try {
      await deletePipeline(id);
      if (detail?.id === id) closeDetail();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.deleteError", "Failed to delete pipeline"));
    }
  };

  const addStage = (id: string) => {
    setStagesSaved(false);
    setOrderedStageIds((prev) => (prev.includes(id) ? prev : [...prev, id]));
  };
  const removeStage = (id: string) => {
    setStagesSaved(false);
    setOrderedStageIds((prev) => prev.filter((x) => x !== id));
  };
  const moveStage = (idx: number, dir: -1 | 1) => {
    setStagesSaved(false);
    setOrderedStageIds((prev) => {
      const next = [...prev];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  };

  const handleSaveStages = async () => {
    if (!detail) return;
    setSavingStages(true);
    setStagesSaved(false);
    setError("");
    try {
      await setPipelineStages(detail.id, orderedStageIds);
      setStagesSaved(true);
      setTimeout(() => setStagesSaved(false), 3000);
      await openDetail(detail.id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.saveStagesError", "Failed to save stages"));
    } finally {
      setSavingStages(false);
    }
  };

  const handleEnroll = async () => {
    if (!detail) return;
    const emails = Array.from(
      new Set(emailsText.split(/[\n,]+/).map((e) => e.trim()).filter(Boolean)),
    );
    if (emails.length === 0) return;
    setEnrolling(true);
    setEnrollMsg("");
    setError("");
    try {
      const res = await enrollInPipeline(detail.id, { emails });
      setEnrollMsg(t("pipeline.enrolledResult", "Enrolled {count} candidate(s)").replace("{count}", String(res.enrolled)));
      setEmailsText("");
      await openDetail(detail.id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("pipeline.enrollError", "Failed to enroll candidates"));
    } finally {
      setEnrolling(false);
    }
  };

  const available = templates.filter((tp) => !orderedStageIds.includes(tp.id));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t("pipeline.title", "Assessment Pipelines")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t("pipeline.subtitle", "Chain assessments into ordered stages and enroll candidates.")}
        </p>
      </div>

      {error && (
        <div className="bg-destructive/10 text-destructive rounded-lg px-4 py-3 text-sm">{error}</div>
      )}

      {/* New pipeline */}
      <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-4">
        <h2 className="text-base font-semibold text-foreground">{t("pipeline.newPipeline", "New pipeline")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="pl-name" className="text-sm font-medium text-foreground">
              {t("pipeline.nameLabel", "Name")}
            </label>
            <input
              id="pl-name"
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("pipeline.namePlaceholder", "e.g. Frontend Engineer hiring")}
              className="border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="pl-desc" className="text-sm font-medium text-foreground">
              {t("pipeline.descriptionLabel", "Description")}
            </label>
            <input
              id="pl-desc"
              type="text"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder={t("pipeline.descriptionPlaceholder", "Optional description")}
              className="border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>
        </div>
        <div>
          <button
            type="button"
            onClick={handleCreate}
            disabled={creating || !newName.trim()}
            className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {creating ? t("pipeline.creating", "Creating…") : t("pipeline.create", "Create pipeline")}
          </button>
        </div>
      </div>

      {/* Pipeline list */}
      {loading ? (
        <p className="text-muted-foreground text-sm">{t("pipeline.loading", "Loading…")}</p>
      ) : pipelines.length === 0 ? (
        <div className="border-2 border-dashed border-border rounded-xl p-10 text-center">
          <p className="text-muted-foreground text-sm">{t("pipeline.empty", "No pipelines yet. Create your first one above.")}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {pipelines.map((p) => {
            const stageCount = p.stages?.length ?? 0;
            const enrolled = p.enrollment_count ?? 0;
            return (
              <div key={p.id} className="bg-card border border-border rounded-xl p-5 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-foreground truncate">{p.name}</h3>
                  {p.description && (
                    <p className="text-sm text-muted-foreground mt-0.5 truncate">{p.description}</p>
                  )}
                  <div className="flex gap-2 flex-wrap mt-2">
                    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-primary/10 text-primary">
                      {stageCount} {stageCount === 1 ? t("pipeline.stageSingular", "stage") : t("pipeline.stagePlural", "stages")}
                    </span>
                    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-secondary text-muted-foreground">
                      {enrolled} {enrolled === 1 ? t("pipeline.candidateSingular", "candidate") : t("pipeline.candidatePlural", "candidates")}
                    </span>
                  </div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => openDetail(p.id)}
                    className="text-xs px-3 py-1.5 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
                  >
                    {t("pipeline.manage", "Manage")}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(p.id, p.name)}
                    className="text-xs px-3 py-1.5 rounded-lg text-destructive hover:bg-destructive/10 transition-colors"
                  >
                    {t("pipeline.delete", "Delete")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Detail / management panel */}
      {detail && (
        <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="text-lg font-bold text-foreground truncate">{detail.name}</h2>
              {detail.description && (
                <p className="text-sm text-muted-foreground mt-0.5">{detail.description}</p>
              )}
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                onClick={() => handleDelete(detail.id, detail.name)}
                className="text-xs px-3 py-1.5 rounded-lg text-destructive hover:bg-destructive/10 transition-colors"
              >
                {t("pipeline.delete", "Delete")}
              </button>
              <button
                type="button"
                onClick={closeDetail}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
              >
                <X size={14} /> {t("pipeline.close", "Close")}
              </button>
            </div>
          </div>

          {detailLoading ? (
            <p className="text-muted-foreground text-sm">{t("pipeline.loading", "Loading…")}</p>
          ) : (
            <div className="grid gap-6 lg:grid-cols-2">
              {/* Stages editor */}
              <div className="flex flex-col gap-4">
                <div>
                  <h3 className="text-base font-semibold text-foreground">{t("pipeline.stagesHeading", "Stages")}</h3>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("pipeline.stagesDesc", "Pick assessments and order them. Candidates progress through stages in order.")}
                  </p>
                </div>

                {/* Selected stages (ordered) */}
                <div className="flex flex-col gap-2">
                  <p className="text-sm font-medium text-foreground">{t("pipeline.selectedStages", "Selected stages (in order)")}</p>
                  {orderedStageIds.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("pipeline.noStagesSelected", "No stages selected yet.")}</p>
                  ) : (
                    <ol className="flex flex-col gap-2">
                      {orderedStageIds.map((id, idx) => (
                        <li key={id} className="flex items-center gap-2 border border-border rounded-lg px-3 py-2">
                          <span className="w-5 h-5 shrink-0 rounded-full bg-primary/10 text-primary text-xs font-semibold flex items-center justify-center">
                            {idx + 1}
                          </span>
                          <span className="flex-1 text-sm text-foreground truncate">{titleFor(id)}</span>
                          <button
                            type="button"
                            onClick={() => moveStage(idx, -1)}
                            disabled={idx === 0}
                            aria-label={t("pipeline.moveUp", "Move up")}
                            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-30 transition-colors"
                          >
                            <ArrowUp size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => moveStage(idx, 1)}
                            disabled={idx === orderedStageIds.length - 1}
                            aria-label={t("pipeline.moveDown", "Move down")}
                            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-30 transition-colors"
                          >
                            <ArrowDown size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => removeStage(id)}
                            aria-label={t("pipeline.removeStage", "Remove")}
                            className="p-1 rounded-md text-destructive hover:bg-destructive/10 transition-colors"
                          >
                            <X size={14} />
                          </button>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>

                {/* Available assessments */}
                <div className="flex flex-col gap-2">
                  <p className="text-sm font-medium text-foreground">{t("pipeline.availableAssessments", "Available assessments")}</p>
                  {templates.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("pipeline.noTemplates", "No assessments available. Create one first.")}</p>
                  ) : available.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("pipeline.allAdded", "All assessments have been added.")}</p>
                  ) : (
                    <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
                      {available.map((tp) => (
                        <div key={tp.id} className="flex items-center gap-2 border border-border rounded-lg px-3 py-2">
                          <span className="flex-1 text-sm text-foreground truncate">{tp.title}</span>
                          <button
                            type="button"
                            onClick={() => addStage(tp.id)}
                            className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
                          >
                            <Plus size={13} /> {t("pipeline.addStage", "Add")}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={handleSaveStages}
                    disabled={savingStages}
                    className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
                  >
                    {savingStages ? t("pipeline.savingStages", "Saving…") : t("pipeline.saveStages", "Save stages")}
                  </button>
                  {stagesSaved && (
                    <span className="text-sm font-medium text-green-700 dark:text-green-300">
                      {t("pipeline.stagesSaved", "Stages saved")}
                    </span>
                  )}
                </div>
              </div>

              {/* Enroll + enrollments */}
              <div className="flex flex-col gap-4">
                <div>
                  <h3 className="text-base font-semibold text-foreground">{t("pipeline.enrollHeading", "Enroll candidates")}</h3>
                  <p className="text-xs text-muted-foreground mt-1">
                    {t("pipeline.enrollDesc", "Enter candidate emails separated by commas or new lines.")}
                  </p>
                </div>

                <textarea
                  dir="ltr"
                  value={emailsText}
                  onChange={(e) => setEmailsText(e.target.value)}
                  rows={4}
                  placeholder={t("pipeline.emailsPlaceholder", "alice@example.com, bob@example.com")}
                  className="border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 resize-y"
                />
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={handleEnroll}
                    disabled={enrolling || !emailsText.trim()}
                    className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
                  >
                    {enrolling ? t("pipeline.enrolling", "Enrolling…") : t("pipeline.enroll", "Enroll")}
                  </button>
                  {enrollMsg && (
                    <span className="text-sm font-medium text-green-700 dark:text-green-300">{enrollMsg}</span>
                  )}
                </div>

                <div className="flex flex-col gap-2">
                  <p className="text-sm font-medium text-foreground">
                    {t("pipeline.enrollments", "Enrollments")}
                    {detail.enrollments.length > 0 && (
                      <span className="ml-2 text-xs font-normal text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
                        {detail.enrollments.length}
                      </span>
                    )}
                  </p>
                  {detail.enrollments.length === 0 ? (
                    <p className="text-xs text-muted-foreground">{t("pipeline.noEnrollments", "No candidates enrolled yet.")}</p>
                  ) : (
                    <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
                      {detail.enrollments.map((en) => (
                        <div key={en.candidate_email} className="flex items-center gap-3 border border-border rounded-lg px-3 py-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-foreground truncate">{en.candidate_name || en.candidate_email}</p>
                            {en.candidate_name && (
                              <p className="text-xs text-muted-foreground truncate">{en.candidate_email}</p>
                            )}
                          </div>
                          <span className="text-xs text-muted-foreground shrink-0">
                            {t("pipeline.stageLabel", "Stage")} {en.current_stage + 1}
                          </span>
                          <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-secondary text-muted-foreground shrink-0 capitalize">
                            {en.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
