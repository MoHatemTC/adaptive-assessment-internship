"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import { startSession, getAssessmentConfig, type IntakeQuestion } from "@/lib/sessionApi";
import CVUpload from "@/components/upload/CVUpload";
import IntakeForm from "@/components/onboarding/IntakeForm";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessage from "@/components/agent/AgentMessage";
import ChatInterface from "@/components/chat/ChatInterface";
import IdentityCapture from "@/components/camera/IdentityCapture";
import LoginButton from "@/components/auth/LoginButton";
import { useI18n } from "@/lib/i18n";
import type { UserProfile, CVParseResult } from "@/lib/types";

type Phase = "loading" | "login" | "cv_upload" | "consent" | "intake" | "identity" | "assessment";

export default function AssessPage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [cvResult, setCvResult] = useState<CVParseResult | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(60);
  const [assessmentType, setAssessmentType] = useState<"track" | "discover" | "personality">("track");
  const [consent, setConsent] = useState({ camera: true, voice: true, data: false });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [cvRequired, setCvRequired] = useState(true);
  const [intakeQuestions, setIntakeQuestions] = useState<IntakeQuestion[]>([]);

  useEffect(() => {
    getCurrentUser().then((u) => {
      if (!u) { setPhase("login"); return; }
      setUser(u);
      setPhase("cv_upload");
    });
    // Pre-start config: whether a CV is required + any admin intake questions
    getAssessmentConfig(token)
      .then((cfg) => { setCvRequired(cfg.cv_required); setIntakeQuestions(cfg.questions || []); })
      .catch(() => { /* fall back to CV-required, no intake */ });
  }, [token]);

  const handleCVSuccess = (result: CVParseResult) => {
    setCvResult(result);
    setPhase("consent");
  };

  // From the consent screen: validate, then either collect intake answers or start.
  const proceedFromConsent = (e: React.FormEvent) => {
    e.preventDefault();
    if (!consent.data) { setError(t("assess.consentDataError")); return; }
    if (cvRequired && !cvResult) { setError(t("assess.cvRequiredError")); return; }
    setError("");
    if (intakeQuestions.length > 0) { setPhase("intake"); return; }
    doStart({});
  };

  const doStart = async (intakeAnswers: Record<string, unknown>) => {
    setStarting(true);
    setError("");
    try {
      const res = await startSession({
        token,
        candidate_name: user!.full_name ?? user!.email.split("@")[0],
        candidate_email: user!.email,
        cv_upload_id: cvResult?.cv_upload_id ?? null,
        consent_camera: consent.camera,
        consent_voice: consent.voice,
        consent_data: true,
        intake_answers: intakeAnswers,
      });
      setSessionId(res.session_id);
      setTimeLimitMinutes(res.time_limit_minutes ?? 60);
      if (res.assessment_type === "discover" || res.assessment_type === "personality") {
        setAssessmentType(res.assessment_type);
      }
      setPhase("identity");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("assess.genericError"));
      setStarting(false);
      setPhase("consent");
    }
  };

  if (phase === "loading") {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  if (phase === "login") {
    return (
      <AgentShell step={1} totalSteps={3} title="Masar">
        <AgentMessage
          content={t("assess.loginGreeting")}
          animate
        />
        <AgentMessage
          content={t("assess.loginGuide")}
          animate={false}
        />
        <AgentMessage
          content={t("assess.signInGoogle")}
          animate={false}
        />
        <div className="flex justify-center mt-2">
          <LoginButton variant="hero" />
        </div>
      </AgentShell>
    );
  }

  if (phase === "intake") {
    return (
      <AgentShell step={2} totalSteps={3} title="Masar">
        <AgentMessage content={t("assess.intakeIntro")} animate />
        <div className="bg-card border border-border rounded-2xl p-5">
          <IntakeForm questions={intakeQuestions} onSubmit={doStart} submitting={starting} ctaLabel={t("assess.startAssessmentCta")} />
        </div>
        {error && <p className="text-sm text-destructive text-center">{error}</p>}
      </AgentShell>
    );
  }

  if (phase === "identity" && sessionId && user) {
    return (
      <IdentityCapture
        sessionId={sessionId}
        userId={user.id}
        onConfirmed={() => setPhase("assessment")}
      />
    );
  }

  if (phase === "assessment" && sessionId && user) {
    return (
      <ChatInterface
        sessionId={sessionId}
        userId={user.id}
        candidateName={user.full_name ?? user.email.split("@")[0]}
        timeLimitMinutes={timeLimitMinutes}
        assessmentType={assessmentType}
        skipReferenceCapture
      />
    );
  }

  const firstName = user?.full_name?.split(" ")[0] ?? t("assess.thereFallback");

  return (
    <AgentShell step={phase === "consent" ? 2 : 1} totalSteps={3} title="Masar">

      {phase === "cv_upload" && (
        <>
          <AgentMessage
            content={t("assess.cvUploadGreeting").replace("{name}", firstName)}
            animate
          />
          <AgentMessage
            content={t("assess.cvUploadPrompt")}
            animate={false}
          />
          <div className="bg-card border border-border rounded-2xl p-5">
            <CVUpload onSuccess={handleCVSuccess} />
          </div>
          {!cvRequired && (
            <button
              type="button"
              onClick={() => setPhase("consent")}
              className="self-center text-sm font-medium text-muted-foreground hover:text-foreground underline underline-offset-2"
            >
              {t("assess.skipCv")}
            </button>
          )}
        </>
      )}

      {phase === "consent" && (
        <>
          <AgentMessage
            content={cvResult
              ? t("assess.consentIntroWithCv")
              : t("assess.consentIntroNoCv")}
            animate
          />

          <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4 animate-slide-up">

            {/* CV summary */}
            {cvResult && (
            <div className="bg-primary/5 rounded-xl p-4 flex flex-col gap-2">
              <p className="text-xs font-medium text-primary uppercase tracking-wider">{t("assess.cvSummary")}</p>
              <p className="text-sm text-foreground leading-relaxed line-clamp-3">{cvResult.parsed_summary}</p>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {cvResult.skills.slice(0, 5).map((s) => (
                  <span key={s.name} className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
                    {s.name}
                  </span>
                ))}
              </div>
            </div>
            )}

            <form onSubmit={proceedFromConsent} className="flex flex-col gap-4">
              <div className="border-t border-border pt-4 flex flex-col gap-3">
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{t("assess.quickPermissions")}</p>

                {[
                  {
                    key: "camera",
                    label: t("assess.consentCameraLabel"),
                    hint: t("assess.consentCameraHint"),
                  },
                  {
                    key: "voice",
                    label: t("assess.consentVoiceLabel"),
                    hint: t("assess.consentVoiceHint"),
                  },
                  {
                    key: "data",
                    label: t("assess.consentDataLabel"),
                    hint: t("assess.consentDataHint"),
                  },
                ].map(({ key, label, hint }) => (
                  <label key={key} className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      className="mt-0.5 accent-primary shrink-0"
                      checked={(consent as Record<string, boolean>)[key]}
                      onChange={(e) => setConsent({ ...consent, [key]: e.target.checked })}
                    />
                    <div>
                      <span className="text-sm text-foreground">{label}</span>
                      <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>
                    </div>
                  </label>
                ))}
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <button
                type="submit"
                disabled={starting || !consent.data}
                className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {starting ? t("assess.settingUp") : t("assess.readyLetsGo")}
              </button>
            </form>
          </div>
        </>
      )}
    </AgentShell>
  );
}
