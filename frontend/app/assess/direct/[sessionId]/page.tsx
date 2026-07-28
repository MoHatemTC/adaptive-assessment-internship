"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import ChatInterface from "@/components/chat/ChatInterface";
import IdentityCapture from "@/components/camera/IdentityCapture";
import type { UserProfile } from "@/lib/types";

export default function DirectAssessPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [ready, setReady] = useState(false);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);

  const timeLimitMinutes = parseInt(searchParams.get("mins") ?? "60", 10);
  const assessmentType = (searchParams.get("mode") ?? "track") as "track" | "discover";

  useEffect(() => {
    getCurrentUser().then((u) => {
      if (!u) { router.push("/"); return; }
      setUser(u);
      setReady(true);
    });
  }, [router]);

  if (!ready || !user) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-8 h-8 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  if (!identityConfirmed) {
    return (
      <IdentityCapture
        sessionId={sessionId}
        userId={user.id}
        onConfirmed={() => setIdentityConfirmed(true)}
      />
    );
  }

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
