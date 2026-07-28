"use client";

import { SkillScore } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface Props { skillScores: Record<string, SkillScore>; }

const SKILL_LABELS: Record<string, string> = {
  thinking: "Thinking", soft: "Soft Skills", work: "Work Ethic", digital_ai: "Digital/AI", growth: "Growth",
};

export default function SkillBreakdown({ skillScores }: Props) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-3">
      <h2 className="font-semibold text-foreground">{t("report.skillBreakdown.title", "Skill Breakdown")}</h2>
      {Object.entries(skillScores).map(([skill, val]) => (
        <div key={skill} className="bg-card border border-border rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-foreground capitalize">{t(`report.skill.${skill}`, SKILL_LABELS[skill] ?? skill)}</span>
            <span className="text-sm font-bold text-primary">{val.score.toFixed(1)}<span className="text-muted-foreground font-normal">{t("report.skillBreakdown.outOfFive", "/5")}</span></span>
          </div>
          <div className="w-full h-1.5 bg-border rounded-full overflow-hidden mb-3">
            <div className="h-full bg-primary rounded-full transition-all duration-700" style={{ width: `${(val.score / 5) * 100}%` }} />
          </div>
          {val.evidence?.length > 0 && (
            <ul className="flex flex-col gap-1">
              {val.evidence.slice(0, 2).map((e, i) => (
                <li key={i} className="text-xs text-muted-foreground pl-2 border-l-2 border-primary/30">{e}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
