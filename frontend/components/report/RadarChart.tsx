"use client";

import { Radar, RadarChart as RC, PolarGrid, PolarAngleAxis, ResponsiveContainer } from "recharts";
import { useI18n } from "@/lib/i18n";

interface Props { data: { skill: string; score: number }[]; }

const SKILL_LABELS: Record<string, string> = {
  thinking: "Thinking", soft: "Soft Skills", work: "Work Ethic", digital_ai: "Digital/AI", growth: "Growth",
};

export default function RadarChart({ data }: Props) {
  const { t } = useI18n();
  const chartData = data.map((d) => ({ subject: t(`report.skill.${d.skill}`, SKILL_LABELS[d.skill] ?? d.skill), score: d.score, fullMark: 5 }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <RC cx="50%" cy="50%" outerRadius="70%" data={chartData}>
        <PolarGrid stroke="hsl(220 13% 91%)" />
        <PolarAngleAxis dataKey="subject" tick={{ fontSize: 12, fill: "hsl(220 9% 46%)" }} />
        <Radar name={t("report.radar.scoreLabel", "Score")} dataKey="score" stroke="hsl(221 83% 53%)" fill="hsl(221 83% 53%)" fillOpacity={0.2} strokeWidth={2} />
      </RC>
    </ResponsiveContainer>
  );
}
