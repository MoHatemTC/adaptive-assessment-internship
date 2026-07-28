"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { supabase } from "@/lib/supabaseClient";
import { useI18n } from "@/lib/i18n";
import { getCurrentUser } from "@/lib/auth";
import { getTrackBySlug, TRACKS } from "@/lib/journeyData";
import type { DiscoveryResult } from "@/lib/sessionApi";
import { Code, Server, Brain, BarChart3, Briefcase, Palette, Megaphone, Smartphone, ShieldCheck, Compass } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  Code, Server, Brain, BarChart3, Briefcase, Palette, Megaphone, Smartphone, ShieldCheck,
};

const RIASEC_LABELS: Record<string, string> = {
  R: "Realistic", I: "Investigative", A: "Artistic", S: "Social", E: "Enterprising", C: "Conventional",
};

export default function DiscoveryResultsPage() {
  const { t } = useI18n();
  const { sessionId } = useParams<{ sessionId: string }>();
  const router = useRouter();
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [candidateName, setCandidateName] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      const user = await getCurrentUser();
      if (!user) { router.push("/"); return; }
      setCandidateName(user.full_name?.split(" ")[0] ?? t("report.thereFallback"));

      // Poll for up to 30s — finalize runs async after last turn
      let attempts = 0;
      while (attempts < 15) {
        const { data } = await supabase
          .from("final_reports")
          .select("discovery_result, candidate_name")
          .eq("session_id", sessionId)
          .single();

        if (data?.discovery_result) {
          setResult(data.discovery_result as DiscoveryResult);
          if (data.candidate_name) setCandidateName(data.candidate_name.split(" ")[0]);
          setLoading(false);
          return;
        }
        attempts++;
        await new Promise((r) => setTimeout(r, 2000));
      }
      setError(t("report.resultsTakingLonger"));
      setLoading(false);
    }
    load();
  }, [sessionId, router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex flex-col items-center justify-center gap-4">
        <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
        <div className="relative z-10 flex flex-col items-center gap-4 text-center">
          <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary to-blue-700 agent-orb-pulse flex items-center justify-center shadow-xl">
            <Compass className="w-7 h-7 text-white animate-spin" style={{ animationDuration: "3s" }} />
          </div>
          <p className="text-base font-semibold text-foreground">{t("report.analyzingTrackFit")}</p>
          <p className="text-sm text-muted-foreground">{t("report.mappingResponses")}</p>
        </div>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="min-h-screen bg-background flex flex-col items-center justify-center gap-4 px-6">
        <p className="text-muted-foreground text-sm text-center">{error || t("report.discoveryNotFound")}</p>
        <button onClick={() => window.location.reload()} className="text-sm text-primary underline">
          {t("report.refresh")}
        </button>
      </div>
    );
  }

  const recommendedTrack = getTrackBySlug(result.recommended_track);
  const RecommendedIcon = recommendedTrack ? (ICON_MAP[recommendedTrack.icon] ?? Code) : Compass;
  const trackColor = recommendedTrack?.color ?? "#1849C6";

  const topRiasec = Object.entries(result.riasec_signals ?? {})
    .sort(([, a], [, b]) => (b as number) - (a as number))
    .slice(0, 3);

  return (
    <div className="min-h-screen bg-background relative overflow-x-hidden">
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="absolute top-0 -left-64 w-[500px] h-[500px] rounded-full opacity-10 blur-3xl pointer-events-none" style={{ backgroundColor: trackColor }} />

      {/* Nav */}
      <nav className="relative z-20 border-b border-border/60 bg-background/80 backdrop-blur-sm">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center gap-3">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary to-blue-700 flex items-center justify-center">
              <span className="text-white text-xs font-bold">م</span>
            </div>
            <span className="text-sm font-bold text-foreground">Masar</span>
          </Link>
          <span className="text-muted-foreground/50">/</span>
          <span className="text-sm text-muted-foreground">{t("report.discoveryResults")}</span>
        </div>
      </nav>

      <div className="relative z-10 max-w-4xl mx-auto px-6 py-12 flex flex-col gap-10">

        {/* Header */}
        <div className="text-center flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">{t("report.discoveryGreeting").replace("{name}", candidateName)}</p>
          <h1 className="text-3xl font-extrabold text-foreground">{t("report.careerDiscoveryResults")}</h1>
        </div>

        {/* Recommended track — hero card */}
        <div
          className="rounded-2xl p-8 flex flex-col sm:flex-row items-start sm:items-center gap-6 border"
          style={{ borderColor: `${trackColor}40`, backgroundColor: `${trackColor}08` }}
        >
          <div
            className="w-16 h-16 rounded-2xl flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: `${trackColor}25` }}
          >
            <RecommendedIcon className="w-8 h-8" style={{ color: trackColor }} />
          </div>
          <div className="flex-1 flex flex-col gap-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full text-white" style={{ backgroundColor: trackColor }}>
                {t("report.bestFit")}
              </span>
              <span className="text-xs text-muted-foreground">{result.personality_archetype}</span>
            </div>
            <h2 className="text-2xl font-bold text-foreground">{result.recommended_track_name}</h2>
            <p className="text-sm text-muted-foreground leading-relaxed mt-1">{result.summary}</p>
          </div>
        </div>

        {/* Personality signals */}
        {result.personality_signals?.length > 0 && (
          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">{t("report.personalitySignals")}</h3>
            <div className="flex flex-wrap gap-2">
              {result.personality_signals.map((s) => (
                <span
                  key={s}
                  className="px-3 py-1 rounded-full text-xs font-medium border"
                  style={{ borderColor: `${trackColor}40`, color: trackColor, backgroundColor: `${trackColor}10` }}
                >
                  {s}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* RIASEC bar */}
        {topRiasec.length > 0 && (
          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">{t("report.dominantAptitude")}</h3>
            <div className="flex flex-col gap-2">
              {topRiasec.map(([key, val]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="text-xs font-medium text-muted-foreground w-24">{t("report.riasec_" + key, RIASEC_LABELS[key] ?? key)}</span>
                  <div className="flex-1 h-2 rounded-full bg-secondary overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${val}%`, backgroundColor: trackColor }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground w-8 text-right">{val}%</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Top 3 tracks */}
        {result.top_tracks?.length > 0 && (
          <div className="flex flex-col gap-4">
            <h3 className="text-sm font-semibold text-foreground">{t("report.topTrackMatches")}</h3>
            <div className="grid gap-4">
              {result.top_tracks.map((t, i) => {
                const track = getTrackBySlug(t.slug);
                const Icon = track ? (ICON_MAP[track.icon] ?? Code) : Code;
                const color = track?.color ?? "#6b7280";
                return (
                  <div
                    key={t.slug}
                    className="flex items-start gap-4 p-5 rounded-xl border border-border bg-card"
                    style={i === 0 ? { borderLeftWidth: "3px", borderLeftColor: color } : {}}
                  >
                    <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5" style={{ backgroundColor: `${color}20` }}>
                      <Icon className="w-4 h-4" style={{ color }} />
                    </div>
                    <div className="flex-1 flex flex-col gap-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-foreground">{t.name}</span>
                        <span className="text-xs font-semibold tabular-nums" style={{ color }}>{t.fit_score.toFixed(1)}/10</span>
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed">{t.reasoning}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* CTA */}
        <div className="bg-card border border-border rounded-2xl p-6 flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="flex-1">
            <p className="text-sm font-semibold text-foreground">{t("report.readyToGoDeeper")}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {t("report.takeSmartAssessment").replace("{track}", result.recommended_track_name)}
            </p>
          </div>
          <div className="flex gap-3 flex-shrink-0 flex-wrap">
            <Link
              href={`/onboarding/tracks/${result.recommended_track}`}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-white text-sm font-semibold transition-all hover:opacity-90"
              style={{ backgroundColor: trackColor }}
            >
              {t("report.startSmartAssessment")} →
            </Link>
            <Link
              href="/onboarding/tracks"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl border border-border text-foreground text-sm font-medium hover:bg-secondary transition-colors"
            >
              {t("report.browseAllTracks")}
            </Link>
          </div>
        </div>

      </div>
    </div>
  );
}
