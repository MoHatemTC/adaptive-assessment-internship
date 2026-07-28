"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessage from "@/components/agent/AgentMessage";
import TrackGrid from "@/components/onboarding/TrackGrid";
import { useI18n } from "@/lib/i18n";

export default function TracksPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const { t } = useI18n();

  useEffect(() => {
    getCurrentUser().then((u) => {
      if (!u) { router.push("/"); return; }
      if (!localStorage.getItem("masar_cv_upload_id")) {
        router.push("/onboarding");
        return;
      }
      setReady(true);
    });
  }, [router]);

  if (!ready) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  return (
    <AgentShell step={2} totalSteps={3} title="Masar">
      <AgentMessage
        content={t("auth.tracks.prompt", "Which specialization do you want to be assessed in? Pick the track that matches your goal — each one has an AI-adaptive assessment built specifically for it.")}
        animate
      />
      <TrackGrid />
    </AgentShell>
  );
}
