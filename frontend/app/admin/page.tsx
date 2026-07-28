"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listTemplates, deleteTemplate, publishTemplate, unpublishTemplate, getPlatformSettings, updatePlatformSettings } from "@/lib/adminApi";
import { Template } from "@/lib/types";
import { publicUrl } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

const TOOL_BADGE: Record<string, string> = {
  mcq:           "bg-primary/10 text-primary",
  voice:         "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  video:         "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  coding:        "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  task:          "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  visualization: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
};

export default function AdminDashboard() {
  const { t: tr } = useI18n();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [trackFilter, setTrackFilter] = useState("");
  const [accepting, setAccepting] = useState(true);
  const [togglingPlatform, setTogglingPlatform] = useState(false);

  useEffect(() => { getPlatformSettings().then((s) => setAccepting(s.accepting_sessions)).catch(() => {}); }, []);

  const togglePlatform = async () => {
    setTogglingPlatform(true);
    try {
      const s = await updatePlatformSettings({ accepting_sessions: !accepting });
      setAccepting(s.accepting_sessions);
    } catch { /* ignore */ } finally { setTogglingPlatform(false); }
  };

  const publishedCount = templates.filter((t) => t.is_published).length;

  // Distinct, non-empty track values across the loaded templates.
  const trackOptions = Array.from(
    new Set(templates.map((t) => t.track).filter((x): x is string => !!x && x.trim().length > 0)),
  ).sort((a, b) => a.localeCompare(b));

  const copyLink = (token: string, id: string) => {
    navigator.clipboard.writeText(publicUrl(`/assess/${token}`));
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const load = async () => {
    setLoading(true);
    try { setTemplates(await listTemplates()); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : tr("adminList.failedToLoad")); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleDelete = async (id: string, title: string) => {
    if (!confirm(tr("adminList.deleteConfirm").replace("{title}", title))) return;
    await deleteTemplate(id); load();
  };

  const handlePublish = async (id: string, published: boolean) => {
    published ? await unpublishTemplate(id) : await publishTemplate(id);
    load();
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{tr("adminList.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {tr("adminList.subtitle")}
          </p>
        </div>
        <Link
          href="/admin/assessment/new"
          className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors shrink-0"
        >
          {tr("adminList.newAssessment")}
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-5">
        <span className="text-xs font-medium text-muted-foreground px-3 py-1.5 rounded-lg bg-secondary/50 border border-border">
          {templates.length} {templates.length === 1 ? tr("adminList.assessmentSingular") : tr("adminList.assessmentPlural")} · {publishedCount} {tr("adminList.publishedLower")}
        </span>
        <button
          type="button"
          onClick={togglePlatform}
          disabled={togglingPlatform}
          title={tr("adminList.platformToggleTooltip")}
          className={`ml-auto inline-flex items-center gap-2 text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors disabled:opacity-50 ${
            accepting
              ? "border-green-500/30 bg-green-500/10 text-green-700 dark:text-green-300"
              : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
          }`}
        >
          <span className={`w-2 h-2 rounded-full ${accepting ? "bg-green-500" : "bg-amber-500"}`} />
          {accepting ? tr("adminList.platformOn") : tr("adminList.platformPaused")}
          <span className="opacity-60">· {accepting ? tr("adminList.pause") : tr("adminList.resume")}</span>
        </button>
      </div>

      {error && <div className="bg-destructive/10 text-destructive rounded-lg px-4 py-3 mb-4 text-sm">{error}</div>}

      {!loading && templates.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-4">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={tr("adminList.searchPlaceholder")}
            className="w-full sm:max-w-sm border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          {trackOptions.length > 0 && (
            <select
              value={trackFilter}
              onChange={(e) => setTrackFilter(e.target.value)}
              aria-label={tr("adminList.trackFilterLabel", "Filter by track")}
              className="w-full sm:w-auto border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              <option value="">{tr("adminList.allTracks", "All tracks")}</option>
              {trackOptions.map((tk) => (
                <option key={tk} value={tk}>{tk}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {(() => {
        const q = search.trim().toLowerCase();
        const shown = templates.filter((t) => {
          const matchesSearch =
            !q || (t.title ?? "").toLowerCase().includes(q) || (t.track ?? "").toLowerCase().includes(q);
          const matchesTrack = !trackFilter || t.track === trackFilter;
          return matchesSearch && matchesTrack;
        });
        return loading ? (
        <p className="text-muted-foreground text-sm">{tr("adminList.loading")}</p>
      ) : templates.length === 0 ? (
        <div className="border-2 border-dashed border-border rounded-xl p-12 text-center">
          <p className="text-muted-foreground text-sm mb-3">{tr("adminList.emptyState")}</p>
          <Link href="/admin/assessment/new" className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium">
            {tr("adminList.createFirst")}
          </Link>
        </div>
      ) : shown.length === 0 ? (
        <p className="text-muted-foreground text-sm py-8 text-center">{tr("adminList.noMatch").replace("{query}", search || trackFilter)}</p>
      ) : (
        <div className="flex flex-col gap-4">
          {shown.map((t) => {
            const TOOL_NAMES = ["mcq", "voice", "video", "coding", "visualization", "task"] as const;
            const enabledTools = TOOL_NAMES.filter((n) => t.tool_config?.[n]?.enabled);
            const hasManualSteps = (t.steps?.length ?? 0) > 0;

            return (
              <div key={t.id} className="bg-card border border-border rounded-xl p-5 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <h2 className="font-semibold text-foreground truncate">{t.title}</h2>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${t.is_published ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300" : "bg-muted text-muted-foreground"}`}>
                      {t.is_published ? tr("adminList.statusPublished") : tr("adminList.statusDraft")}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${hasManualSteps ? "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300" : "bg-primary/10 text-primary"}`}>
                      {hasManualSteps ? tr("adminList.manualSteps").replace("{count}", String(t.steps.length)) : tr("adminList.aiPlanner")}
                    </span>
                  </div>

                  {t.description && (
                    <p className="text-sm text-muted-foreground mb-2 truncate">{t.description}</p>
                  )}

                  {/* Enabled tools */}
                  <div className="flex gap-1.5 flex-wrap">
                    {enabledTools.length > 0
                      ? enabledTools.map((tool) => (
                          <span key={tool} className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${TOOL_BADGE[tool] ?? "bg-muted text-muted-foreground"}`}>
                            {tool}
                          </span>
                        ))
                      : <span className="text-xs text-muted-foreground">{tr("adminList.noTools")}</span>
                    }
                  </div>

                  <p className="text-xs text-muted-foreground mt-1.5">
                    {enabledTools.length} {enabledTools.length === 1 ? tr("adminList.toolSingular") : tr("adminList.toolPlural")}
                    {(() => {
                      const qc = enabledTools.reduce((s, n) => {
                        const c = t.tool_config?.[n]?.count;
                        return s + (n === "task" ? 1 : (typeof c === "number" ? c : 0));
                      }, 0);
                      return qc > 0 ? ` · ${qc} ${qc === 1 ? tr("adminList.questionSingular") : tr("adminList.questionPlural")}` : ` · ${tr("adminList.autoCounts")}`;
                    })()}
                    {t.modes && t.modes.length > 1 ? ` · ${t.modes.length} ${tr("adminList.modes")}` : ""}
                  </p>

                  {t.is_published && t.public_link_token && (
                    <div className="flex items-center gap-2 mt-2 bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800 rounded-lg px-3 py-1.5">
                      <span className="text-xs font-mono text-green-800 dark:text-green-300 truncate flex-1">
                        {publicUrl(`/assess/${t.public_link_token}`)}
                      </span>
                      <button
                        onClick={() => copyLink(t.public_link_token!, t.id)}
                        className="text-xs font-medium text-green-700 dark:text-green-300 hover:text-green-900 shrink-0 transition-colors"
                      >
                        {copiedId === t.id ? tr("adminList.copied") : tr("adminList.copy")}
                      </button>
                    </div>
                  )}
                </div>

                <div className="flex gap-2 shrink-0">
                  <Link
                    href={`/admin/assessment/${t.id}`}
                    className="text-xs px-3 py-1.5 rounded-lg border border-border text-foreground hover:bg-secondary transition-colors"
                  >
                    {tr("adminList.view")}
                  </Link>
                  <button
                    onClick={() => handlePublish(t.id, t.is_published)}
                    className={`text-xs px-3 py-1.5 rounded-lg font-medium transition-colors ${
                      t.is_published
                        ? "bg-muted text-muted-foreground hover:bg-secondary"
                        : "bg-primary text-white hover:bg-primary/90"
                    }`}
                  >
                    {t.is_published ? tr("adminList.unpublish") : tr("adminList.publish")}
                  </button>
                  <button
                    onClick={() => handleDelete(t.id, t.title)}
                    className="text-xs px-3 py-1.5 rounded-lg text-destructive hover:bg-destructive/10 transition-colors"
                  >
                    {tr("adminList.delete")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      );
      })()}
    </div>
  );
}
