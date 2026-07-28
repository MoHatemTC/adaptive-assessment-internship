"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getCodingChallenge } from "@/lib/adminApi";
import type { CodingChallengeDetail } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

function ScoreBar({ label, score, max = 5 }: { label: string; score: number; max?: number }) {
  const pct = (score / max) * 100;
  const color = score >= 4 ? "bg-green-500" : score >= 2.5 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-muted-foreground w-32 shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-secondary rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-semibold text-foreground w-8 text-right">{score.toFixed(1)}</span>
    </div>
  );
}

export default function CodingChallengeDetailPage() {
  const { t } = useI18n();
  const { id } = useParams<{ id: string }>();
  const [challenge, setChallenge] = useState<CodingChallengeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [codeTab, setCodeTab] = useState<"submission" | "solution">("submission");

  useEffect(() => {
    getCodingChallenge(id).then(setChallenge).finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }
  if (!challenge) {
    return <p className="text-muted-foreground">{t("admin.coding.detail.notFound", "Challenge not found.")}</p>;
  }

  const scores = challenge.llm_scores;

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-foreground">{challenge.topic}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {challenge.candidate_sessions?.candidate_name ?? t("admin.coding.detail.unknown", "Unknown")} ·{" "}
            {challenge.candidate_sessions?.candidate_email ?? ""} · {t("admin.coding.detail.questionPrefix", "Q")}{challenge.question_number}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <span className={`text-xs px-2.5 py-1 rounded-full font-medium capitalize ${
            challenge.difficulty === "hard" ? "bg-red-500/10 text-red-600" :
            challenge.difficulty === "medium" ? "bg-yellow-500/10 text-yellow-600" :
            "bg-green-500/10 text-green-600"
          }`}>{challenge.difficulty}</span>
          <span className="text-xs px-2.5 py-1 rounded-full bg-primary/10 text-primary font-medium capitalize">
            {challenge.skill_target}
          </span>
        </div>
      </div>

      {/* Challenge body */}
      <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-foreground">{t("admin.coding.detail.challenge", "Challenge")}</h2>
        <p className="text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed">{challenge.body}</p>
        {challenge.constraints && (
          <p className="text-xs text-muted-foreground bg-secondary/50 rounded-lg px-3 py-2">
            <span className="font-medium text-foreground">{t("admin.coding.detail.constraints", "Constraints:")}</span> {challenge.constraints}
          </p>
        )}
        {challenge.expected_approach && (
          <p className="text-xs text-muted-foreground bg-secondary/50 rounded-lg px-3 py-2">
            <span className="font-medium text-foreground">{t("admin.coding.detail.expectedApproach", "Expected approach:")}</span> {challenge.expected_approach}
          </p>
        )}
      </div>

      {/* Test cases */}
      {challenge.test_cases?.length > 0 && (
        <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-foreground">{t("admin.coding.detail.testCases", "Test Cases")} ({challenge.test_cases.length})</h2>
          <div className="flex flex-col gap-3">
            {challenge.test_cases.map((tc) => {
              const result = scores?.test_results?.find((r) => r.id === tc.id);
              return (
                <div key={tc.id} className="flex flex-col gap-1.5 p-3 rounded-xl bg-secondary/30 border border-border">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-foreground">
                      <span className="text-muted-foreground mr-2">#{tc.id}</span>
                      {tc.description}
                    </p>
                    {result && (
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ${
                        result.likely_passes ? "bg-green-500/10 text-green-600" : "bg-red-500/10 text-red-600"
                      }`}>
                        {result.likely_passes ? t("admin.coding.detail.likelyPasses", "Likely passes") : t("admin.coding.detail.likelyFails", "Likely fails")}
                      </span>
                    )}
                  </div>
                  {tc.expected_behavior && (
                    <p className="text-xs text-muted-foreground">{tc.expected_behavior}</p>
                  )}
                  {tc.assert_code && (
                    <pre className="text-xs bg-background border border-border rounded-lg px-3 py-2 overflow-x-auto font-mono text-foreground">
                      {tc.assert_code}
                    </pre>
                  )}
                  {result?.reason && (
                    <p className="text-xs text-muted-foreground italic">{result.reason}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Code + LLM scores */}
      {challenge.submitted_code ? (
        <div className="flex flex-col gap-4">
          {/* LLM scores */}
          {scores && (
            <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-foreground">{t("admin.coding.detail.llmEvaluation", "LLM Evaluation")}</h2>
                <span className={`text-lg font-bold ${
                  scores.overall_score >= 4 ? "text-green-600" :
                  scores.overall_score >= 2.5 ? "text-yellow-600" : "text-red-600"
                }`}>{scores.overall_score.toFixed(1)} / 5</span>
              </div>
              <div className="flex flex-col gap-2">
                <ScoreBar label={t("admin.coding.detail.correctness", "Correctness")}       score={scores.correctness} />
                <ScoreBar label={t("admin.coding.detail.codeQuality", "Code Quality")}      score={scores.code_quality} />
                <ScoreBar label={t("admin.coding.detail.edgeCases", "Edge Cases")}        score={scores.edge_case_handling} />
              </div>
              {scores.feedback && (
                <p className="text-xs text-muted-foreground border-t border-border pt-3 leading-relaxed">
                  {scores.feedback}
                </p>
              )}
            </div>
          )}

          {/* Code tabs */}
          <div className="bg-card border border-border rounded-2xl overflow-hidden">
            <div className="flex border-b border-border">
              {(["submission", "solution"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setCodeTab(tab)}
                  disabled={tab === "solution" && !challenge.sample_solution}
                  className={`px-5 py-3 text-sm font-medium capitalize transition-colors ${
                    codeTab === tab
                      ? "text-primary border-b-2 border-primary"
                      : "text-muted-foreground hover:text-foreground"
                  } disabled:opacity-30 disabled:cursor-not-allowed`}
                >
                  {tab === "submission" ? t("admin.coding.detail.candidateSubmission", "Candidate Submission") : t("admin.coding.detail.sampleSolution", "Sample Solution")}
                </button>
              ))}
            </div>
            <pre className="text-xs font-mono p-4 overflow-x-auto bg-[#1e1e1e] text-[#d4d4d4] min-h-[160px] whitespace-pre">
              {codeTab === "submission" ? challenge.submitted_code : (challenge.sample_solution ?? "")}
            </pre>
          </div>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-2xl p-8 text-center text-sm text-muted-foreground">
          {t("admin.coding.detail.noSubmission", "No submission yet — candidate has not submitted their solution.")}
        </div>
      )}
    </div>
  );
}
