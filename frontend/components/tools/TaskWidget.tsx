"use client";

import { useState, useEffect, useCallback } from "react";
import { submitTaskZip } from "@/lib/uploadApi";
import type { TaskPayload } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface Props {
  payload: Record<string, unknown>;
  sessionId: string;
  onSubmit: (result: Record<string, unknown>) => void;
}

export default function TaskWidget({ payload, sessionId, onSubmit }: Props) {
  const { t } = useI18n();
  const task = payload as unknown as TaskPayload;
  const timeLimitMs = (task.time_limit_minutes ?? 30) * 60 * 1000;
  const [msLeft, setMsLeft] = useState(timeLimitMs);
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    const t = setInterval(() => {
      setMsLeft((prev) => {
        if (prev <= 1000) {
          clearInterval(t);
          // Auto-submit if file present, else submit empty
          return 0;
        }
        return prev - 1000;
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    setError("");
    try {
      let result;
      if (file) {
        result = await submitTaskZip(sessionId, file);
      } else {
        result = { criteria_scores: [], total_score: 0, rationale: "No submission received (time expired or skipped)." };
      }
      setSubmitted(true);
      onSubmit({ submission_result: result, tool_type: "task" });
    } catch (e) {
      setError(e instanceof Error ? e.message : t("tools.submissionFailed"));
    } finally {
      setSubmitting(false);
    }
  }, [file, sessionId, onSubmit]);

  // Auto-submit on time up
  useEffect(() => {
    if (msLeft === 0 && !submitted) handleSubmit();
  }, [msLeft, submitted, handleSubmit]);

  const mm = String(Math.floor(msLeft / 60000)).padStart(2, "0");
  const ss = String(Math.floor((msLeft % 60000) / 1000)).padStart(2, "0");
  const urgent = msLeft < 5 * 60 * 1000;

  return (
    <div className="border border-border rounded-2xl bg-card overflow-hidden">
      {/* Timer bar */}
      <div className={`px-5 py-3 flex items-center justify-between border-b border-border ${urgent ? "bg-destructive/5" : "bg-secondary/30"}`}>
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="text-xs text-muted-foreground font-medium">{t("tools.timeRemaining")}</span>
        </div>
        <span className={`text-lg font-mono font-bold tabular-nums ${urgent ? "text-destructive" : "text-foreground"}`}>
          {mm}:{ss}
        </span>
      </div>

      <div className="p-5 flex flex-col gap-5">
        {/* Task Brief */}
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">{t("tools.taskBrief")}</p>
          <div className="bg-secondary/30 rounded-xl p-4 text-sm text-foreground leading-relaxed whitespace-pre-wrap">
            {task.brief}
          </div>
        </div>

        {/* Deliverables */}
        {task.deliverables?.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">{t("tools.requiredDeliverables")}</p>
            <ul className="space-y-1">
              {task.deliverables.map((d, i) => (
                <li key={i} className="flex items-center gap-2 text-sm text-foreground">
                  <svg className="w-4 h-4 text-primary flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  {d}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Rubric */}
        {task.rubric?.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">{t("tools.evaluationCriteria")}</p>
            <div className="space-y-1.5">
              {task.rubric.map((r, i) => (
                <div key={i} className="flex items-center gap-3 text-sm bg-secondary/20 rounded-lg px-3 py-2">
                  <span className="w-5 h-5 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center flex-shrink-0 font-medium">
                    {i + 1}
                  </span>
                  <span className="text-foreground flex-1">{r.criterion}</span>
                  <span className="text-xs text-muted-foreground">{r.weight} {r.weight !== 1 ? t("tools.points") : t("tools.point")}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ZIP Upload */}
        <div>
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">{t("tools.submitYourWork")}</p>
          <label className={`
            flex flex-col items-center gap-2 p-4 rounded-xl border-2 border-dashed cursor-pointer transition-all
            ${file ? "border-green-500/50 bg-green-500/5" : "border-border hover:border-primary/50 hover:bg-secondary/30"}
          `}>
            <input
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) setFile(f);
              }}
            />
            {file ? (
              <>
                <svg className="w-8 h-8 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                <p className="text-sm font-medium text-foreground">{file.name}</p>
                <p className="text-xs text-muted-foreground">{t("tools.clickToChange")}</p>
              </>
            ) : (
              <>
                <svg className="w-8 h-8 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                </svg>
                <p className="text-sm text-foreground font-medium">{t("tools.uploadZip")}</p>
                <p className="text-xs text-muted-foreground">{t("tools.includeDeliverables")}</p>
              </>
            )}
          </label>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <button
          onClick={handleSubmit}
          disabled={submitting || submitted}
          className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-50"
        >
          {submitting ? t("tools.submitting") : submitted ? t("tools.submittedCheck") : t("tools.submitWork")}
        </button>
      </div>
    </div>
  );
}
