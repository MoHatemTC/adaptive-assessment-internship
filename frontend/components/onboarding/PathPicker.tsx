"use client";

import { Compass } from "lucide-react";
import { TRACKS } from "@/lib/journeyData";
import { useI18n } from "@/lib/i18n";

interface Props {
  onPick: (slug: string) => void;
  busySlug?: string | null;
}

/**
 * Single-screen path picker: one Discovery card (open career guidance) + a card
 * per specialization track (role-specific assessment). Replaces the old two-step
 * "know your track vs. coach" → catalog flow, which was redundant for learners
 * who already know the role they want.
 */
export default function PathPicker({ onPick, busySlug }: Props) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-4">
      {/* Discovery — open career guidance */}
      <button
        type="button"
        onClick={() => onPick("discover")}
        disabled={!!busySlug}
        className="flex items-start gap-4 p-5 rounded-2xl border-2 border-blue-500/30 bg-blue-500/5 hover:border-blue-500 hover:bg-blue-500/10 transition-all text-left group cursor-pointer disabled:opacity-60 disabled:cursor-wait"
      >
        <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center shrink-0 group-hover:bg-blue-500/30 transition-colors">
          <Compass className="w-5 h-5 text-blue-600" />
        </div>
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-bold text-foreground">{t("onboarding.discoverTitle")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("onboarding.discoverDesc")}
          </p>
        </div>
        <span className="ml-auto self-center text-sm font-semibold text-blue-600 whitespace-nowrap">
          {busySlug === "discover" ? t("onboarding.starting") : t("onboarding.discoverCta")}
        </span>
      </button>

      {/* Role-specific assessments */}
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mt-1">{t("onboarding.orPickRole")}</p>
      <div className="grid sm:grid-cols-2 gap-3">
        {TRACKS.map((track) => (
          <button
            key={track.slug}
            type="button"
            onClick={() => onPick(track.slug)}
            disabled={!!busySlug}
            className="flex items-center gap-3 p-4 rounded-xl border border-border bg-card hover:border-primary/50 hover:bg-secondary/40 transition-all text-left group cursor-pointer disabled:opacity-60 disabled:cursor-wait"
          >
            <span
              className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-white text-sm font-bold"
              style={{ backgroundColor: track.color }}
              aria-hidden="true"
            >
              {track.name.charAt(0)}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground truncate">{track.fullName}</p>
              <p className="text-xs text-muted-foreground">
                {busySlug === track.slug ? t("onboarding.starting") : t("onboarding.aiAdaptiveAssessment")}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
