"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabaseClient";
import { useI18n } from "@/lib/i18n";
import { getCurrentUser } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export default function AuthCallbackPage() {
  const router = useRouter();
  const { t } = useI18n();
  const [status, setStatus] = useState(t("report.completingSignIn"));

  useEffect(() => {
    async function handleCallback() {
      const { data, error } = await supabase.auth.getSession();

      if (error || !data.session) {
        setStatus(t("report.signInFailed"));
        setTimeout(() => router.push("/"), 2000);
        return;
      }

      const user = await getCurrentUser();

      if (!user) {
        setStatus(t("report.couldNotLoadProfile"));
        setTimeout(() => router.push("/"), 2000);
        return;
      }

      setStatus(t("report.welcomeRedirecting").replace("{name}", user.full_name?.split(" ")[0] ?? t("report.thereFallback")));

      if (user.role === "admin") {
        setTimeout(() => router.push("/admin"), 1000);
        return;
      }

      // Candidates: check session history to decide between onboarding and dashboard
      try {
        const res = await fetch(
          `${BASE}/session/history?email=${encodeURIComponent(user.email)}`
        );
        if (res.ok) {
          const history = await res.json();
          if (Array.isArray(history) && history.length > 0) {
            setTimeout(() => router.push("/dashboard"), 1000);
            return;
          }
        }
      } catch { /* fall through to onboarding */ }

      setTimeout(() => router.push("/onboarding"), 1000);
    }

    handleCallback();
  }, [router]);

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center gap-6">
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="relative z-10 flex flex-col items-center gap-5 text-center">
        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-primary to-blue-700 agent-orb-pulse flex items-center justify-center shadow-xl">
          <span className="text-white text-xl font-bold">م</span>
        </div>
        <div className="flex flex-col gap-1">
          <p className="text-base font-semibold text-foreground">{status}</p>
          <div className="flex gap-1 justify-center mt-2">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="w-2 h-2 rounded-full bg-primary animate-bounce"
                style={{ animationDelay: `${i * 0.15}s` }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
