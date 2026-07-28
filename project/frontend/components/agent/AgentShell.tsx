"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useI18n } from "@/lib/i18n";
import AgentAvatar from "./AgentAvatar";

interface Props {
  step?: number;
  totalSteps?: number;
  title?: string;
  timeRemainingMinutes?: number;
  messageCount?: number;
  exitHref?: string;
  children: React.ReactNode;
}

export default function AgentShell({ step, totalSteps, title = "Masar", timeRemainingMinutes, messageCount, exitHref, children }: Props) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll only when a new message arrives AND the user is already near the bottom.
  // Without messageCount in deps this fired every second (countdown re-render) and
  // prevented the user from scrolling up to re-read earlier questions.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distFromBottom < 120) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messageCount]);

  const urgent = timeRemainingMinutes !== undefined && timeRemainingMinutes <= 10;
  const mm = timeRemainingMinutes !== undefined ? String(Math.floor(timeRemainingMinutes)).padStart(2, "0") : null;
  const ss = timeRemainingMinutes !== undefined
    ? String(Math.floor((timeRemainingMinutes % 1) * 60)).padStart(2, "0")
    : null;

  return (
    <div className="min-h-screen bg-background flex flex-col relative overflow-hidden">
      {/* Dot grid + orbs */}
      <div className="absolute inset-0 bg-dot-grid opacity-40 pointer-events-none" />
      <div className="absolute top-1/3 -left-48 w-96 h-96 rounded-full bg-primary/8 blur-3xl pointer-events-none" />
      <div className="absolute bottom-1/4 -right-48 w-96 h-96 rounded-full bg-primary/6 blur-3xl pointer-events-none" />

      {/* Header */}
      <header className="relative z-10 border-b border-border/60 bg-background/80 backdrop-blur-sm px-4 py-3 flex items-center gap-3">
        <AgentAvatar size="sm" />
        <div className="flex-1">
          <p className="text-sm font-semibold text-foreground">{title}</p>
          <p className="text-xs text-muted-foreground">{t("chat.aiInterviewer")}</p>
        </div>

        <div className="flex items-center gap-4">
          {/* Exit to dashboard — assessment progress is saved and resumable */}
          {exitHref && (
            <Link
              href={exitHref}
              className="text-xs font-medium text-muted-foreground hover:text-foreground px-2.5 py-1.5 rounded-lg border border-border hover:bg-secondary transition-colors"
            >
              ← {t("chat.dashboard")}
            </Link>
          )}

          {/* Countdown timer */}
          {mm !== null && ss !== null && (
            <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border ${urgent ? "border-destructive/40 bg-destructive/5" : "border-border bg-secondary/40"}`}>
              <svg className={`w-3.5 h-3.5 ${urgent ? "text-destructive" : "text-muted-foreground"}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className={`text-sm font-mono font-semibold tabular-nums ${urgent ? "text-destructive" : "text-foreground"}`}>
                {mm}:{ss}
              </span>
            </div>
          )}

          {/* Progress */}
          {step !== undefined && totalSteps !== undefined && (
            <div className="flex flex-col items-end gap-1">
              <span className="text-xs text-muted-foreground">{step}/{totalSteps}</span>
              <div className="w-20 h-1 bg-border rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-500"
                  style={{ width: `${(step / totalSteps) * 100}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </header>

      {/* Content */}
      <main ref={containerRef} className="relative z-10 flex-1 flex flex-col max-w-2xl w-full mx-auto px-4 py-6 gap-4 overflow-y-auto">
        {children}
        <div ref={bottomRef} />
      </main>
    </div>
  );
}
