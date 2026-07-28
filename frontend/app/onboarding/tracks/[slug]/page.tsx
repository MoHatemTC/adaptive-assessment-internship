"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { getCurrentUser } from "@/lib/auth";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessage from "@/components/agent/AgentMessage";
import AssessmentChoice from "@/components/onboarding/AssessmentChoice";
import { getTrackBySlug } from "@/lib/journeyData";
import { useI18n } from "@/lib/i18n";

export default function TrackDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const { t } = useI18n();

  const track = getTrackBySlug(slug);

  useEffect(() => {
    if (!track) { router.push("/onboarding/tracks"); return; }
    getCurrentUser().then((u) => {
      if (!u) { router.push("/"); return; }
      if (!localStorage.getItem("masar_cv_upload_id")) {
        router.push("/onboarding");
        return;
      }
      setReady(true);
    });
  }, [router, track]);

  if (!ready || !track) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  return (
    <AgentShell step={3} totalSteps={3} title="Masar">
      <div className="flex flex-col gap-1 mb-1">
        <Link
          href="/onboarding/tracks"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors w-fit"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          {t("auth.trackDetail.allTracks", "All tracks")}
        </Link>
        <div className="flex items-center gap-2 mt-1">
          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: track.color }} />
          <h2 className="text-lg font-bold text-foreground">{track.fullName}</h2>
          <span className="text-xs text-muted-foreground">{track.personality}</span>
        </div>
      </div>

      <AgentMessage
        content={`${t("auth.trackDetail.chosenPrefix", "You've chosen")} ${track.fullName}. ${t("auth.trackDetail.proceedQuestion", "How would you like to proceed — get a smart skill assessment, or explore the full specialization?")}`}
        animate
      />

      <AssessmentChoice track={track} />
    </AgentShell>
  );
}
