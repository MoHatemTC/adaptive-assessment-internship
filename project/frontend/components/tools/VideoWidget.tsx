"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Video, Square, Volume2, VolumeX, Send } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface Props {
  payload: Record<string, unknown>;
  sessionId: string;
  onSubmit: (r: Record<string, unknown>) => void;
}

interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean; interimResults: boolean; lang: string;
  start(): void; stop(): void;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  onend: (() => void) | null;
}
interface SpeechRecognitionEvent extends Event { results: SpeechRecognitionResultList; }

function getSpeechAPI(): (new () => SpeechRecognitionInstance) | null {
  if (typeof window === "undefined") return null;
  return (
    (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionInstance }).SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionInstance }).webkitSpeechRecognition ??
    null
  );
}

/** Camera-on interview: records the candidate (self-view) while capturing a transcript.
 *  The transcript is submitted (scored like a voice answer); the recording is local. */
export default function VideoWidget({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const question =
    (payload.body as string) || (payload.question_body as string) || t("tools.defaultQuestionVideo");
  const interviewerName = (payload.interviewer_name as string) || (payload.interviewer as string) || "Masar";
  const interviewerRole = (payload.interviewer_role as string) || t("tools.interviewer");

  const SpeechAPI = getSpeechAPI();
  const speechSupported = Boolean(SpeechAPI);

  const [camReady, setCamReady] = useState(false);
  const [camError, setCamError] = useState(false);
  const [recording, setRecording] = useState(false);
  const [text, setText] = useState("");
  const [interim, setInterim] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recRef = useRef<SpeechRecognitionInstance | null>(null);
  const activeRef = useRef(false);
  const baseRef = useRef("");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef(0);

  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  // Camera preview on mount
  useEffect(() => {
    let cancelled = false;
    navigator.mediaDevices?.getUserMedia({ video: true, audio: true })
      .then((stream) => {
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (videoRef.current) { videoRef.current.srcObject = stream; videoRef.current.play().catch(() => {}); }
        setCamReady(true);
      })
      .catch(() => setCamError(true));
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    if (recording) {
      startRef.current = Date.now() - elapsed * 1000;
      timerRef.current = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    } else if (timerRef.current) clearInterval(timerRef.current);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [recording]); // eslint-disable-line react-hooks/exhaustive-deps

  const stop = useCallback(() => {
    activeRef.current = false;
    recRef.current?.stop(); recRef.current = null;
    try { recorderRef.current?.state !== "inactive" && recorderRef.current?.stop(); } catch { /* noop */ }
    setInterim(""); setRecording(false);
  }, []);

  const start = useCallback(() => {
    // MediaRecorder (local capture) — best-effort
    if (streamRef.current && typeof MediaRecorder !== "undefined") {
      try { const mr = new MediaRecorder(streamRef.current); recorderRef.current = mr; mr.start(); } catch { /* noop */ }
    }
    // Speech transcript
    if (SpeechAPI) {
      baseRef.current = text ? text.trim() + " " : "";
      const rec = new SpeechAPI();
      rec.continuous = true; rec.interimResults = true; rec.lang = "en-US";
      rec.onresult = (e: SpeechRecognitionEvent) => {
        let intr = ""; let final = baseRef.current;
        for (let i = 0; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) final += r[0].transcript + " "; else intr += r[0].transcript;
        }
        setText(final); setInterim(intr);
      };
      rec.onerror = () => stop();
      rec.onend = () => { if (activeRef.current) rec.start(); };
      recRef.current = rec; activeRef.current = true; rec.start();
    }
    setRecording(true);
  }, [SpeechAPI, text, stop]);

  const handleTTS = useCallback(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; }
    const u = new SpeechSynthesisUtterance(question); u.rate = 0.95;
    u.onend = () => setSpeaking(false); u.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(u); setSpeaking(true);
  }, [speaking, question]);

  useEffect(() => () => { stop(); window.speechSynthesis?.cancel(); }, [stop]);

  // P4 E: grab a still frame from the live self-view so the answer can be scored
  // on visual delivery (presentation) in addition to the transcript.
  const captureFrame = (): string | null => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return null;
    try {
      const c = document.createElement("canvas");
      c.width = 320;
      c.height = Math.round(320 * ((v.videoHeight / v.videoWidth) || 0.75));
      const ctx = c.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(v, 0, 0, c.width, c.height);
      return c.toDataURL("image/jpeg", 0.6);
    } catch { return null; }
  };

  const submit = () => {
    const answer = text.trim();
    if (!answer || submitting) return;
    setSubmitting(true);
    const frame = camReady && !camError ? captureFrame() : null;   // capture BEFORE stopping the stream
    stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    onSubmit({ transcript: answer, duration_seconds: elapsed, recorded_video: camReady && !camError, frames: frame ? [frame] : [] });
  };

  const hasTTS = typeof window !== "undefined" && Boolean(window.speechSynthesis);

  return (
    <div className="animate-slide-up flex flex-col items-center gap-5 py-4">
      <div className="flex items-center gap-2 text-xs font-medium tracking-wide uppercase text-muted-foreground">
        <span className="w-5 h-5 rounded-full bg-gradient-to-br from-primary to-blue-700 flex items-center justify-center text-white text-[9px] font-bold">{interviewerName.charAt(0)}</span>
        <span className="text-primary">{interviewerName}</span><span className="text-border">·</span><span>{interviewerRole}</span>
      </div>

      {/* Camera self-view */}
      <div className="relative w-full max-w-md aspect-video rounded-2xl overflow-hidden bg-black border border-border">
        <video ref={videoRef} muted playsInline className="w-full h-full object-cover" style={{ transform: "scaleX(-1)" }} />
        {camError && (
          <div className="absolute inset-0 flex items-center justify-center text-center px-4">
            <p className="text-sm text-white/80">{t("tools.cameraUnavailable")}</p>
          </div>
        )}
        {recording && (
          <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-black/60 rounded-full px-2.5 py-1">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-xs font-mono text-white">{t("tools.rec")} {fmt(elapsed)}</span>
          </div>
        )}
      </div>

      <h2 className="max-w-2xl text-center text-xl md:text-2xl font-bold text-foreground leading-snug text-balance px-2">{question}</h2>

      {hasTTS && (
        <button onClick={handleTTS}
          className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full border text-sm font-medium transition-colors ${speaking ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300" : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"}`}>
          {speaking ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}{speaking ? t("tools.stop") : t("tools.readAloud")}
        </button>
      )}

      <div className="w-full max-w-2xl">
        <div className="relative flex items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-primary/30">
          <button
            onClick={() => (recording ? stop() : start())}
            title={recording ? t("tools.stopRecording") : t("tools.recordYourAnswer")}
            className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-all ${recording ? "bg-red-500 text-white animate-pulse" : "bg-primary text-white hover:bg-primary/90"}`}
          >
            {recording ? <Square className="w-4 h-4 fill-white" /> : <Video className="w-4 h-4" />}
          </button>
          <textarea
            value={text + (interim ? (text.endsWith(" ") || !text ? "" : " ") + interim : "")}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            rows={1}
            readOnly={recording}
            placeholder={recording ? t("tools.recordingStopToEdit") : t("tools.recordOrTypeEnter")}
            className="flex-1 resize-none bg-transparent px-2 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none max-h-40"
          />
          <button onClick={submit} disabled={!text.trim() || submitting}
            className="shrink-0 inline-flex items-center gap-1.5 px-4 h-10 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary/90 transition-colors disabled:opacity-40">
            {t("tools.send")} <Send className="w-3.5 h-3.5" />
          </button>
        </div>
        {!speechSupported && !camError && (
          <p className="text-xs text-muted-foreground px-2 mt-1.5">{t("tools.speechNotSupported")}</p>
        )}
      </div>
    </div>
  );
}
