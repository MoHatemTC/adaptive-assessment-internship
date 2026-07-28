"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listGeneratedQuestions } from "@/lib/adminApi";
import type { GeneratedQuestion } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const TOOL_COLORS: Record<string, string> = {
  mcq:           "bg-primary/10 text-primary",
  voice:         "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  coding:        "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300",
  task:          "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  visualization: "bg-pink-100 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
};

const DIFF_COLORS: Record<string, string> = {
  easy:   "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
  medium: "bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300",
  hard:   "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

const TOOLS  = ["mcq", "voice", "coding", "task", "visualization"];
const SKILLS = ["thinking", "soft", "work", "digital_ai", "growth"];

// ── Payload renderers per tool type ──────────────────────────

function MCQDetail({ payload, answer }: { payload: Record<string, unknown>; answer?: GeneratedQuestion["candidate_answer"] }) {
  const { t } = useI18n();
  const options = (payload.options as { id: string; text: string }[]) || [];
  const correctId = (payload.answer_key as Record<string, unknown>)?.correct_id as string | undefined;
  let selected: string | undefined;
  try {
    const raw = answer?.answer_text ? JSON.parse(answer.answer_text) : null;
    selected = raw?.selected_id;
  } catch { /* ignore */ }

  return (
    <div className="flex flex-col gap-2 mt-2">
      {options.map((o) => {
        const isCorrect  = o.id === correctId;
        const isSelected = o.id === selected;
        return (
          <div key={o.id} className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg border ${
            isCorrect  ? "border-green-400 bg-green-500/10 text-green-700 dark:text-green-300" :
            isSelected ? "border-red-400 bg-red-500/10 text-red-700 dark:text-red-300" :
            "border-border text-muted-foreground"
          }`}>
            <span className="font-bold uppercase w-4 shrink-0">{o.id}</span>
            <span className="flex-1">{o.text}</span>
            {isCorrect  && <span className="shrink-0 text-green-600 font-medium">✓ {t("admin.bank.correct", "correct")}</span>}
            {isSelected && !isCorrect && <span className="shrink-0 text-red-600 font-medium">✗ {t("admin.bank.selected", "selected")}</span>}
            {isCorrect && isSelected && <span className="shrink-0 text-green-600 font-medium">✓ {t("admin.bank.correctAndSelected", "correct & selected")}</span>}
          </div>
        );
      })}
      {(payload.answer_key as Record<string, unknown>)?.explanation && (
        <p className="text-xs text-muted-foreground italic px-1">
          {String((payload.answer_key as Record<string, unknown>).explanation)}
        </p>
      )}
    </div>
  );
}

function VoiceDetail({ payload, answer }: { payload: Record<string, unknown>; answer?: GeneratedQuestion["candidate_answer"] }) {
  const { t } = useI18n();
  const criteria = (payload.evaluation_criteria as string[]) || [];
  let transcript = answer?.answer_text || "";
  try { transcript = JSON.parse(answer?.answer_text || "{}").transcript ?? transcript; } catch { /* ignore */ }

  return (
    <div className="flex flex-col gap-2 mt-2">
      {criteria.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-muted-foreground mb-1">{t("admin.bank.evaluationCriteria", "Evaluation criteria")}</p>
          <ul className="flex flex-col gap-1">
            {criteria.map((c, i) => (
              <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                <span className="text-primary mt-0.5">·</span>{c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {transcript && (
        <div>
          <p className="text-xs font-semibold text-muted-foreground mb-1">{t("admin.bank.candidateTranscript", "Candidate transcript")}</p>
          <p className="text-xs text-foreground bg-secondary/40 rounded-lg px-3 py-2 leading-relaxed">{transcript}</p>
        </div>
      )}
    </div>
  );
}

function VizDetail({ payload, answer }: { payload: Record<string, unknown>; answer?: GeneratedQuestion["candidate_answer"] }) {
  const { t } = useI18n();
  const chartData = payload.chart_data as Record<string, unknown> | undefined;
  let answerText = answer?.answer_text || "";
  try { answerText = JSON.parse(answer?.answer_text || "{}").answer_text ?? answerText; } catch { /* ignore */ }

  return (
    <div className="flex flex-col gap-2 mt-2">
      <div className="flex gap-3">
        <span className="text-xs px-2 py-1 rounded bg-secondary text-muted-foreground capitalize">
          {String(payload.chart_type || "—")} {t("admin.bank.chartWord", "chart")}
        </span>
        {payload.chart_title && (
          <span className="text-xs py-1 text-muted-foreground">{String(payload.chart_title)}</span>
        )}
      </div>
      {chartData?.labels && (
        <p className="text-xs text-muted-foreground">
          {t("admin.bank.labelsLabel", "Labels:")} {(chartData.labels as string[]).join(", ")}
        </p>
      )}
      {answerText && (
        <div>
          <p className="text-xs font-semibold text-muted-foreground mb-1">{t("admin.bank.candidateAnalysis", "Candidate analysis")}</p>
          <p className="text-xs text-foreground bg-secondary/40 rounded-lg px-3 py-2 leading-relaxed">{answerText}</p>
        </div>
      )}
    </div>
  );
}

function PayloadDetail({ q }: { q: GeneratedQuestion }) {
  const { t } = useI18n();
  const p = q.payload || {};
  const a = q.candidate_answer;

  if (q.tool_type === "mcq")           return <MCQDetail  payload={p} answer={a} />;
  if (q.tool_type === "voice")         return <VoiceDetail payload={p} answer={a} />;
  if (q.tool_type === "visualization") return <VizDetail   payload={p} answer={a} />;
  if (q.tool_type === "coding") {
    const challengeId = p.challenge_id as string | undefined;
    return (
      <div className="mt-2">
        {challengeId ? (
          <Link href={`/admin/coding/${challengeId}`} className="text-xs text-primary hover:underline font-medium">
            {t("admin.bank.viewCodingChallenge", "View coding challenge & submission →")}
          </Link>
        ) : (
          <p className="text-xs text-muted-foreground italic">{t("admin.bank.noChallengeLinked", "No challenge linked yet.")}</p>
        )}
      </div>
    );
  }
  if (q.tool_type === "task") {
    const rubric = (p.rubric as { criterion: string }[]) || [];
    return (
      <div className="flex flex-col gap-2 mt-2">
        {rubric.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-muted-foreground mb-1">{t("admin.bank.rubric", "Rubric")}</p>
            <ul className="flex flex-col gap-1">
              {rubric.map((r, i) => (
                <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                  <span className="text-primary mt-0.5">·</span>{r.criterion}
                </li>
              ))}
            </ul>
          </div>
        )}
        {a?.answer_text && (
          <p className="text-xs text-muted-foreground">{t("admin.bank.submissionRecorded", "Submission recorded")}</p>
        )}
      </div>
    );
  }
  return null;
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function GeneratedQuestionsPage() {
  const { t } = useI18n();
  const [questions, setQuestions] = useState<GeneratedQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ tool_type: "", skill_target: "" });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [openQuestion, setOpenQuestion] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setQuestions(
        await listGeneratedQuestions(
          Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) as { tool_type?: string; skill_target?: string }
        )
      );
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, [filters]);

  const grouped = questions.reduce<Record<string, GeneratedQuestion[]>>((acc, q) => {
    if (!acc[q.session_id]) acc[q.session_id] = [];
    acc[q.session_id].push(q);
    return acc;
  }, {});

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{t("admin.bank.generatedTitle", "Generated Questions")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("admin.bank.generatedSubtitle", "All questions created on-the-fly per candidate. Expand a session to review questions, options, and answers.")}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-4 py-4 mb-4 border-y border-border">
        <div className="text-sm">
          <span className="font-semibold text-foreground">{questions.length}</span>
          <span className="text-muted-foreground ml-1">{t("admin.bank.questionsWord", "questions")}</span>
        </div>
        <div className="text-sm">
          <span className="font-semibold text-foreground">{Object.keys(grouped).length}</span>
          <span className="text-muted-foreground ml-1">{t("admin.bank.sessionsWord", "sessions")}</span>
        </div>
        <div className="text-sm">
          <span className="font-semibold text-foreground">{questions.filter(q => q.candidate_answer).length}</span>
          <span className="text-muted-foreground ml-1">{t("admin.bank.answeredWord", "answered")}</span>
        </div>
      </div>

      <div className="flex gap-3 mb-6">
        <select className="border border-border rounded-lg px-3 py-1.5 text-sm bg-background focus:outline-none"
          value={filters.tool_type} onChange={(e) => setFilters({ ...filters, tool_type: e.target.value })}>
          <option value="">{t("admin.bank.allTools", "All Tools")}</option>
          {TOOLS.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="border border-border rounded-lg px-3 py-1.5 text-sm bg-background focus:outline-none"
          value={filters.skill_target} onChange={(e) => setFilters({ ...filters, skill_target: e.target.value })}>
          <option value="">{t("admin.bank.allSkills", "All Skills")}</option>
          {SKILLS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {(filters.tool_type || filters.skill_target) && (
          <button onClick={() => setFilters({ tool_type: "", skill_target: "" })}
            className="text-xs text-muted-foreground hover:text-foreground px-2">{t("admin.bank.clear", "Clear")}</button>
        )}
      </div>

      {loading ? (
        <p className="text-muted-foreground text-sm">{t("admin.bank.loading", "Loading…")}</p>
      ) : questions.length === 0 ? (
        <div className="border-2 border-dashed border-border rounded-xl p-12 text-center">
          <p className="text-muted-foreground text-sm">
            {filters.tool_type || filters.skill_target ? t("admin.bank.noQuestionsMatch", "No questions match.") : t("admin.bank.noQuestionsGenerated", "No questions generated yet.")}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {Object.entries(grouped).map(([sessionId, qs]) => {
            const candidate = qs[0].candidate_sessions;
            const isOpen = expanded === sessionId;
            return (
              <div key={sessionId} className="bg-card border border-border rounded-xl overflow-hidden">
                <button className="w-full flex items-center justify-between px-5 py-4 hover:bg-secondary/40 transition-colors text-left"
                  onClick={() => setExpanded(isOpen ? null : sessionId)}>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold text-primary">
                      {((candidate?.candidate_name || "?").trim()[0] || "?").toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground">{candidate?.candidate_name ?? t("admin.bank.unknown", "Unknown")}</p>
                      <p className="text-xs text-muted-foreground">
                        {candidate?.candidate_email ?? ""} · {qs.length} {t("admin.bank.questionsCount", `question${qs.length !== 1 ? "s" : ""}`)}
                        {" · "}{qs.filter(q => q.candidate_answer).length} {t("admin.bank.answeredWord", "answered")}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex gap-1 flex-wrap">
                      {Array.from(new Set(qs.map((q) => q.tool_type))).map((t) => (
                        <span key={t} className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${TOOL_COLORS[t] ?? "bg-muted text-muted-foreground"}`}>{t}</span>
                      ))}
                    </div>
                    <svg className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`}
                      fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                </button>

                {isOpen && (
                  <div className="border-t border-border divide-y divide-border">
                    {[...qs].sort((a, b) => a.question_number - b.question_number).map((q) => {
                      const qOpen = openQuestion === q.id;
                      const answered = !!q.candidate_answer;
                      return (
                        <div key={q.id} className="px-5 py-4">
                          <div className="flex items-start gap-4">
                            <div className="w-6 h-6 rounded-full bg-secondary flex items-center justify-center text-xs font-bold text-muted-foreground shrink-0 mt-0.5">
                              {q.question_number}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex flex-wrap gap-1.5 mb-2">
                                <span className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${TOOL_COLORS[q.tool_type] ?? ""}`}>{q.tool_type}</span>
                                <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted-foreground capitalize">{q.skill_target}</span>
                                {q.topic && <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted-foreground">{q.topic}</span>}
                                <span className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${DIFF_COLORS[q.difficulty] ?? ""}`}>{q.difficulty}</span>
                                {answered && (
                                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-700 dark:text-green-300 font-medium">
                                    {t("admin.bank.scoreLabel", "Score:")} {q.candidate_answer!.score.toFixed(1)} / 5
                                  </span>
                                )}
                              </div>
                              <p className="text-sm text-foreground leading-relaxed">{q.body}</p>

                              {/* Toggle detail */}
                              {q.payload && (
                                <button
                                  onClick={() => setOpenQuestion(qOpen ? null : q.id)}
                                  className="mt-2 text-xs text-primary hover:underline"
                                >
                                  {qOpen ? t("admin.bank.hideDetails", "Hide details ▲") : `${t("admin.bank.show", "Show")} ${q.tool_type === "mcq" ? t("admin.bank.optionsAndAnswer", "options & answer") : q.tool_type === "voice" ? t("admin.bank.criteriaAndTranscript", "criteria & transcript") : q.tool_type === "visualization" ? t("admin.bank.chartAndAnswer", "chart & answer") : t("admin.bank.details", "details")} ▼`}
                                </button>
                              )}

                              {qOpen && (
                                <div className="mt-3 border-t border-border/60 pt-3">
                                  <PayloadDetail q={q} />
                                  {answered && q.candidate_answer?.grading_rationale && (
                                    <div className="mt-3 text-xs text-muted-foreground bg-secondary/40 rounded-lg px-3 py-2">
                                      <span className="font-medium text-foreground">{t("admin.bank.gradingLabel", "Grading:")} </span>
                                      {q.candidate_answer.grading_rationale}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
