"use client";

import { useState } from "react";
import { useI18n } from "@/lib/i18n";
import type { IntakeQuestion } from "@/lib/sessionApi";

interface Props {
  questions: IntakeQuestion[];
  onSubmit: (answers: Record<string, unknown>) => void;
  submitting?: boolean;
  ctaLabel?: string;
}

/** Renders admin-configured pre-assessment intake questions and collects answers. */
export default function IntakeForm({ questions, onSubmit, submitting, ctaLabel }: Props) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const resolvedCtaLabel = ctaLabel ?? t("onboarding.continue");

  const set = (label: string, v: unknown) => setAnswers((p) => ({ ...p, [label]: v }));
  const toggle = (label: string, opt: string) =>
    setAnswers((p) => {
      const cur = Array.isArray(p[label]) ? (p[label] as string[]) : [];
      return { ...p, [label]: cur.includes(opt) ? cur.filter((x) => x !== opt) : [...cur, opt] };
    });

  const inputCls = "w-full border border-border rounded-lg px-3 py-2 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30";

  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(answers); }} className="flex flex-col gap-4">
      {questions.map((q, i) => (
        <div key={i} className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-foreground">{q.label}</label>

          {q.type === "short_text" && (
            <input className={inputCls} value={(answers[q.label] as string) ?? ""} onChange={(e) => set(q.label, e.target.value)} />
          )}
          {q.type === "long_text" && (
            <textarea className={`${inputCls} min-h-[80px] resize-none`} value={(answers[q.label] as string) ?? ""} onChange={(e) => set(q.label, e.target.value)} />
          )}
          {q.type === "radio" && (
            <div className="flex flex-col gap-1.5">
              {(q.options ?? []).map((o) => (
                <label key={o} className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input type="radio" name={q.label} className="accent-primary" checked={answers[q.label] === o} onChange={() => set(q.label, o)} />
                  {o}
                </label>
              ))}
            </div>
          )}
          {q.type === "checkbox" && (
            <div className="flex flex-col gap-1.5">
              {(q.options ?? []).map((o) => (
                <label key={o} className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input type="checkbox" className="accent-primary"
                    checked={Array.isArray(answers[q.label]) && (answers[q.label] as string[]).includes(o)}
                    onChange={() => toggle(q.label, o)} />
                  {o}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
      <button type="submit" disabled={submitting}
        className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-50">
        {submitting ? t("onboarding.settingThingsUp") : resolvedCtaLabel}
      </button>
    </form>
  );
}
