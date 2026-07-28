"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { getCurrentUser } from "@/lib/auth";
import { getMyPipelines } from "@/lib/adminApi";
import UserMenu from "@/components/auth/UserMenu";
import type { UserProfile, PipelineStatus } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

interface PastSession {
  id: string;
  status: string;
  integrity_status: string;
  started_at: string | null;
  completed_at: string | null;
  assessment_templates: { title: string; assessment_type?: string; time_limit_minutes?: number } | null;
  final_reports: { total_score: number; placement: string }[] | null;
}

const STATUS_LABEL: Record<string, string> = {
  in_progress: "dashboard.statusInProgress",
  completed:   "dashboard.statusCompleted",
  identity:    "dashboard.statusNotStarted",
};

const PLACEMENT_STYLE: Record<string, string> = {
  PRO:      "text-primary font-bold",
  BEGINNER: "text-muted-foreground font-medium",
};

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export default function DashboardPage() {
  const router = useRouter();
  const { t } = useI18n();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<PastSession[]>([]);
  const [pipelines, setPipelines] = useState<PipelineStatus[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser().then(async (u) => {
      if (!u) { router.push("/"); return; }
      if (u.role === "admin") { router.push("/admin"); return; }
      setUser(u);
      // Pipeline enrollments (non-critical; section hides itself when empty)
      getMyPipelines(u.email).then(setPipelines).catch(() => {});
      try {
        const res = await fetch(`${BASE}/session/history?email=${encodeURIComponent(u.email)}`);
        if (res.ok) setSessions(await res.json());
      } catch {
        // non-critical
      } finally {
        setLoading(false);
      }
    });
  }, [router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  const report = (s: PastSession) =>
    Array.isArray(s.final_reports) ? s.final_reports[0] : null;

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="absolute top-0 -left-32 w-96 h-96 rounded-full bg-primary/8 blur-3xl pointer-events-none" />

      {/* Nav */}
      <nav className="relative z-20 border-b border-border/60 bg-background/80 backdrop-blur-sm">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center gap-4">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary to-blue-700 flex items-center justify-center">
              <span className="text-white text-xs font-bold">م</span>
            </div>
            <span className="text-sm font-bold text-foreground">Masar</span>
          </Link>
          <span className="text-xs text-muted-foreground">/ {t("dashboard.navDashboard")}</span>
          {user && <div className="ml-auto"><UserMenu user={user} /></div>}
        </div>
      </nav>

      <main className="relative z-10 max-w-2xl mx-auto px-6 py-12 flex flex-col gap-8">

        {/* Welcome */}
        <div className="flex items-center gap-4">
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="w-12 h-12 rounded-full" />
          ) : (
            <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-lg">
              {((user?.full_name || user?.email || "?").trim()[0] || "?").toUpperCase()}
            </div>
          )}
          <div>
            <p className="text-xl font-bold text-foreground">
              {t("dashboard.welcome")} {user?.full_name?.split(" ")[0] ?? t("dashboard.candidate")}
            </p>
            <p className="text-sm text-muted-foreground">{user?.email}</p>
          </div>
        </div>

        {/* How to start */}
        <div className="bg-primary/5 border border-primary/20 rounded-2xl p-6 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center">
              <svg className="w-4 h-4 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-foreground">{t("dashboard.readyToTakeAssessment")}</p>
          </div>
          <p className="text-sm text-muted-foreground leading-relaxed">
            {t("dashboard.assessmentLinkHelp")}
          </p>
          <p className="text-xs text-muted-foreground bg-background/60 rounded-lg px-3 py-2 font-mono border border-border/60">
            masar.ai/assess/<span className="text-primary">{t("dashboard.yourUniqueToken")}</span>
          </p>
        </div>

        {/* Your assessment pipeline */}
        {pipelines.length > 0 && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-semibold text-foreground">
              {t("pipeline.dashTitle", "Your assessment pipeline")}
            </p>
            {pipelines.map((p) => {
              const nextIdx = p.stages.findIndex((s) => !s.done);
              return (
                <div key={p.pipeline_id} className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
                  <p className="text-sm font-semibold text-foreground">{p.name}</p>
                  <ol className="flex flex-col gap-2">
                    {p.stages.map((s, i) => {
                      const isNext = i === nextIdx;
                      return (
                        <li key={`${s.template_id}-${i}`} className="flex items-center gap-3">
                          <span
                            className={`w-6 h-6 shrink-0 rounded-full flex items-center justify-center text-xs font-semibold ${
                              s.done
                                ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300"
                                : isNext
                                ? "bg-primary/15 text-primary"
                                : "bg-secondary text-muted-foreground"
                            }`}
                          >
                            {s.done ? "✓" : i + 1}
                          </span>
                          <span
                            className={`flex-1 text-sm truncate ${
                              s.done ? "text-muted-foreground line-through" : "text-foreground"
                            }`}
                          >
                            {s.title}
                          </span>
                          {s.done ? (
                            <span className="text-xs font-medium text-green-700 dark:text-green-300 shrink-0">
                              {t("pipeline.done", "Done")}
                            </span>
                          ) : isNext && s.token ? (
                            <Link
                              href={`/assess/${s.token}`}
                              className="text-xs font-medium text-primary hover:underline shrink-0"
                            >
                              {t("pipeline.start", "Start")} →
                            </Link>
                          ) : isNext ? (
                            <span className="text-xs font-medium text-primary shrink-0">
                              {t("pipeline.next", "Next")}
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              );
            })}
          </div>
        )}

        {/* Past sessions */}
        <div className="flex flex-col gap-3">
          <p className="text-sm font-semibold text-foreground">
            {t("dashboard.pastAssessments")}
            {sessions.length > 0 && (
              <span className="ml-2 text-xs font-normal text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
                {sessions.length}
              </span>
            )}
          </p>

          {sessions.length === 0 ? (
            <div className="bg-card border border-border rounded-2xl p-8 text-center flex flex-col gap-2">
              <p className="text-sm text-muted-foreground">{t("dashboard.noAssessmentsYet")}</p>
              <p className="text-xs text-muted-foreground">{t("dashboard.completedAssessmentsAppearHere")}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {sessions.map((s) => {
                const r = report(s);
                return (
                  <div
                    key={s.id}
                    className="bg-card border border-border rounded-2xl px-5 py-4 flex items-center gap-4"
                  >
                    {/* Status dot */}
                    <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
                      s.status === "completed" ? "bg-green-500" :
                      s.status === "in_progress" ? "bg-yellow-500 animate-pulse" :
                      "bg-secondary"
                    }`} />

                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">
                        {s.assessment_templates?.title ?? t("dashboard.assessmentFallback")}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {STATUS_LABEL[s.status] ? t(STATUS_LABEL[s.status]) : s.status} · {fmt(s.started_at)}
                      </p>
                    </div>

                    {/* Score + placement */}
                    {r && (
                      <div className="text-right flex-shrink-0">
                        <p className={`text-sm ${PLACEMENT_STYLE[r.placement] ?? ""}`}>
                          {r.placement}
                        </p>
                        <p className="text-xs text-muted-foreground">{r.total_score.toFixed(1)} / 25</p>
                      </div>
                    )}

                    {/* Resume link for in-progress */}
                    {s.status === "in_progress" && (
                      <Link
                        href={`/assess/direct/${s.id}?mins=${s.assessment_templates?.time_limit_minutes ?? 60}&mode=${s.assessment_templates?.assessment_type ?? "track"}`}
                        className="text-xs font-medium text-yellow-600 hover:text-yellow-700 dark:text-yellow-400 dark:hover:text-yellow-300 hover:underline flex-shrink-0"
                      >
                        {t("dashboard.resume")} →
                      </Link>
                    )}

                    {/* Report link */}
                    {s.status === "completed" && (
                      <Link
                        href={s.assessment_templates?.assessment_type === "discover" ? `/discover/results/${s.id}` : `/report/${s.id}`}
                        className="text-xs font-medium text-primary hover:underline flex-shrink-0"
                      >
                        {t("dashboard.report")} →
                      </Link>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

      </main>
    </div>
  );
}
