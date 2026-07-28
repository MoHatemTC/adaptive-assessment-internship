"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getAdminSession } from "@/lib/adminApi";
import type { AdminSessionDetail, AdminSessionQuestion } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const SKILL_LABELS: Record<string, string> = {
  thinking:   "Critical Thinking",
  soft:       "Communication",
  work:       "Technical Work",
  digital_ai: "Digital & AI",
  growth:     "Learning & Growth",
};

const SKILL_COLORS: Record<string, string> = {
  thinking:   "bg-blue-500",
  soft:       "bg-green-500",
  work:       "bg-orange-500",
  digital_ai: "bg-purple-500",
  growth:     "bg-pink-500",
};

function ScoreCircle({ score, max = 5 }: { score: number; max?: number }) {
  const pct = (score / max) * 100;
  const r = 28;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const color = score >= 3.5 ? "#22c55e" : score >= 2 ? "#f59e0b" : "#ef4444";
  return (
    <svg width="72" height="72" className="rotate-[-90deg]">
      <circle cx="36" cy="36" r={r} fill="none" stroke="hsl(var(--secondary))" strokeWidth="6" />
      <circle cx="36" cy="36" r={r} fill="none" stroke={color} strokeWidth="6"
        strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
      <text x="36" y="36" textAnchor="middle" dominantBaseline="central"
        className="rotate-90" style={{ transform: "rotate(90deg)", transformOrigin: "36px 36px" }}
        fontSize="13" fontWeight="700" fill={color}>
        {score.toFixed(1)}
      </text>
    </svg>
  );
}

function AnswerDetail({ q }: { q: AdminSessionQuestion }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ans = q.candidate_answer;
  const payload = q.payload ?? {};
  const score = ans?.score ?? 0;

  const scoreBadge = (
    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
      score >= 3.5 ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300" :
      score >= 2   ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300" :
                     "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
    }`}>{score.toFixed(1)}/5</span>
  );

  const toolBadge = (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${
      q.tool_type === "mcq"           ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300" :
      q.tool_type === "voice"         ? "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300" :
      q.tool_type === "coding"        ? "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300" :
      q.tool_type === "visualization" ? "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300" :
                                        "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300"
    }`}>{q.tool_type}</span>
  );

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      {/* Header row */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-secondary/30 transition-colors text-left"
      >
        <span className="text-xs font-bold text-muted-foreground w-6">#{q.question_number}</span>
        {toolBadge}
        <span className="flex-1 text-sm text-foreground line-clamp-1">{q.body}</span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground capitalize">{q.difficulty}</span>
          {ans ? scoreBadge : <span className="text-xs text-muted-foreground italic">{t("admin.sessions.unanswered", "unanswered")}</span>}
          <svg className={`w-4 h-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {open && (
        <div className="border-t border-border p-4 flex flex-col gap-4">
          {/* Question body */}
          <p className="text-sm text-foreground leading-relaxed bg-secondary/30 rounded-lg p-3">{q.body}</p>

          {/* MCQ */}
          {q.tool_type === "mcq" && (() => {
            const options: { id: string; text: string }[] = (payload.options as { id: string; text: string }[]) ?? [];
            const correctId  = (payload.answer_key as { correct_id?: string })?.correct_id ?? "";
            let selectedId = "";
            try {
              const parsed = typeof ans?.answer_text === "string" ? JSON.parse(ans.answer_text) : ans?.answer_text;
              selectedId = parsed?.selected_id ?? "";
            } catch { /* */ }
            return (
              <div className="flex flex-col gap-1.5">
                {options.map((opt) => {
                  const isCorrect  = opt.id === correctId;
                  const isSelected = opt.id === selectedId;
                  return (
                    <div key={opt.id} className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
                      isCorrect && isSelected ? "border-green-500 bg-green-50 dark:bg-green-950/30 text-green-800 dark:text-green-300" :
                      isCorrect              ? "border-green-300 bg-green-50/50 dark:bg-green-950/20 text-green-700 dark:text-green-400" :
                      isSelected             ? "border-red-400 bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400" :
                                               "border-border text-muted-foreground"
                    }`}>
                      <span className="font-mono font-bold text-xs uppercase w-4">{opt.id}</span>
                      <span className="flex-1">{opt.text}</span>
                      {isCorrect  && <span className="text-xs font-semibold text-green-600">✓ {t("admin.sessions.correct", "Correct")}</span>}
                      {isSelected && !isCorrect && <span className="text-xs font-semibold text-red-500">✗ {t("admin.sessions.selected", "Selected")}</span>}
                    </div>
                  );
                })}
              </div>
            );
          })()}

          {/* Voice */}
          {q.tool_type === "voice" && (() => {
            const criteria: string[] = (payload.evaluation_criteria as string[]) ?? [];
            let transcript = "";
            let turns: { question: string; answer: string }[] = [];
            let durationSeconds = 0;
            try {
              const parsed = typeof ans?.answer_text === "string" ? JSON.parse(ans.answer_text) : ans?.answer_text;
              transcript = parsed?.transcript ?? "";
              turns = parsed?.turns ?? [];
              durationSeconds = parsed?.duration_seconds ?? 0;
            } catch { transcript = ans?.answer_text ?? ""; }
            const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
            const questionPrefix = t("admin.sessions.questionPrefix", "Q");
            const noAnswerLabel = t("admin.sessions.noAnswer", "No answer");
            return (
              <div className="flex flex-col gap-3">
                {/* Duration badge */}
                {durationSeconds > 0 && (
                  <div className="flex items-center gap-2">
                    <span className="text-xs bg-secondary px-2 py-0.5 rounded-full font-mono text-muted-foreground">
                      {fmt(durationSeconds)}
                    </span>
                    {turns.length > 0 && (
                      <span className="text-xs text-muted-foreground">{turns.length} {turns.length !== 1 ? t("admin.sessions.turnsPlural", "turns") : t("admin.sessions.turnSingular", "turn")}</span>
                    )}
                  </div>
                )}
                {/* Evaluation criteria */}
                {criteria.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">{t("admin.sessions.evaluationCriteria", "Evaluation Criteria")}</p>
                    <ul className="flex flex-col gap-1">
                      {criteria.map((c, i) => <li key={i} className="text-xs text-muted-foreground flex gap-1.5"><span>•</span>{c}</li>)}
                    </ul>
                  </div>
                )}
                {/* Multi-turn breakdown */}
                {turns.length > 0 ? (
                  <div className="flex flex-col gap-2">
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.interviewTurns", "Interview Turns")}</p>
                    {turns.map((t, i) => (
                      <div key={i} className="bg-secondary/30 rounded-lg p-3 flex flex-col gap-1.5">
                        <p className="text-xs font-medium text-muted-foreground">{questionPrefix}{i + 1}: {t.question}</p>
                        <p className="text-sm text-foreground leading-relaxed italic">{t.answer || <span className="not-italic text-muted-foreground">{noAnswerLabel}</span>}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">{t("admin.sessions.candidateTranscript", "Candidate Transcript")}</p>
                    <p className="text-sm text-foreground bg-secondary/30 rounded-lg p-3 leading-relaxed italic">
                      {transcript || <span className="not-italic text-muted-foreground">{t("admin.sessions.noTranscript", "No transcript recorded")}</span>}
                    </p>
                  </div>
                )}
              </div>
            );
          })()}

          {/* Visualization */}
          {q.tool_type === "visualization" && (() => {
            let analysis = "";
            try {
              const parsed = typeof ans?.answer_text === "string" ? JSON.parse(ans.answer_text) : ans?.answer_text;
              analysis = parsed?.answer_text ?? parsed?.transcript ?? "";
            } catch { analysis = ans?.answer_text ?? ""; }
            return (
              <div className="flex flex-col gap-3">
                <div className="flex gap-3 text-xs text-muted-foreground">
                  {payload.chart_type && <span className="bg-secondary px-2 py-0.5 rounded-full capitalize">{payload.chart_type as string} {t("admin.sessions.chartSuffix", "chart")}</span>}
                  {payload.chart_title && <span className="font-medium text-foreground">{payload.chart_title as string}</span>}
                </div>
                {payload.question && (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">{t("admin.sessions.questionLabel", "Question")}</p>
                    <p className="text-sm text-foreground">{payload.question as string}</p>
                  </div>
                )}
                <div>
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">{t("admin.sessions.candidateAnalysis", "Candidate Analysis")}</p>
                  <p className="text-sm text-foreground bg-secondary/30 rounded-lg p-3 leading-relaxed italic">
                    {analysis || <span className="not-italic text-muted-foreground">{t("admin.sessions.noAnalysis", "No analysis submitted")}</span>}
                  </p>
                </div>
              </div>
            );
          })()}

          {/* Coding */}
          {q.tool_type === "coding" && (() => {
            let code = "";
            try {
              const parsed = typeof ans?.answer_text === "string" ? JSON.parse(ans.answer_text) : ans?.answer_text;
              code = parsed?.code ?? "";
            } catch { code = ""; }
            return (
              <div className="flex flex-col gap-2">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.submittedCode", "Submitted Code")}</p>
                {code ? (
                  <pre className="bg-zinc-900 text-zinc-100 text-xs rounded-xl p-4 overflow-x-auto leading-relaxed">{code}</pre>
                ) : (
                  <p className="text-sm text-muted-foreground italic">{t("admin.sessions.noCode", "No code submitted")}</p>
                )}
              </div>
            );
          })()}

          {/* Grading rationale */}
          {ans?.grading_rationale && (
            <div className="border-t border-border pt-3">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">{t("admin.sessions.gradingRationale", "Grading Rationale")}</p>
              <p className="text-sm text-muted-foreground leading-relaxed">{ans.grading_rationale}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AdminSessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [data, setData] = useState<AdminSessionDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAdminSession(sessionId).then(setData).finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) return (
    <div className="flex items-center justify-center py-20">
      <div className="w-6 h-6 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
    </div>
  );
  if (!data) return <p className="text-muted-foreground text-sm">{t("admin.sessions.sessionNotFound", "Session not found.")}</p>;

  const { session, report, questions } = data;

  const durationMin = session.started_at && session.completed_at
    ? Math.round((new Date(session.completed_at).getTime() - new Date(session.started_at).getTime()) / 60000)
    : null;

  return (
    <div className="flex flex-col gap-6 max-w-3xl">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <button onClick={() => router.push("/admin/sessions")} className="hover:text-foreground transition-colors">{t("admin.sessions.title", "Candidates")}</button>
        <span>/</span>
        <span className="text-foreground font-medium">{session.candidate_name || session.candidate_email}</span>
      </div>

      {/* Report card */}
      <div className="bg-card border border-border rounded-2xl p-6 flex flex-col gap-5">
        {/* Candidate header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-foreground">{session.candidate_name || t("admin.sessions.candidateFallback", "Candidate")}</h1>
            <p className="text-sm text-muted-foreground">{session.candidate_email}</p>
          </div>
          {report?.placement && (
            <span className={`text-sm font-bold px-3 py-1 rounded-full ${
              report.placement === "PRO"
                ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
            }`}>
              {report.placement}
            </span>
          )}
        </div>

        {/* Meta row */}
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground border-t border-border pt-4">
          <span><span className="font-medium text-foreground">{t("admin.sessions.templateLabel", "Template:")}</span> {session.template_title}</span>
          {session.template_track && session.template_track !== "—" && (
            <span><span className="font-medium text-foreground">{t("admin.sessions.trackLabel", "Track:")}</span> {session.template_track}</span>
          )}
          <span><span className="font-medium text-foreground">{t("admin.sessions.questionsLabel", "Questions:")}</span> {session.answered_count ?? questions.length}</span>
          {durationMin !== null && (
            <span><span className="font-medium text-foreground">{t("admin.sessions.durationLabel", "Duration:")}</span> {durationMin} {t("admin.sessions.minutesSuffix", "min")}</span>
          )}
          <span className={`font-medium capitalize ${
            session.integrity_status === "clean"   ? "text-green-600" :
            session.integrity_status === "warned"  ? "text-amber-600" :
                                                     "text-red-600"
          }`}>
            {session.integrity_status}
          </span>
          {session.completed_at && (
            <span>{new Date(session.completed_at).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}</span>
          )}
        </div>

        {/* Skill scores */}
        {report?.skill_scores && (
          <div className="flex flex-col gap-3">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{t("admin.sessions.skillBreakdown", "Skill Breakdown")}</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {Object.entries(report.skill_scores).map(([skill, data]) => (
                <div key={skill} className="flex items-center gap-3 bg-secondary/30 rounded-xl p-3">
                  <ScoreCircle score={data.score} />
                  <div>
                    <p className="text-xs font-semibold text-foreground">{t(`admin.sessions.skill.${skill}`, SKILL_LABELS[skill] ?? skill)}</p>
                    <div className={`h-1 w-full rounded-full mt-1 ${SKILL_COLORS[skill] ?? "bg-primary"}`}
                      style={{ opacity: 0.3 + (data.score / 5) * 0.7 }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Total score */}
        {report?.total_score != null && (
          <div className="flex items-center gap-4 bg-secondary/40 rounded-xl px-4 py-3">
            <div>
              <p className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">{t("admin.sessions.totalScore", "Total Score")}</p>
              <p className="text-2xl font-bold text-foreground">{report.total_score.toFixed(1)}<span className="text-sm font-normal text-muted-foreground">/25</span></p>
            </div>
            <div className="flex-1 h-2 bg-secondary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${report.total_score >= 18 ? "bg-green-500" : report.total_score >= 12 ? "bg-amber-500" : "bg-red-400"}`}
                style={{ width: `${(report.total_score / 25) * 100}%` }}
              />
            </div>
          </div>
        )}

        {/* Summary */}
        {report?.feedback && (
          <div className="border-t border-border pt-4">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">{t("admin.sessions.summary", "Summary")}</p>
            <p className="text-sm text-foreground leading-relaxed bg-secondary/30 rounded-xl px-4 py-3 border-l-2 border-primary">
              {report.feedback}
            </p>
          </div>
        )}

        {/* Retro sections */}
        {((report?.went_well?.length ?? 0) > 0 || (report?.needs_improvement?.length ?? 0) > 0) && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(report?.went_well?.length ?? 0) > 0 && (
              <div className="bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-xl p-4">
                <p className="text-xs font-bold text-green-700 dark:text-green-400 uppercase tracking-wider mb-2">
                  ✅ {t("admin.sessions.whatWentWell", "What Went Well")}
                </p>
                <ul className="flex flex-col gap-1.5">
                  {report!.went_well.map((item, i) => (
                    <li key={i} className="text-sm text-green-900 dark:text-green-200 leading-relaxed flex gap-2">
                      <span className="text-green-500 mt-0.5 shrink-0">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(report?.needs_improvement?.length ?? 0) > 0 && (
              <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded-xl p-4">
                <p className="text-xs font-bold text-red-700 dark:text-red-400 uppercase tracking-wider mb-2">
                  ⚠️ {t("admin.sessions.needsImprovement", "Needs Improvement")}
                </p>
                <ul className="flex flex-col gap-1.5">
                  {report!.needs_improvement.map((item, i) => (
                    <li key={i} className="text-sm text-red-900 dark:text-red-200 leading-relaxed flex gap-2">
                      <span className="text-red-500 mt-0.5 shrink-0">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Recommendations */}
        {report?.recommendations && report.recommendations.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
              {t("admin.sessions.recommendedNextSteps", "Recommended Next Steps")}
            </p>
            <ul className="flex flex-col gap-2">
              {report.recommendations.map((r, i) => {
                const hasLink = r.includes(" → http");
                if (hasLink) {
                  const [actionPart, url] = r.split(" → http");
                  const fullUrl = "http" + url.trim();
                  const hasCourse = actionPart.includes(" — ");
                  const [action, course] = hasCourse ? actionPart.split(" — ") : [actionPart, ""];
                  return (
                    <li key={i} className="flex gap-2 text-sm bg-secondary/30 rounded-xl px-3 py-2.5">
                      <span className="text-blue-500 font-bold mt-0.5 shrink-0">→</span>
                      <span className="flex flex-col gap-0.5">
                        <span className="text-foreground">{action}</span>
                        {course && (
                          <a href={fullUrl} target="_blank" rel="noopener noreferrer"
                            className="text-xs text-blue-600 dark:text-blue-400 font-medium hover:underline">
                            📚 {course}
                          </a>
                        )}
                      </span>
                    </li>
                  );
                }
                return (
                  <li key={i} className="flex gap-2 text-sm text-foreground bg-secondary/30 rounded-xl px-3 py-2.5">
                    <span className="text-primary font-bold mt-0.5">→</span>
                    <span>{r}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>

      {/* Questions & Answers */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">{t("admin.sessions.questionsAndAnswers", "Questions & Answers")}</h2>
          <span className="text-xs text-muted-foreground">{questions.length} {questions.length !== 1 ? t("admin.sessions.questionsPlural", "questions") : t("admin.sessions.questionSingular", "question")}</span>
        </div>

        {questions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("admin.sessions.noQuestionsRecorded", "No questions recorded for this session.")}</p>
        ) : (
          questions.map((q) => <AnswerDetail key={q.id} q={q} />)
        )}
      </div>
    </div>
  );
}
