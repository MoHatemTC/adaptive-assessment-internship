"use client";

import { useEffect, useRef } from "react";
import { sendProctoringEvent } from "@/lib/sessionApi";

interface Props { sessionId: string; onViolation: () => void; }

export default function CameraGuard({ sessionId, onViolation }: Props) {
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    navigator.mediaDevices.getUserMedia({ video: true }).then((stream) => {
      streamRef.current = stream;
      interval = setInterval(() => {
        const track = stream.getVideoTracks()[0];
        if (!track || track.readyState === "ended") {
          sendProctoringEvent({ session_id: sessionId, event_type: "camera_off" });
          onViolation();
        }
      }, 5000);
    }).catch(() => {
      sendProctoringEvent({ session_id: sessionId, event_type: "camera_off" });
    });
    return () => {
      clearInterval(interval);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, [sessionId]);

  return null;
}
