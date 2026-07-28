"use client";

import { useEffect, useRef, useState } from "react";
import { sendCameraFrame, sendReferencePhoto } from "@/lib/uploadApi";
import { useI18n } from "@/lib/i18n";

interface Props {
  sessionId: string;
  userId: string;
  onViolation?: (type: string) => void;
  skipReferenceCapture?: boolean;
}

export default function AlwaysOnCamera({ sessionId, userId, onViolation, skipReferenceCapture = false }: Props) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const refPhotoCaptured = useRef(false);
  const onViolationRef = useRef(onViolation);

  // Keep the ref current without making the camera effect depend on the callback
  useEffect(() => { onViolationRef.current = onViolation; }, [onViolation]);

  const [status, setStatus] = useState<"starting" | "active" | "error">("starting");
  const [minimized, setMinimized] = useState(false);
  const [refCaptured, setRefCaptured] = useState(false);

  function captureFrame(): { base64: string; avgBrightness: number } | null {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || video.readyState < 2) return null;
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(video, 0, 0, 320, 240);

    // Sample a grid of pixels to compute average brightness cheaply
    const imageData = ctx.getImageData(0, 0, 320, 240);
    const data = imageData.data;
    let total = 0;
    const step = 40; // sample every 40th pixel
    let count = 0;
    for (let i = 0; i < data.length; i += 4 * step) {
      total += (data[i] + data[i + 1] + data[i + 2]) / 3;
      count++;
    }
    const avgBrightness = count > 0 ? total / count : 0;

    const dataUrl = canvas.toDataURL("image/jpeg", 0.7);
    return { base64: dataUrl.split(",")[1], avgBrightness };
  }

  useEffect(() => {
    let frameInterval: NodeJS.Timeout;
    let refTimer: NodeJS.Timeout;

    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 320, height: 240 },
        });
        streamRef.current = stream;

        // videoRef is always in the DOM — safe to set here
        const video = videoRef.current!;
        video.srcObject = stream;
        await video.play();
        setStatus("active");

        // Capture reference identity photo after 3s (skip if already done in IdentityCapture)
        refTimer = setTimeout(async () => {
          if (refPhotoCaptured.current || skipReferenceCapture) return;
          const frame = captureFrame();
          if (frame) {
            refPhotoCaptured.current = true;
            try {
              await sendReferencePhoto(sessionId, userId, frame.base64);
              setRefCaptured(true);
              setTimeout(() => setRefCaptured(false), 4000);
            } catch {
              // Non-critical — continue without ref photo
            }
          }
        }, 3000);

        // Send a frame every 8 seconds for proctoring
        frameInterval = setInterval(async () => {
          const frame = captureFrame();
          if (!frame) return;

          // ── Brightness check (client-side, free) ──────────────────────────
          // Average brightness < 12/255 → nearly black → camera blocked/off.
          // Use the same vocabulary as the backend + banner ("camera_off").
          if (frame.avgBrightness < 12) {
            if (onViolationRef.current) onViolationRef.current("camera_off");
            try {
              // Still send frame so the blocked image is stored in DB
              await sendCameraFrame(sessionId, userId, frame.base64);
            } catch { /* silent */ }
            return; // skip Gemini analysis — the frame is too dark to be useful
          }

          try {
            const result = await sendCameraFrame(sessionId, userId, frame.base64);
            if (result.violation && result.violation_type && onViolationRef.current) {
              onViolationRef.current(result.violation_type);
            }
          } catch {
            // Silent — never crash the assessment
          }
        }, 8000);
      } catch {
        setStatus("error");
      }
    }

    startCamera();

    return () => {
      clearTimeout(refTimer);
      clearInterval(frameInterval);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, [sessionId, userId]); // onViolation intentionally excluded — stored in ref to avoid camera restarts

  return (
    <div
      className={`fixed top-4 right-4 z-50 rounded-2xl overflow-hidden shadow-xl border border-border/80 bg-card transition-all duration-300 ${
        minimized ? "w-12 h-12" : "w-36 h-28"
      }`}
    >
      {/* Canvas always hidden — used only for frame capture */}
      <canvas ref={canvasRef} className="hidden" />

      {minimized ? (
        <button
          onClick={() => setMinimized(false)}
          className="w-full h-full flex items-center justify-center bg-card"
          title={t("cmp.alwaysOnCamera.showCamera", "Show camera")}
        >
          <svg className="w-5 h-5 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.069A1 1 0 0121 8.847v6.306a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
        </button>
      ) : (
        <div className="relative w-full h-full">
          {/*
            Video is ALWAYS rendered in the DOM so videoRef is valid when
            startCamera() runs. Visibility is controlled by CSS, not conditional rendering.
          */}
          <video
            ref={videoRef}
            muted
            playsInline
            autoPlay
            className={`w-full h-full object-cover ${status === "active" ? "block" : "hidden"}`}
          />

          {status === "error" && (
            <div className="absolute inset-0 bg-destructive/10 flex items-center justify-center">
              <span className="text-xs text-muted-foreground text-center px-1">{t("cmp.alwaysOnCamera.noCamera", "No camera")}</span>
            </div>
          )}

          {status === "starting" && (
            <div className="absolute inset-0 bg-muted flex items-center justify-center">
              <div className="w-4 h-4 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            </div>
          )}

          {/* Status dot */}
          <div className="absolute top-1.5 left-1.5">
            <div className={`w-2 h-2 rounded-full ${
              status === "active" ? "bg-green-500 animate-pulse" :
              status === "error" ? "bg-red-500" : "bg-yellow-500 animate-pulse"
            }`} />
          </div>

          {/* Identity captured badge */}
          {refCaptured && (
            <div className="absolute bottom-1 left-1 right-1 bg-green-600/90 text-white text-[9px] text-center rounded px-1 py-0.5 font-medium">
              {t("cmp.alwaysOnCamera.idCaptured", "ID captured")}
            </div>
          )}

          {/* Minimize */}
          <button
            onClick={() => setMinimized(true)}
            className="absolute top-1 right-1 w-5 h-5 rounded-full bg-black/40 flex items-center justify-center hover:bg-black/60 transition-colors"
          >
            <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}
