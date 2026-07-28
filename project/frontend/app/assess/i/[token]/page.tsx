"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import { openInvitation } from "@/lib/adminApi";
import CVUpload from "@/components/upload/CVUpload";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessage from "@/components/agent/AgentMessage";
import ChatInterface from "@/components/chat/ChatInterface";
import IdentityCapture from "@/components/camera/IdentityCapture";
import LoginButton from "@/components/auth/LoginButton";
import { useI18n } from "@/lib/i18n";
import type { UserProfile, CVParseResult } from "@/lib/types";

type Phase = "loading" | "login" | "invite_info" | "cv_upload" | "consent" | "identity" | "assessment" | "error";

export default function InviteAssessPage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { t } = useI18n();

  const [user, setUser] = useState<UserProfile | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [cvResult, setCvResult] = useState<CVParseResult | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [timeLimitMinutes, setTimeLimitMinutes] = useState(60);
  const [assessmentType, setAssessmentType] = useState<"track" | "discover" | "personality" | "hr">("track");
  const [consent, setConsent] = useState({ camera: true, voice: true, data: false });
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  // Invite metadata
  const [templateTitle, setTemplateTitle] = useState("");
  const [templateDescription, setTemplateDescription] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState("");

  useEffect(() => {
    const init = async () => {
      try {
        const inv = await openInvitation(token);
        setTemplateTitle(inv.template_title || t("assess.assessmentFallback"));
        setTemplateDescription(inv.description ?? null);
        setTimeLimitMinutes(inv.time_limit_minutes);
        setAssessmentType(inv.assessment_type as typeof assessmentType);

        const u = await getCurrentUser();
        if (!u) {
          setPhase("login");
          return;
        }
        setUser(u);
        setPhase("invite_info");
      } catch (e) {
        setInviteError(e instanceof Error ? e.message : t("assess.invitationInvalidError"));
        setPhase("error");
      }
    };
    init();
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!consent.data) { setError(t("assess.consentDataError")); return; }
    if (!cvResult) { setError(t("assess.cvRequiredError")); return; }
    setStarting(true);
    setError("");
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000"}/session/start-invite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invitation_token: token,
          candidate_name: user!.full_name ?? user!.email.split("@")[0],
          candidate_email: user!.email,
          cv_upload_id: cvResult.cv_upload_id,
          consent_camera: consent.camera,
          consent_voice: consent.voice,
          consent_data: consent.data,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail ?? t("assess.startSessionError"));
      }
      const data = await res.json();
      setSessionId(data.session_id);
      setTimeLimitMinutes(data.time_limit_minutes ?? 60);
      if (data.assessment_type) setAssessmentType(data.assessment_type);
      setPhase("identity");
    } catch (e) {
      setError(e instanceof Error ? e.message : t("assess.genericError"));
      setStarting(false);
    }
  };

  const firstName = user?.full_name?.split(" ")[0] ?? t("assess.thereFallback");
  const hours = Math.floor(timeLimitMinutes / 60);
  const mins = timeLimitMinutes % 60;
  const duration = hours
    ? t("assess.durationHoursMinutes").replace("{hours}", String(hours)).replace("{mins}", String(mins))
    : t("assess.durationMinutes").replace("{mins}", String(mins));

  if (phase === "loading") {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center p-6">
        <div className="max-w-md text-center">
          <div className="text-4xl mb-4">🔒</div>
          <h1 className="text-xl font-bold text-foreground mb-2">{t("assess.invitationUnavailable")}</h1>
          <p className="text-sm text-muted-foreground">{inviteError}</p>
        </div>
      </div>
    );
  }

  if (phase === "login") {
    return (
      <AgentShell step={1} totalSteps={3} title="Masar">
        <AgentMessage content={t("assess.loginInviteGreeting").replace("{title}", templateTitle)} animate />
        <AgentMessage content={t("assess.signInGoogleInvite")} animate={false} />
        <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">{templateDescription || t("assess.defaultDescription")}</p>
          <p className="text-xs text-muted-foreground">⏱ {t("assess.approximately")} {duration}</p>
        </div>
        <div className="flex justify-center mt-2">
          <LoginButton variant="hero" />
        </div>
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

  return (
    <AgentShell step={phase === "consent" ? 2 : 1} totalSteps={3} title="Masar">
      {phase === "invite_info" && (
        <>
          <AgentMessage content={t("assess.inviteInfoGreeting").replace("{name}", firstName)} animate />
          <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-3">
            <p className="text-sm font-semibold text-foreground">{templateTitle}</p>
            {templateDescription && <p className="text-sm text-muted-foreground">{templateDescription}</p>}
            <div className="flex gap-4 text-xs text-muted-foreground pt-1">
              <span>⏱ {duration}</span>
              {assessmentType === "hr" && <span>🎙 {t("assess.voiceInterviewOnly")}</span>}
            </div>
            <button
              onClick={() => setPhase("cv_upload")}
              className="w-full mt-2 py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors"
            >
              {t("assess.getStarted")}
            </button>
          </div>
        </>
      )}

      {phase === "cv_upload" && (
        <>
          <AgentMessage content={t("assess.inviteCvPrompt").replace("{name}", firstName)} animate />
          <AgentMessage content={t("assess.inviteCvUploadPrompt")} animate={false} />
          <div className="bg-card border border-border rounded-2xl p-5">
            <CVUpload onSuccess={(r) => { setCvResult(r); setPhase("consent"); }} />
          </div>
        </>
      )}

      {phase === "consent" && cvResult && (
        <>
          <AgentMessage content={t("assess.inviteConsentIntro")} animate />
          <div className="bg-card border border-border rounded-2xl p-5 flex flex-col gap-4 animate-slide-up">
            <div className="bg-primary/5 rounded-xl p-4 flex flex-col gap-2">
              <p className="text-xs font-medium text-primary uppercase tracking-wider">{t("assess.cvSummary")}</p>
              <p className="text-sm text-foreground leading-relaxed line-clamp-3">{cvResult.parsed_summary}</p>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {cvResult.skills.slice(0, 5).map((s) => (
                  <span key={s.name} className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">{s.name}</span>
                ))}
              </div>
            </div>
            <form onSubmit={handleStart} className="flex flex-col gap-4">
              <div className="border-t border-border pt-4 flex flex-col gap-3">
                {[
                  { key: "camera", label: t("assess.consentCameraLabel"), hint: t("assess.inviteCameraHint") },
                  { key: "voice", label: t("assess.consentVoiceLabel"), hint: t("assess.inviteVoiceHint") },
                  { key: "data", label: t("assess.consentDataLabel"), hint: t("assess.inviteDataHint") },
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
