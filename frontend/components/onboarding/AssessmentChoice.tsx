"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Zap, BookOpen, ExternalLink, Clock, Loader2 } from "lucide-react";
import { selfStartSession } from "@/lib/onboardingApi";
import { getCurrentUser } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import type { Track } from "@/lib/journeyData";

interface Props {
  track: Track;
}

export default function AssessmentChoice({ track }: Props) {
  const router = useRouter();
  const { t } = useI18n();
  const [consent, setConsent] = useState({ camera: true, voice: true, data: false });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  const handleStart = async () => {
    if (!consent.data) {
      setError(t("onboarding.mustConsentData"));
      return;
    }
    const cvUploadId = localStorage.getItem("masar_cv_upload_id");
    if (!cvUploadId) {
      setError(t("onboarding.cvNotFound"));
      return;
    }

    setStarting(true);
    setError("");
    try {
      const user = await getCurrentUser();
      if (!user) { router.push("/"); return; }

      const res = await selfStartSession({
        track_slug: track.slug,
        candidate_name: user.full_name ?? user.email.split("@")[0],
        candidate_email: user.email,
        cv_upload_id: cvUploadId,
        consent_camera: consent.camera,
        consent_voice: consent.voice,
        consent_data: true,
      });

      router.push(`/assess/direct/${res.session_id}?mins=${res.time_limit_minutes}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("onboarding.failedToStart"));
      setStarting(false);
    }
  };

  return (
    <div className="grid md:grid-cols-2 gap-4">
      {/* Smart Assessment */}
      <div className="flex flex-col gap-4 p-6 rounded-2xl border-2 border-primary/30 bg-primary/5">
        <div className="flex items-center justify-between">
          <div className="w-10 h-10 rounded-xl bg-primary/20 flex items-center justify-center">
            <Zap className="w-5 h-5 text-primary" />
          </div>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-primary text-white uppercase tracking-wider">
            {t("onboarding.recommended")}
          </span>
        </div>

        <div className="flex flex-col gap-1">
          <h3 className="text-base font-bold text-foreground">{t("onboarding.smartAssessment")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("onboarding.smartAssessmentDescA")} {track.name}{t("onboarding.smartAssessmentDescB")}
          </p>
        </div>

        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock className="w-3.5 h-3.5" />
          ~{track.timeLimit} {t("onboarding.minutesFormats")}
        </div>

        {/* Consent */}
        <div className="flex flex-col gap-2 pt-3 border-t border-border/60">
          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">{t("onboarding.consentPrivacy")}</p>
          {[
            { key: "camera", label: t("onboarding.consentCamera") },
            { key: "voice", label: t("onboarding.consentVoice") },
            { key: "data", label: t("onboarding.consentData"), required: true },
          ].map(({ key, label }) => (
            <label key={key} className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={(consent as Record<string, boolean>)[key]}
                onChange={(e) => setConsent((c) => ({ ...c, [key]: e.target.checked }))}
              />
              <span className="text-xs text-foreground">{label}</span>
            </label>
          ))}
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <button
          onClick={handleStart}
          disabled={starting || !consent.data}
          className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {starting ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t("onboarding.starting")}
            </>
          ) : (
            t("onboarding.startAssessment")
          )}
        </button>
      </div>

      {/* Full Specialization */}
      <div className="flex flex-col gap-4 p-6 rounded-2xl border border-border bg-card">
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: `${track.color}20` }}
        >
          <BookOpen className="w-5 h-5" style={{ color: track.color }} />
        </div>

        <div className="flex flex-col gap-1">
          <h3 className="text-base font-bold text-foreground">{t("onboarding.fullSpecialization")}</h3>
          <p className="text-sm text-muted-foreground">
            {t("onboarding.fullSpecDescA")} {track.name} {t("onboarding.fullSpecDescB")}
          </p>
        </div>

        <ul className="flex flex-col gap-1.5 flex-1">
          {track.journeys.map((j) => (
            <li key={j} className="text-xs text-muted-foreground flex items-center gap-2">
              <span
                className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: track.color }}
              />
              {j}
            </li>
          ))}
        </ul>

        <a
          href={track.specializationUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="w-full py-3 rounded-xl border border-border text-foreground font-semibold text-sm hover:bg-secondary transition-colors flex items-center justify-center gap-2"
        >
          {t("onboarding.viewOnSprints")}
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
      </div>
    </div>
  );
}
