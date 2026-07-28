"use client";

import { useI18n } from "@/lib/i18n";

interface Props {
  text: string;
  completedType: string;
  scores: number[];
}

const SECTION_KEY: Record<string, string> = {
  mcq:           "tools.sectionMcq",
  voice:         "tools.sectionVoice",
  coding:        "tools.sectionCoding",
  visualization: "tools.sectionVisualization",
  task:          "tools.sectionTask",
};

export default function StackFeedbackCard({ text, completedType, scores }: Props) {
  const { t } = useI18n();
  const sectionLabel = SECTION_KEY[completedType] ? t(SECTION_KEY[completedType]) : completedType;
  const avg = scores.length
    ? scores.reduce((a, b) => a + b, 0) / scores.length
    : 0;

  const dotColor =
    avg >= 3.5 ? "bg-green-500" : avg >= 2.0 ? "bg-yellow-400" : "bg-red-400";

  const barColor = (s: number) =>
    s >= 3.5 ? "bg-green-500" : s >= 2.0 ? "bg-yellow-400" : "bg-red-400";

  return (
    <div className="bg-primary/5 border border-primary/20 rounded-2xl p-4 flex flex-col gap-3 animate-slide-up">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <span className="text-xs font-semibold text-primary uppercase tracking-wider">
            {sectionLabel} · {t("tools.done")}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${dotColor}`} />
          <span className="text-xs font-mono font-medium text-foreground">{avg.toFixed(1)}/5</span>
        </div>
      </div>

      {text && (
        <p className="text-sm text-foreground leading-relaxed">{text}</p>
      )}

      {scores.length > 1 && (
        <div className="flex items-end gap-1 h-8">
          {scores.map((s, i) => {
            const pct = Math.max(8, Math.round((s / 5) * 100));
            return (
              <div
                key={i}
                className="flex-1 bg-secondary rounded-sm overflow-hidden flex items-end"
                style={{ height: "100%" }}
              >
                <div
                  className={`w-full rounded-sm ${barColor(s)}`}
                  style={{ height: `${pct}%` }}
                  title={`${t("tools.questionAbbrev")}${i + 1}: ${s.toFixed(1)}/5`}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
