"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { listAdminSessions, listTemplates } from "@/lib/adminApi";
import type { AdminSessionSummary, Template } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  in_progress: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  flagged: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

const PLACEMENT_COLORS: Record<string, string> = {
  PRO:      "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  BEGINNER: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

const INTEGRITY_COLORS: Record<string, string> = {
  clean:   "text-green-600",
  warned:  "text-amber-600",
  flagged: "text-red-600",
};

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(100, (score / 25) * 100);
  const color = score >= 18 ? "bg-green-500" : score >= 12 ? "bg-amber-500" : "bg-red-400";
  return (
    <div className="flex items-center gap-2 min-w-[100px]">
      <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-muted-foreground w-10 text-right">{score.toFixed(1)}/25</span>
    </div>
  );
}

export default function AdminSessionsPage() {
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const templateIdParam = searchParams.get("template_id") ?? "";

  const [sessions, setSessions] = useState<AdminSessionSummary[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [templateFilter, setTemplateFilter] = useState(templateIdParam);
  const [statusFilter, setStatusFilter] = useState("completed");
  const [search, setSearch] = useState("");

  const load = async () => {
    setLoading(true);
    const [s, t] = await Promise.all([
      listAdminSessions({
        template_id: templateFilter || undefined,
        status: statusFilter || undefined,
      }),
      listTemplates(),
    ]);
    setSessions(s);
    setTemplates(t);
    setLoading(false);
  };

  useEffect(() => { load(); }, [templateFilter, statusFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = sessions.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      s.candidate_name?.toLowerCase().includes(q) ||
      s.candidate_email?.toLowerCase().includes(q) ||
      s.template_title?.toLowerCase().includes(q)
    );
  });

  const activeTemplate = templates.find((t) => t.id === templateFilter);

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t("admin.sessions.title", "Candidates")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {activeTemplate
            ? <>{t("admin.sessions.resultsFor", "Results for")} <span className="font-medium text-foreground">{activeTemplate.title}</span></>
            : t("admin.sessions.allResultsSubtitle", "All assessment results across all templates")}
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder={t("admin.sessions.searchPlaceholder", "Search name or email…")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 w-56"
        />

        <select
          value={templateFilter}
          onChange={(e) => setTemplateFilter(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          <option value="">{t("admin.sessions.allTemplates", "All templates")}</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>{t.title}</option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          <option value="">{t("admin.sessions.allStatuses", "All statuses")}</option>
          <option value="completed">{t("admin.sessions.statusCompleted", "Completed")}</option>
          <option value="in_progress">{t("admin.sessions.statusInProgress", "In progress")}</option>
          <option value="flagged">{t("admin.sessions.statusFlagged", "Flagged")}</option>
        </select>

        <span className="text-sm text-muted-foreground ml-auto">
          {filtered.length} {filtered.length !== 1 ? t("admin.sessions.candidatesPlural", "candidates") : t("admin.sessions.candidateSingular", "candidate")}
        </span>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 text-muted-foreground text-sm">
          {t("admin.sessions.noSessionsFound", "No sessions found.")}
          {statusFilter === "completed" && ` ${t("admin.sessions.tryChangingStatusFilter", "Try changing the status filter.")}`}
        </div>
      ) : (
        <div className="bg-card border border-border rounded-2xl overflow-hidden">
          <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[900px]">
            <thead>
              <tr className="border-b border-border bg-secondary/40">
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colCandidate", "Candidate")}</th>
                {!templateFilter && <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colTemplate", "Template")}</th>}
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colScore", "Score")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colPlacement", "Placement")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colStatus", "Status")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colIntegrity", "Integrity")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colAnswered", "Answered")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.colCompleted", "Completed")}</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((s) => (
                <tr key={s.id} className="hover:bg-secondary/30 transition-colors cursor-pointer" onClick={() => router.push(`/admin/sessions/${s.id}`)}>
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{s.candidate_name || "—"}</p>
                    <p className="text-xs text-muted-foreground">{s.candidate_email}</p>
                  </td>
                  {!templateFilter && (
                    <td className="px-4 py-3">
                      <p className="text-foreground">{s.template_title}</p>
                      {s.template_track && s.template_track !== "—" && (
                        <p className="text-xs text-muted-foreground">{s.template_track}</p>
                      )}
                    </td>
                  )}
                  <td className="px-4 py-3">
                    {s.report.total_score != null
                      ? <ScoreBar score={s.report.total_score} />
                      : <span className="text-muted-foreground text-xs">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    {s.report.placement ? (
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${PLACEMENT_COLORS[s.report.placement] ?? ""}`}>
                        {s.report.placement}
                      </span>
                    ) : <span className="text-muted-foreground text-xs">—</span>}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${STATUS_COLORS[s.status] ?? "bg-secondary text-muted-foreground"}`}>
                      {s.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs font-medium capitalize ${INTEGRITY_COLORS[s.integrity_status] ?? "text-muted-foreground"}`}>
                      {s.integrity_status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{s.answered_count} {t("admin.sessions.questionsAbbrev", "Q")}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {s.completed_at ? new Date(s.completed_at).toLocaleDateString() : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span className="text-xs text-primary font-medium">{t("admin.sessions.view", "View")} →</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
