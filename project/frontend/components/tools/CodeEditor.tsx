"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useI18n } from "@/lib/i18n";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

interface TestCase {
  id: number;
  description: string;
  expected_behavior: string;
}

interface TestResult {
  id: number;
  likely_passes: boolean;
  reason: string;
}

interface LLMEval {
  correctness: number;
  code_quality: number;
  edge_case_handling: number;
  overall_score: number;
  feedback: string;
  test_results: TestResult[];
}

interface Props {
  payload: Record<string, unknown>;
  onSubmit: (r: Record<string, unknown>) => void;
}

function ScoreBadge({ label, score }: { label: string; score: number }) {
  const color =
    score >= 4 ? "bg-green-500/10 text-green-600 border-green-200 dark:border-green-800" :
    score >= 2.5 ? "bg-yellow-500/10 text-yellow-600 border-yellow-200 dark:border-yellow-800" :
    "bg-red-500/10 text-red-600 border-red-200 dark:border-red-800";
  return (
    <div className={`flex flex-col items-center px-3 py-2 rounded-lg border text-xs ${color}`}>
      <span className="font-bold text-base">{score.toFixed(1)}</span>
      <span className="text-center leading-tight mt-0.5">{label}</span>
    </div>
  );
}

export default function CodeEditor({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const language = (payload.language as string) || "python";
  const starterCode = (payload.starter_code as string) || (language === "python" ? "# Write your solution here\n" : "// Write your solution here\n");
  const MAX_EVALS = 3;
  const [code, setCode] = useState(starterCode);
  const [evaluating, setEvaluating] = useState(false);
  const [evalResult, setEvalResult] = useState<LLMEval | null>(null);
  const [evalError, setEvalError] = useState("");
  const [evalCount, setEvalCount] = useState(0);

  const testCases: TestCase[] = (payload.test_cases as TestCase[]) || [];
  const challengeId = payload.challenge_id as string | undefined;

  const handleEvaluate = async () => {
    if (!challengeId) { setEvalError(t("tools.challengeNotLinked")); return; }
    if (evalCount >= MAX_EVALS) return;
    setEvaluating(true);
    setEvalError("");
    setEvalResult(null);
    try {
      const res = await fetch(`${BASE}/chat/run-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, challenge_id: challengeId }),
      });
      if (!res.ok) throw new Error(await res.text());
      setEvalResult(await res.json());
      setEvalCount((c) => c + 1);
    } catch (e) {
      setEvalError(e instanceof Error ? e.message : t("tools.evaluationFailed"));
    } finally {
      setEvaluating(false);
    }
  };

  const handleSubmit = () => {
    onSubmit({ code, language, challenge_id: challengeId });
  };

  return (
    <div className="animate-slide-up bg-card border border-border rounded-2xl overflow-hidden flex flex-col">

      {/* Language badge */}
      <div className="px-4 pt-3 pb-0 flex items-center gap-2">
        <span className="text-xs px-2 py-0.5 rounded bg-orange-500/10 text-orange-600 dark:text-orange-400 font-medium capitalize border border-orange-200 dark:border-orange-800">
          {language}
        </span>
        <span className="text-xs text-muted-foreground">{t("tools.codingChallenge")}</span>
      </div>

      {/* Test cases list */}
      {testCases.length > 0 && (
        <div className="px-4 pt-4 pb-3 border-b border-border flex flex-col gap-2">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            {t("tools.testCases")} ({testCases.length})
          </p>
          <div className="flex flex-col gap-1.5">
            {testCases.map((tc) => (
              <div key={tc.id} className="flex items-start gap-2 text-xs text-muted-foreground">
                <span className="w-5 h-5 rounded-full bg-secondary flex items-center justify-center text-[10px] font-semibold shrink-0 mt-0.5">
                  {tc.id}
                </span>
                <span>
                  <span className="font-medium text-foreground">{tc.description}</span>
                  {tc.expected_behavior && (
                    <span className="ml-1 text-muted-foreground">— {tc.expected_behavior}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Monaco editor */}
      <div className="h-64">
        <MonacoEditor
          height="100%"
          language={language}
          theme="vs-dark"
          value={code}
          onChange={(v) => setCode(v ?? "")}
          options={{ fontSize: 13, minimap: { enabled: false }, scrollBeyondLastLine: false, lineNumbers: "on" }}
        />
      </div>

      {/* Actions + results */}
      <div className="p-4 border-t border-border flex flex-col gap-4">

        <div className="flex items-center gap-3">
          <button
            onClick={handleEvaluate}
            disabled={evaluating || evalCount >= MAX_EVALS}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-secondary text-foreground text-sm font-medium hover:bg-secondary/80 transition-colors disabled:opacity-50"
          >
            {evaluating ? (
              <>
                <div className="w-4 h-4 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                {t("tools.evaluatingWithAI")}
              </>
            ) : evalCount >= MAX_EVALS ? (
              t("tools.noMoreEvaluations")
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                {t("tools.testMyCode")}
              </>
            )}
          </button>
          <span className={`text-xs font-medium shrink-0 ${evalCount >= MAX_EVALS ? "text-destructive" : "text-muted-foreground"}`}>
            {MAX_EVALS - evalCount}/{MAX_EVALS} {t("tools.left")}
          </span>
        </div>

        {evalError && (
          <p className="text-xs text-destructive">{evalError}</p>
        )}

        {evalResult && (
          <div className="flex flex-col gap-3 p-3 bg-secondary/40 rounded-xl border border-border">
            {/* Score row */}
            <div className="flex gap-2 justify-between">
              <ScoreBadge label={t("tools.correctness")} score={evalResult.correctness} />
              <ScoreBadge label={t("tools.codeQuality")} score={evalResult.code_quality} />
              <ScoreBadge label={t("tools.edgeCases")} score={evalResult.edge_case_handling} />
              <ScoreBadge label={t("tools.overall")} score={evalResult.overall_score} />
            </div>

            {/* Per-test results */}
            {evalResult.test_results?.length > 0 && (
              <div className="flex flex-col gap-1">
                {evalResult.test_results.map((tr) => (
                  <div key={tr.id} className={`flex items-start gap-2 text-xs px-2.5 py-1.5 rounded-lg ${
                    tr.likely_passes ? "bg-green-500/10 text-green-700 dark:text-green-300" : "bg-red-500/10 text-red-700 dark:text-red-300"
                  }`}>
                    <span className="shrink-0 mt-0.5">{tr.likely_passes ? "✓" : "✗"}</span>
                    <span>{t("tools.test")} {tr.id}: {tr.reason}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Feedback */}
            {evalResult.feedback && (
              <p className="text-xs text-muted-foreground border-t border-border pt-2 leading-relaxed">
                {evalResult.feedback}
              </p>
            )}
          </div>
        )}

        <button
          onClick={handleSubmit}
          className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          {t("tools.doneSubmitSolution")}
        </button>
      </div>
    </div>
  );
}
