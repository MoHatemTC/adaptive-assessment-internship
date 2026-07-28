"use client";

import { useEffect } from "react";
import { sendProctoringEvent } from "@/lib/sessionApi";

interface Props { sessionId: string; onViolation: () => void; }

export default function ClipboardMonitor({ sessionId, onViolation }: Props) {
  useEffect(() => {
    const handle = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData("text") ?? "";
      sendProctoringEvent({ session_id: sessionId, event_type: "copy_paste", metadata: { length: text.length } });
      onViolation();
    };
    document.addEventListener("paste", handle);
    return () => document.removeEventListener("paste", handle);
  }, [sessionId]);

  return null;
}
