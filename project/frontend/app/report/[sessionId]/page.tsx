"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { supabase } from "@/lib/supabaseClient";
import { useI18n } from "@/lib/i18n";
import { Report } from "@/lib/types";
import RadarChart from "@/components/report/RadarChart";
import SkillBreakdown from "@/components/report/SkillBreakdown";
import PlacementBadge from "@/components/report/PlacementBadge";
import AgentAvatar from "@/components/agent/AgentAvatar";

const MBTI_TYPES = new Set([
  "INTJ","INTP","ENTJ","ENTP","INFJ","INFP","ENFJ","ENFP",
  "ISTJ","ISFJ","ESTJ","ESFJ","ISTP","ISFP","ESTP","ESFP",
]);

const MBTI_NAMES: Record<string, string> = {
  INTJ:"The Architect", INTP:"The Thinker", ENTJ:"The Commander", ENTP:"The Debater",
  INFJ:"The Advocate", INFP:"The Mediator", ENFJ:"The Protagonist", ENFP:"The Campaigner",
  ISTJ:"The Inspector", ISFJ:"The Defender", ESTJ:"The Executive", ESFJ:"The Consul",
  ISTP:"The Craftsman", ISFP:"The Adventurer", ESTP:"The Entrepreneur", ESFP:"The Entertainer",
};

const DIM_LABELS: Record<string, [string, string]> = {
  EI: ["Extraversion", "Introversion"],
  NS: ["Intuition", "Sensing"],
  TF: ["Thinking", "Feeling"],
  JP: ["Judging", "Perceiving"],
};

function MBTIReport({ report, name }: { report: Report; name: string }) {
  const { t } = useI18n();
  const mbtiType = report.placement;
  const typeName = t("report.mbtiName_" + mbtiType, MBTI_NAMES[mbtiType] ?? "");

  return (
    <div className="flex flex-col gap-6">
      {/* Type badge */}
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="w-24 h-24 rounded-3xl bg-primary/10 border-2 border-primary/30 flex items-center justify-center">
          <span className="text-3xl font-black text-primary tracking-tight">{mbtiType}</span>
        </div>
        <div>
          <p className="text-xl font-bold text-foreground">{typeName}</p>
          <p className="text-sm text-muted-foreground mt-0.5">{t("report.mbtiPersonalityType")}</p>
        </div>
      </div>

      {/* Dimension bars */}
      <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
        <h2 className="font-semibold text-foreground text-sm">{t("report.dimensionBreakdown")}</h2>
        {Object.entries(DIM_LABELS).map(([dim, [left, right]]) => {
          const score = report.skill_scores[dim]?.score ?? 2.5;
          const pct = Math.round((score / 5) * 100);
          const dominant = mbtiType.includes(dim[0]) ? left : right;
          return (
            <div key={dim} className="flex flex-col gap-1.5">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span className={dominant === left ? "text-primary font-medium" : ""}>{left}</span>
                <span className={dominant === right ? "text-primary font-medium" : ""}>{right}</span>
              </div>
              <div className="relative h-2 bg-secondary rounded-full overflow-hidden">
                <div
                  className="absolute left-0 top-0 h-full bg-primary rounded-full transition-all duration-700"
                  style={{ width: `${dominant === left ? pct : 100 - pct}%` }}
                />
              </div>
              <p className="text-[10px] text-muted-foreground text-center">
                {pct}% {dominant}
              </p>
            </div>
          );
        })}
      </div>

      {/* Feedback */}
      <div className="bg-accent border border-primary/20 rounded-2xl p-5">
        <h2 className="font-semibold text-foreground mb-2">{t("report.personalityOverview")}</h2>
        <p className="text-sm text-foreground/80 leading-relaxed">{report.feedback}</p>
      </div>

      {/* Recommendations */}
      {report.recommendations?.length > 0 && (
        <div className="bg-card border border-border rounded-2xl p-5">
          <h2 className="font-semibold text-foreground mb-3">{t("report.suggestedNextSteps")}</h2>
          <ul className="flex flex-col gap-2">
            {report.recommendations.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                <span className="mt-0.5 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center shrink-0 font-medium">{i + 1}</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function HRReport({ report, name }: { report: Report; name: string }) {
  const { t } = useI18n();
  const skillData = Object.entries(report.skill_scores).map(([skill, val]) => ({
    skill, score: val.score,
  }));

  return (
    <div className="flex flex-col gap-6">
      {/* HR badge */}
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="w-24 h-24 rounded-3xl bg-green-100 dark:bg-green-950 border-2 border-green-300 dark:border-green-700 flex items-center justify-center">
          <span className="text-3xl">🎙</span>
        </div>
        <div>
          <p className="text-xl font-bold text-foreground">{name ? t("report.hrAssessmentTitle").replace("{name}", name.split(" ")[0]) : t("report.hrAssessmentReport")}</p>
          <p className="text-sm text-muted-foreground mt-0.5">{t("report.softSkillsEvaluation")}</p>
        </div>
        <span className="text-xs font-semibold px-3 py-1 rounded-full bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300 border border-green-200 dark:border-green-800">
          {t("report.hrAssessed")} ✓
        </span>
      </div>

      {/* Radar chart */}
      {skillData.length > 0 && (
        <div className="bg-card border border-border rounded-2xl p-6 animate-scale-in">
          <h2 className="font-semibold text-foreground mb-4 text-center">{t("report.softSkillsRadar")}</h2>
          <RadarChart data={skillData} />
        </div>
      )}

      {/* Competency scores */}
      {skillData.length > 0 && (
        <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
          <h2 className="font-semibold text-foreground text-sm">{t("report.competencyBreakdown")}</h2>
          {skillData.map(({ skill, score }) => {
            const pct = Math.min(100, Math.round((score / 5) * 100));
            const color = pct >= 72 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-400";
            return (
              <div key={skill} className="flex flex-col gap-1">
                <div className="flex justify-between text-xs">
                  <span className="font-medium text-foreground">{skill}</span>
                  <span className="text-muted-foreground">{score.toFixed(1)} / 5</span>
                </div>
                <div className="h-1.5 bg-secondary rounded-full overflow-hidden">
                  <div className={`h-full rounded-full transition-all duration-700 ${color}`} style={{ width: `${pct}%` }} />
                </div>
                {report.skill_scores[skill]?.feedback && (
                  <p className="text-xs text-muted-foreground leading-relaxed">{report.skill_scores[skill].feedback}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Behavioral summary */}
      {report.feedback && (
        <div className="bg-accent border border-primary/20 rounded-2xl p-5">
          <h2 className="font-semibold text-foreground mb-2">{t("report.behavioralSummary")}</h2>
          <p className="text-sm text-foreground/80 leading-relaxed">{report.feedback}</p>
        </div>
      )}

      {/* What went well */}
      {report.went_well && report.went_well.length > 0 && (
        <div className="bg-green-50 dark:bg-green-950/40 border border-green-200 dark:border-green-800 rounded-2xl p-5">
          <h2 className="font-semibold text-green-800 dark:text-green-300 mb-3">{t("report.whatWentWell")}</h2>
          <ul className="flex flex-col gap-2">
            {report.went_well.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                <span className="mt-0.5 text-green-600 dark:text-green-400 shrink-0">✓</span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Areas for improvement */}
      {report.needs_improvement && report.needs_improvement.length > 0 && (
        <div className="bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-2xl p-5">
          <h2 className="font-semibold text-amber-800 dark:text-amber-300 mb-3">{t("report.areasForImprovement")}</h2>
          <ul className="flex flex-col gap-2">
            {report.needs_improvement.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                <span className="mt-0.5 text-amber-600 dark:text-amber-400 shrink-0">→</span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Coaching suggestions */}
      {report.recommendations?.length > 0 && (
        <div className="bg-card border border-border rounded-2xl p-5">
          <h2 className="font-semibold text-foreground mb-3">{t("report.coachingSuggestions")}</h2>
          <ul className="flex flex-col gap-2">
            {report.recommendations.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                <span className="mt-0.5 w-5 h-5 rounded-full bg-green-100 dark:bg-green-950 text-green-700 dark:text-green-300 text-xs flex items-center justify-center shrink-0 font-medium">{i + 1}</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function ReportPage() {
  const { t } = useI18n();
  const { sessionId } = useParams<{ sessionId: string }>();
  const [report, setReport] = useState<Report | null>(null);
  const [candidateName, setCandidateName] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const [{ data: reportData, error: reportErr }, { data: sessionData }] = await Promise.all([
          supabase.from("final_reports").select("*").eq("session_id", sessionId).single(),
          supabase.from("candidate_sessions").select("candidate_name").eq("id", sessionId).single(),
        ]);
        if (reportErr && reportErr.code !== "PGRST116") {
          setLoadError(t("report.failedToLoad"));
        } else {
          if (reportData) setReport(reportData as Report);
          if (sessionData?.candidate_name) setCandidateName(sessionData.candidate_name);
        }
      } catch {
        setLoadError(t("report.networkError"));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [sessionId]);

  if (loading) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <AgentAvatar size="lg" />
        <p className="text-muted-foreground text-sm animate-pulse">{t("report.puttingResultsTogether")}</p>
      </div>
    </div>
  );

  if (loadError || !report) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <p className="text-muted-foreground">{loadError || t("report.reportNotFound")}</p>
    </div>
  );

  const isMBTI = MBTI_TYPES.has(report.placement);
  const isHR = report.placement === "HR_ASSESSED";
  const skillData = Object.entries(report.skill_scores).map(([skill, val]) => ({
    skill, score: val.score,
  }));

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="absolute top-0 -right-48 w-96 h-96 rounded-full bg-primary/8 blur-3xl pointer-events-none" />

      <div className="relative z-10 max-w-2xl mx-auto px-4 py-12 flex flex-col gap-8">
        {/* Header */}
        <div className="flex flex-col items-center gap-4 text-center animate-fade-in">
          <AgentAvatar size="lg" />
          <div>
            <h1 className="text-3xl font-bold text-foreground">
              {candidateName ? t("report.candidateResults").replace("{name}", candidateName.split(" ")[0]) : t("report.yourAssessmentReport")}
            </h1>
            <p className="text-muted-foreground mt-1">{t("report.poweredBy")}</p>
          </div>
          {!isMBTI && !isHR && <PlacementBadge placement={report.placement} totalScore={report.total_score} maxScore={(Object.keys(report.skill_scores || {}).length || 5) * 5} />}
        </div>

        {isMBTI ? (
          <MBTIReport report={report} name={candidateName} />
        ) : isHR ? (
          <HRReport report={report} name={candidateName} />
        ) : (
          <>
            {/* Radar chart */}
            <div className="bg-card border border-border rounded-2xl p-6 animate-scale-in">
              <h2 className="font-semibold text-foreground mb-4 text-center">{t("report.skillRadar")}</h2>
              <RadarChart data={skillData} />
            </div>

            {/* Skill breakdown */}
            <div className="animate-slide-up">
              <SkillBreakdown skillScores={report.skill_scores} />
            </div>

            {/* Feedback */}
            <div className="bg-accent border border-primary/20 rounded-2xl p-5 animate-slide-up">
              <h2 className="font-semibold text-foreground mb-2">{t("report.feedback")}</h2>
              <p className="text-sm text-foreground/80 leading-relaxed">{report.feedback}</p>
            </div>

            {/* Recommendations */}
            {report.recommendations?.length > 0 && (
              <div className="bg-card border border-border rounded-2xl p-5 animate-slide-up">
                <h2 className="font-semibold text-foreground mb-3">{t("report.recommendations")}</h2>
                <ul className="flex flex-col gap-2">
                  {report.recommendations.map((r, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                      <span className="mt-0.5 w-5 h-5 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center shrink-0 font-medium">{i + 1}</span>
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {/* Integrity */}
        {report.integrity_status !== "clean" && (
          <div className="bg-warning/10 border border-warning/30 rounded-2xl p-4 text-sm text-warning animate-fade-in">
            ⚠ {t("report.integrityFlagged").replace("{status}", report.integrity_status)}
          </div>
        )}

        {report.email_sent && (
          <p className="text-center text-xs text-muted-foreground">{t("report.emailSent")}</p>
        )}
      </div>
    </div>
  );
}
