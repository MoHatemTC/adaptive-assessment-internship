"use client";

import { useEffect } from "react";
import { sendProctoringEvent } from "@/lib/sessionApi";

interface Props { sessionId: string; onViolation: () => void; }

export default function TabMonitor({ sessionId, onViolation }: Props) {
  useEffect(() => {
    const handle = () => {
      if (document.hidden) {
        sendProctoringEvent({ session_id: sessionId, event_type: "tab_switch" });
        onViolation();
      }
    };
    const blur = () => {
      sendProctoringEvent({ session_id: sessionId, event_type: "window_blur" });
      onViolation();
    };
    document.addEventListener("visibilitychange", handle);
    window.addEventListener("blur", blur);
    return () => { document.removeEventListener("visibilitychange", handle); window.removeEventListener("blur", blur); };
  }, [sessionId]);

  return null;
}
