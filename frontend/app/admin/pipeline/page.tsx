"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getPipeline } from "@/lib/adminApi";
import type { PipelineEntry } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  started:   "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  opened:    "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  pending:   "bg-secondary text-muted-foreground",
  expired:   "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

const TYPE_BADGES: Record<string, { label: string; color: string }> = {
  track:       { label: "Technical", color: "bg-primary/10 text-primary" },
  hr:          { label: "HR",        color: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300" },
  personality: { label: "MBTI",      color: "bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300" },
  discover:    { label: "Discovery", color: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
};

function ScoreBar({ score, max }: { score: number; max?: number }) {
  const out = max ?? 25;
  const pct = Math.min(100, (score / out) * 100);
  const color = pct >= 72 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-muted-foreground">{score.toFixed(1)}</span>
    </div>
  );
}

// Group entries by candidate email
function groupByCandidate(entries: PipelineEntry[]): Map<string, PipelineEntry[]> {
  const map = new Map<string, PipelineEntry[]>();
  for (const e of entries) {
    const key = e.candidate_email;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(e);
  }
  return map;
}

export default function PipelinePage() {
  const { t } = useI18n();
  const router = useRouter();
  const [entries, setEntries] = useState<PipelineEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  useEffect(() => {
    getPipeline()
      .then((data) => { setEntries(data); setLoading(false); })
      .catch((err) => { setLoadError(err.message ?? t("admin.coding.pipeline.loadError", "Failed to load pipeline")); setLoading(false); });
  }, []);

  const filtered = entries.filter((e) => {
    const q = search.toLowerCase();
    const matchSearch = !q || (e.candidate_email || "").toLowerCase().includes(q) ||
      (e.candidate_name || "").toLowerCase().includes(q);
    const matchStatus = !statusFilter || e.status === statusFilter;
    return matchSearch && matchStatus;
  });

  const grouped = groupByCandidate(filtered);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t("admin.coding.pipeline.title", "Hiring Pipeline")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("admin.coding.pipeline.subtitle", "All invited candidates and their assessment progress")}</p>
      </div>

      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder={t("admin.coding.pipeline.searchPlaceholder", "Search candidate name or email…")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 w-64"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
        >
          <option value="">{t("admin.coding.pipeline.status.all", "All statuses")}</option>
          <option value="completed">{t("admin.coding.pipeline.status.completed", "Completed")}</option>
          <option value="started">{t("admin.coding.pipeline.status.inProgress", "In progress")}</option>
          <option value="opened">{t("admin.coding.pipeline.status.opened", "Opened")}</option>
          <option value="pending">{t("admin.coding.pipeline.status.pending", "Pending")}</option>
          <option value="expired">{t("admin.coding.pipeline.status.expired", "Expired")}</option>
        </select>
        <span className="text-sm text-muted-foreground ml-auto">{grouped.size} {grouped.size !== 1 ? t("admin.coding.pipeline.candidates", "candidates") : t("admin.coding.pipeline.candidate", "candidate")}</span>
      </div>

      {loadError && (
        <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-xl px-4 py-3">
          {loadError}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : grouped.size === 0 ? (
        <div className="text-center py-20 text-muted-foreground text-sm">
          {t("admin.coding.pipeline.noCandidates", "No candidates found.")}{" "}
          <button onClick={() => router.push("/admin")} className="text-primary underline">{t("admin.coding.pipeline.goToTemplates", "Go to templates to invite candidates.")}</button>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-secondary/40">
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.coding.pipeline.colCandidate", "Candidate")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.coding.pipeline.colAssessment", "Assessment")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.coding.pipeline.colScore", "Score")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.coding.pipeline.colPlacement", "Placement")}</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.coding.pipeline.colInviteStatus", "Invite status")}</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {Array.from(grouped.entries()).map(([email, rows]) =>
                rows.map((row, idx) => {
                  const report = Array.isArray(row.candidate_sessions?.final_reports)
                ? row.candidate_sessions!.final_reports![0]
                : row.candidate_sessions?.final_reports ?? null;
                  const tpl = row.assessment_templates;
                  const typeInfo = TYPE_BADGES[tpl?.assessment_type ?? "track"] ?? TYPE_BADGES.track;
                  const isFirstForCandidate = idx === 0;

                  return (
                    <tr
                      key={row.id}
                      className={`hover:bg-secondary/30 transition-colors ${row.session_id ? "cursor-pointer" : ""}`}
                      onClick={() => row.session_id && router.push(`/admin/sessions/${row.session_id}`)}
                    >
                      <td className={`px-4 py-3 ${isFirstForCandidate ? "" : "opacity-0 pointer-events-none"}`}>
                        {isFirstForCandidate && (
                          <>
                            <p className="font-medium text-foreground">{row.candidate_name || email.split("@")[0]}</p>
                            <p className="text-xs text-muted-foreground">{email}</p>
                          </>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full mr-2 ${typeInfo.color}`}>{t(`admin.coding.pipeline.type.${tpl?.assessment_type ?? "track"}`, typeInfo.label)}</span>
                        <span className="text-xs text-foreground">{tpl?.title || "—"}</span>
                      </td>
                      <td className="px-4 py-3 min-w-[100px]">
                        {report?.total_score != null
                          ? <ScoreBar score={report.total_score} />
                          : <span className="text-xs text-muted-foreground">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        {report?.placement ? (
                          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                            report.placement === "PRO" || report.placement === "HR_ASSESSED"
                              ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                              : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                          }`}>
                            {report.placement === "HR_ASSESSED" ? t("admin.coding.pipeline.hrAssessed", "HR ✓") : report.placement}
                          </span>
                        ) : <span className="text-xs text-muted-foreground">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${STATUS_COLORS[row.status] ?? "bg-secondary text-muted-foreground"}`}>
                          {row.status}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {row.session_id && <span className="text-xs text-primary font-medium">{t("admin.coding.pipeline.view", "View")} →</span>}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
