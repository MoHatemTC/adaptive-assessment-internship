"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listCodingChallenges } from "@/lib/adminApi";
import type { CodingChallenge } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const DIFF_COLOR: Record<string, string> = {
  easy:   "bg-green-500/10 text-green-700 dark:text-green-300",
  medium: "bg-yellow-500/10 text-yellow-700 dark:text-yellow-300",
  hard:   "bg-red-500/10 text-red-700 dark:text-red-300",
};

function ScoreDot({ score }: { score: number | null }) {
  if (score === null) return <span className="text-xs text-muted-foreground">—</span>;
  const color = score >= 4 ? "text-green-600" : score >= 2.5 ? "text-yellow-600" : "text-red-600";
  return <span className={`text-sm font-bold ${color}`}>{score.toFixed(1)}</span>;
}

function fmt(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export default function CodingAdminPage() {
  const { t } = useI18n();
  const [challenges, setChallenges] = useState<CodingChallenge[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listCodingChallenges().then(setChallenges).finally(() => setLoading(false));
  }, []);

  const submitted = challenges.filter((c) => c.submitted_at).length;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{t("admin.coding.list.title", "Coding Challenges")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {challenges.length} {t("admin.coding.list.generated", "generated")} · {submitted} {t("admin.coding.list.submittedCount", "submitted")}
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : challenges.length === 0 ? (
        <div className="bg-card border border-border rounded-2xl p-12 text-center text-muted-foreground text-sm">
          {t("admin.coding.list.empty", "No coding challenges generated yet.")}
        </div>
      ) : (
        <div className="bg-card border border-border rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-secondary/30">
              <tr>
                {[t("admin.coding.list.colCandidate", "Candidate"), t("admin.coding.list.colTopic", "Topic"), t("admin.coding.list.colDifficulty", "Difficulty"), t("admin.coding.list.colScore", "Score"), t("admin.coding.list.colSubmitted", "Submitted"), ""].map((h) => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {challenges.map((c) => (
                <tr key={c.id} className="hover:bg-secondary/20 transition-colors">
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{c.candidate_sessions?.candidate_name ?? "—"}</p>
                    <p className="text-xs text-muted-foreground">{c.candidate_sessions?.candidate_email ?? ""}</p>
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-foreground">{c.topic}</p>
                    <p className="text-xs text-muted-foreground capitalize">{c.skill_target}</p>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-1 rounded-full font-medium capitalize ${DIFF_COLOR[c.difficulty] ?? ""}`}>
                      {c.difficulty}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <ScoreDot score={c.overall_score} />
                    {c.overall_score !== null && (
                      <span className="text-xs text-muted-foreground ml-1">/ 5</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{fmt(c.submitted_at)}</td>
                  <td className="px-4 py-3">
                    <Link href={`/admin/coding/${c.id}`} className="text-xs font-medium text-primary hover:underline">
                      {t("admin.coding.list.review", "Review")} →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
