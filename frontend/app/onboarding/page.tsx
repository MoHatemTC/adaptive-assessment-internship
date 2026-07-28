"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import { getSessionHistory } from "@/lib/sessionApi";
import { selfStartSession } from "@/lib/onboardingApi";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessage from "@/components/agent/AgentMessage";
import ProfileForm, { type ProfileData } from "@/components/onboarding/ProfileForm";
import PathPicker from "@/components/onboarding/PathPicker";
import { useI18n } from "@/lib/i18n";
import type { UserProfile, CVParseResult } from "@/lib/types";

type Step = "loading" | "profile" | "intent" | "starting";

function OnboardingInner() {
  const router = useRouter();
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const trackParam = searchParams.get("track");   // set when a landing card was clicked
  const autoPickedRef = useRef(false);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [step, setStep] = useState<Step>("loading");
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    async function init() {
      const u = await getCurrentUser();
      if (!u) { router.push("/"); return; }
      if (u.role === "admin") { router.push("/admin"); return; }
      setUser(u);

      // Returning candidates go straight to dashboard
      try {
        const history = await getSessionHistory(u.email);
        if (history.length > 0) { router.push("/dashboard"); return; }
      } catch { /* ignore — new user */ }

      // If CV already uploaded this session, skip to intent
      if (typeof window !== "undefined" && localStorage.getItem("masar_cv_upload_id")) {
        setStep("intent");
      } else {
        setStep("profile");
      }
    }
    init();
  }, [router]);

  const handleProfileComplete = (profile: ProfileData, cv: CVParseResult) => {
    localStorage.setItem("masar_cv_upload_id", cv.cv_upload_id);
    localStorage.setItem("masar_cv_summary", cv.parsed_summary);
    localStorage.setItem("masar_profile", JSON.stringify(profile));
    setStep("intent");
  };

  // Single handler for both Discovery ("discover") and any role track (slug).
  // Self-starts the default template for that path and drops the learner straight
  // into the assessment — no separate intent + catalog steps.
  const handlePick = async (slug: string) => {
    if (!user) return;
    const cvUploadId = typeof window !== "undefined" ? localStorage.getItem("masar_cv_upload_id") : null;
    if (!cvUploadId) { setStep("profile"); return; }

    setBusySlug(slug);
    setError("");
    try {
      const res = await selfStartSession({
        track_slug: slug,
        candidate_name: user.full_name ?? user.email.split("@")[0],
        candidate_email: user.email,
        cv_upload_id: cvUploadId,
      });
      const mode = res.assessment_type ?? (slug === "discover" ? "discover" : "track");
      router.push(`/assess/direct/${res.session_id}?mins=${res.time_limit_minutes}&mode=${mode}`);
    } catch (e) {
      setBusySlug(null);
      setError(
        e instanceof Error && e.message
          ? e.message
          : t("onboarding.noAssessmentSetUp"),
      );
    }
  };

  // If arriving from a landing card (?track=…), skip the picker and start that path
  // once the learner has completed profile + CV (step === "intent").
  useEffect(() => {
    if (step === "intent" && trackParam && !autoPickedRef.current) {
      autoPickedRef.current = true;
      handlePick(trackParam);
    }
  }, [step, trackParam]); // eslint-disable-line react-hooks/exhaustive-deps

  if (step === "loading" || step === "starting") {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
          {step === "starting" && <p className="text-sm text-muted-foreground">{t("onboarding.settingUpDiscovery")}</p>}
        </div>
      </div>
    );
  }

  return (
    <AgentShell step={step === "profile" ? 1 : 2} totalSteps={3} title={t("onboarding.brand")}>
      <AgentMessage
        content={
          step === "profile"
            ? `${t("onboarding.greetingHi")} ${user?.full_name?.split(" ")[0] ?? t("onboarding.greetingThere")}${t("onboarding.greetingRest")}`
            : t("onboarding.proceedPrompt")
        }
        animate
      />
      {step === "profile" && <ProfileForm onComplete={handleProfileComplete} />}
      {step === "intent" && (
        <>
          <PathPicker onPick={handlePick} busySlug={busySlug} />
          {error && <p className="text-sm text-destructive">{error}</p>}
        </>
      )}
    </AgentShell>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    }>
      <OnboardingInner />
    </Suspense>
  );
}
