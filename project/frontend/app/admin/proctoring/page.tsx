"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listTemplates, getAnalytics } from "@/lib/adminApi";
import type { Template } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

// ── Local typed shape of the /admin/analytics response ─────────────────────
interface AssessmentAnalytics {
  mode: "assessment";
  template_id: string;
  totals: { sessions: number; completed: number; reports: number };
  placement: { passed: number; failed: number };
  placement_breakdown?: Record<string, number>;
  avg_score: number;
  skill_averages: Record<string, number>;
  integrity: { clean: number; warned: number; flagged: number };
  violations: Record<string, number>;
  cheating_sessions: number;
}

interface LatestLogSession {
  id: string;
  candidate_name: string;
  candidate_email: string;
  integrity_status: string;
  status: string;
  started_at: string | null;
  template_id: string | null;
}

interface LatestLogs {
  mode: "latest_logs";
  sessions: LatestLogSession[];
}

type Analytics = AssessmentAnalytics | LatestLogs;

const INTEGRITY_STYLES: Record<string, string> = {
  flagged: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  warned:  "bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300",
  clean:   "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
};

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// snake_case → "Sentence case" (e.g. camera_off → "Camera off")
function humanize(key: string) {
  const s = key.replace(/_/g, " ").trim();
  return s.length ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

export default function AnalyticsPage() {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);

  // Load template list once for the assessment picker.
  useEffect(() => {
    listTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, []);

  // (Re)load analytics whenever the selected assessment changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getAnalytics(selectedId || undefined)
      .then((res) => { if (!cancelled) setData(res as unknown as Analytics); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const skillLabel = (skill: string) => t(`admin.sessions.skill.${skill}`, humanize(skill));
  const violationLabel = (key: string) => t(`analytics.vtype.${key}`, t(`admin.proctoring.vtype.${key}`, humanize(key)));

  return (
    <div className="flex flex-col gap-6">
      {/* Header + assessment picker */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-foreground">{t("analytics.title", "Analytics")}</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {t("analytics.subtitle", "Per-assessment performance, placement, and integrity insights")}
          </p>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground uppercase tracking-wider">
            {t("analytics.assessmentLabel", "Assessment")}
          </label>
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
            className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 min-w-56"
          >
            <option value="">{t("analytics.allLatestLogs", "All (latest logs)")}</option>
            {templates.map((tpl) => (
              <option key={tpl.id} value={tpl.id}>{tpl.title}</option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="w-7 h-7 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : !data ? (
        <div className="bg-card border border-border rounded-2xl p-10 text-center">
          <p className="text-muted-foreground text-sm">{t("analytics.loadError", "Could not load analytics.")}</p>
        </div>
      ) : data.mode === "assessment" ? (
        <AssessmentView
          a={data}
          t={t}
          skillLabel={skillLabel}
          violationLabel={violationLabel}
        />
      ) : (
        <LatestLogsView data={data} t={t} />
      )}
    </div>
  );
}

// ── Selected-assessment analytics ──────────────────────────────────────────
function AssessmentView({
  a, t, skillLabel, violationLabel,
}: {
  a: AssessmentAnalytics;
  t: (k: string, f?: string) => string;
  skillLabel: (s: string) => string;
  violationLabel: (k: string) => string;
}) {
  const stats: { label: string; value: string | number; accent?: string }[] = [
    { label: t("analytics.stat.sessions", "Total sessions"), value: a.totals.sessions },
    { label: t("analytics.stat.completed", "Completed"), value: a.totals.completed },
    { label: t("analytics.stat.passed", "Passed"), value: a.placement.passed, accent: "text-green-600 dark:text-green-400" },
    { label: t("analytics.stat.failed", "Failed"), value: a.placement.failed, accent: "text-red-600 dark:text-red-400" },
    { label: t("analytics.stat.avgScore", "Avg score"), value: `${a.avg_score ?? 0}/25` },
    { label: t("analytics.stat.cheating", "Cheating cases"), value: a.cheating_sessions, accent: a.cheating_sessions > 0 ? "text-red-600 dark:text-red-400" : undefined },
  ];

  const skills = Object.entries(a.skill_averages ?? {});
  const violations = Object.entries(a.violations ?? {}).sort((x, y) => y[1] - x[1]);
  const integrityRows: { key: "clean" | "warned" | "flagged"; label: string }[] = [
    { key: "clean", label: t("analytics.integrity.clean", "Clean") },
    { key: "warned", label: t("analytics.integrity.warned", "Warned") },
    { key: "flagged", label: t("analytics.integrity.flagged", "Flagged") },
  ];

  const hasSessions = a.totals.sessions > 0;

  return (
    <div className="flex flex-col gap-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        {stats.map((s) => (
          <div key={s.label} className="bg-card border border-border rounded-2xl p-5">
            <p className="text-xs text-muted-foreground uppercase tracking-wider">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${s.accent ?? "text-foreground"}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {!hasSessions && (
        <div className="bg-card border border-border rounded-2xl p-6 text-center">
          <p className="text-muted-foreground text-sm">{t("analytics.noData", "No sessions for this assessment yet.")}</p>
        </div>
      )}

      {/* Skill averages */}
      <div className="bg-card border border-border rounded-2xl p-5">
        <p className="text-xs text-muted-foreground uppercase tracking-wider mb-4">{t("analytics.skillAverages", "Skill averages")}</p>
        {skills.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("analytics.noSkillData", "No skill data yet.")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {skills.map(([skill, score]) => (
              <div key={skill}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-foreground">{skillLabel(skill)}</span>
                  <span className="text-muted-foreground tabular-nums">{Number(score).toFixed(1)}/5</span>
                </div>
                <div className="h-2 w-full rounded-full bg-secondary overflow-hidden">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.max(0, Math.min(100, (Number(score) / 5) * 100))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Placement breakdown — every placement type (meaningful for non-track assessments too) */}
      <div className="bg-card border border-border rounded-2xl p-5">
        <p className="text-xs text-muted-foreground uppercase tracking-wider mb-4">{t("analytics.placementBreakdown", "Placement breakdown")}</p>
        {Object.keys(a.placement_breakdown ?? {}).length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("analytics.noPlacementData", "No placements yet.")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            {Object.entries(a.placement_breakdown ?? {}).sort((x, y) => y[1] - x[1]).map(([label, count]) => {
              const total = Object.values(a.placement_breakdown ?? {}).reduce((s, n) => s + n, 0) || 1;
              return (
                <div key={label}>
                  <div className="flex items-center justify-between text-sm mb-1">
                    <span className="text-foreground">{label}</span>
                    <span className="text-muted-foreground tabular-nums">{count}</span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-secondary overflow-hidden">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${(count / total) * 100}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Integrity breakdown */}
        <div className="bg-card border border-border rounded-2xl p-5">
          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-4">{t("analytics.integrity", "Integrity")}</p>
          <div className="flex flex-col gap-2">
            {integrityRows.map((row) => (
              <div key={row.key} className="flex items-center justify-between">
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${INTEGRITY_STYLES[row.key]}`}>{row.label}</span>
                <span className="text-sm font-semibold text-foreground tabular-nums">{a.integrity?.[row.key] ?? 0}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Violations by type */}
        <div className="bg-card border border-border rounded-2xl p-5">
          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-4">{t("analytics.violations", "Violations by type")}</p>
          {violations.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("analytics.noViolations", "No violations recorded.")}</p>
          ) : (
            <div className="flex flex-col gap-2">
              {violations.map(([type, count]) => (
                <div key={type} className="flex items-center justify-between text-sm">
                  <span className="text-foreground">{violationLabel(type)}</span>
                  <span className="font-semibold text-foreground tabular-nums">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── "All" view: latest integrity logs across assessments ───────────────────
function LatestLogsView({ data, t }: { data: LatestLogs; t: (k: string, f?: string) => string }) {
  const sessions = data.sessions ?? [];
  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold text-foreground">{t("analytics.latestLogsTitle", "Latest integrity logs")}</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          {t("analytics.latestLogsSubtitle", "Recent flagged or warned sessions across all assessments")}
        </p>
      </div>

      {sessions.length === 0 ? (
        <div className="bg-card border border-border rounded-2xl p-10 text-center">
          <p className="text-muted-foreground text-sm">{t("analytics.emptyLogs", "No flagged or warned sessions.")}</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("analytics.colCandidate", "Candidate")}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("analytics.colStatus", "Status")}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("analytics.colIntegrity", "Integrity")}</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider">{t("analytics.colStarted", "Started")}</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sessions.map((s) => (
                <tr key={s.id} className="hover:bg-secondary/30 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{s.candidate_name}</p>
                    <p className="text-xs text-muted-foreground">{s.candidate_email}</p>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{s.status}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${INTEGRITY_STYLES[s.integrity_status] ?? ""}`}>
                      {s.integrity_status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{fmt(s.started_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <Link href={`/admin/proctoring/${s.id}`} className="text-xs font-medium text-primary hover:underline">
                      {t("analytics.review", "Review")} →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
