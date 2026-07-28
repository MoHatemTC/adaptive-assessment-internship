"use client";

import { Placement } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface Props { placement: Placement; totalScore: number; maxScore?: number; }

export default function PlacementBadge({ placement, totalScore, maxScore = 25 }: Props) {
  const { t } = useI18n();
  const isPro = placement === "PRO";
  return (
    <div className={`inline-flex flex-col items-center gap-1 px-8 py-3 rounded-2xl border-2 ${isPro ? "border-success/40 bg-success/10" : "border-warning/40 bg-warning/10"}`}>
      <span className={`text-2xl font-bold ${isPro ? "text-success" : "text-warning"}`}>{placement}</span>
      <span className="text-xs text-muted-foreground">{totalScore.toFixed(1)} / {maxScore} {t("report.placement.total", "total")}</span>
    </div>
  );
}
