"use client";

import { useEffect, useRef, useState } from "react";
import { sendReferencePhoto } from "@/lib/uploadApi";
import { useI18n } from "@/lib/i18n";

interface Props {
  sessionId: string;
  userId: string;
  onConfirmed: () => void;
}

type Status = "starting" | "countdown" | "captured" | "error";

export default function IdentityCapture({ sessionId, userId, onConfirmed }: Props) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const capturedRef = useRef(false);

  const [status, setStatus] = useState<Status>("starting");
  const [countdown, setCountdown] = useState(3);
  const [cameraError, setCameraError] = useState(false);

  useEffect(() => {
    let countdownInterval: ReturnType<typeof setInterval>;

    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 480 }, height: { ideal: 360 }, facingMode: "user" },
        });
        streamRef.current = stream;

        const video = videoRef.current;
        if (!video) { setCameraError(true); return; }

        video.srcObject = stream;

        // Guard against race condition: loadeddata may fire before handler is registered.
        // Check readyState first; if video already has data, skip waiting.
        if (video.readyState < 2) {
          await new Promise<void>((resolve) => {
            const timeout = setTimeout(resolve, 5000); // 5s hard fallback
            video.onloadeddata = () => { clearTimeout(timeout); resolve(); };
            video.onerror = () => { clearTimeout(timeout); resolve(); }; // don't block on error
          });
        }

        // autoPlay attribute handles play(); call it explicitly as a fallback.
        video.play().catch(() => {});

        setStatus("countdown");
        let count = 3;
        setCountdown(count);

        countdownInterval = setInterval(() => {
          count -= 1;
          setCountdown(count);
          if (count <= 0) {
            clearInterval(countdownInterval);
            captureAndSend();
          }
        }, 1000);
      } catch {
        setCameraError(true);
        setStatus("error");
      }
    }

    startCamera();

    return () => {
      clearInterval(countdownInterval);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function captureAndSend() {
    if (capturedRef.current) return;
    capturedRef.current = true;

    const video = videoRef.current;
    const canvas = canvasRef.current;

    if (video && canvas && video.readyState >= 2) {
      canvas.width = 480;
      canvas.height = 360;
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.drawImage(video, 0, 0, 480, 360);
        const base64 = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
        try {
          await sendReferencePhoto(sessionId, userId, base64);
        } catch {
          // Non-critical
        }
      }
    }

    setStatus("captured");
  }

  const handleContinue = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    onConfirmed();
  };

  return (
    <div className="fixed inset-0 z-[200] bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md flex flex-col gap-5">

        {/* Header */}
        <div className="text-center flex flex-col gap-1">
          <h1 className="text-xl font-bold text-foreground">{t("cmp.identityCapture.title", "Quick identity check")}</h1>
          <p className="text-sm text-muted-foreground">
            {cameraError
              ? t("cmp.identityCapture.noCameraFine", "No camera found — that's totally fine!")
              : t("cmp.identityCapture.lookAtCamera", "Look straight at the camera — we'll take a quick snapshot before you begin.")}
          </p>
        </div>

        {/* Camera preview */}
        <div className="relative rounded-2xl overflow-hidden bg-black aspect-video">
          <canvas ref={canvasRef} className="hidden" />

          {/* autoPlay is required for play() to work without a user gesture in Chrome */}
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className={`w-full h-full object-cover ${cameraError ? "hidden" : "block"}`}
          />

          {/* Face guide oval */}
          {!cameraError && status !== "captured" && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="w-40 h-52 rounded-full border-2 border-white/60 border-dashed" />
            </div>
          )}

          {/* Starting spinner */}
          {status === "starting" && !cameraError && (
            <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
              <div className="w-6 h-6 rounded-full border-2 border-white/30 border-t-white animate-spin" />
            </div>
          )}

          {/* Countdown badge */}
          {status === "countdown" && countdown > 0 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-16 h-16 rounded-full bg-primary/80 backdrop-blur-sm flex items-center justify-center">
                <span className="text-3xl font-bold text-white">{countdown}</span>
              </div>
            </div>
          )}

          {/* Captured checkmark */}
          {status === "captured" && (
            <div className="absolute inset-0 bg-green-500/20 flex items-center justify-center">
              <div className="w-16 h-16 rounded-full bg-green-500 flex items-center justify-center shadow-xl">
                <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
              </div>
            </div>
          )}

          {/* No camera state */}
          {cameraError && (
            <div className="absolute inset-0 bg-muted flex flex-col items-center justify-center gap-3 p-6">
              <svg className="w-10 h-10 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M15 10l4.553-2.069A1 1 0 0121 8.847v6.306a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <p className="text-sm text-muted-foreground text-center">
                {t("cmp.identityCapture.noCameraDetected", "No camera detected — the assessment works perfectly without it.")}
              </p>
            </div>
          )}
        </div>

        {/* Status text */}
        <p className="text-sm text-center text-muted-foreground min-h-[1.25rem]">
          {cameraError
            ? t("cmp.identityCapture.everythingWorks", "Everything else works just fine — let's keep going.")
            : status === "starting"
            ? t("cmp.identityCapture.startingCamera", "Starting camera…")
            : status === "countdown"
            ? `${t("cmp.identityCapture.holdStill", "Hold still — capturing in")} ${countdown}…`
            : t("cmp.identityCapture.snapshotDone", "Snapshot taken. You're all set!")}
        </p>

        {/* CTA */}
        {(status === "captured" || cameraError) ? (
          <button
            onClick={handleContinue}
            className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors"
          >
            {cameraError ? t("cmp.identityCapture.continueAnyway", "Continue anyway →") : t("cmp.identityCapture.letsBegin", "Let's begin →")}
          </button>
        ) : (
          <button
            disabled
            className="w-full py-3 rounded-xl bg-primary/40 text-white font-semibold text-sm cursor-not-allowed"
          >
            {status === "starting" ? t("cmp.identityCapture.startingCamera", "Starting camera…") : `${t("cmp.identityCapture.capturingIn", "Capturing in")} ${countdown}…`}
          </button>
        )}
      </div>
    </div>
  );
}
