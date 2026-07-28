"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Volume2, VolumeX, Send } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface Props {
  payload: Record<string, unknown>;
  sessionId: string;
  onSubmit: (r: Record<string, unknown>) => void;
}

interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  onend: (() => void) | null;
}
interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
}

function getSpeechAPI(): (new () => SpeechRecognitionInstance) | null {
  if (typeof window === "undefined") return null;
  return (
    (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionInstance }).SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionInstance }).webkitSpeechRecognition ??
    null
  );
}

export default function VoiceInterview({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const question =
    (payload.body as string) ||
    (payload.question_body as string) ||
    t("tools.defaultQuestionVoice");

  // Interviewer persona (populated by the generator when available; graceful defaults).
  const interviewerName = (payload.interviewer_name as string) || (payload.interviewer as string) || "Masar";
  const interviewerRole = (payload.interviewer_role as string) || t("tools.interviewer");
  const interviewerOrg  = (payload.interviewer_org as string) || "";

  const SpeechAPI = getSpeechAPI();
  const speechSupported = Boolean(SpeechAPI);

  const [recording, setRecording] = useState(false);
  const [text, setText] = useState("");        // the answer — editable, filled by mic or typing
  const [interim, setInterim] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const recRef = useRef<SpeechRecognitionInstance | null>(null);
  const activeRef = useRef(false);
  const baseRef = useRef("");        // text captured before the current mic session
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef(0);

  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  useEffect(() => {
    if (recording) {
      startTimeRef.current = Date.now() - elapsed * 1000;
      timerRef.current = setInterval(() => setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000)), 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [recording]); // eslint-disable-line react-hooks/exhaustive-deps

  const stopRec = useCallback(() => {
    activeRef.current = false;
    recRef.current?.stop();
    recRef.current = null;
    setInterim("");
    setRecording(false);
  }, []);

  const startRec = useCallback(() => {
    if (!SpeechAPI) return;
    baseRef.current = text ? text.trim() + " " : "";
    const rec = new SpeechAPI();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.onresult = (e: SpeechRecognitionEvent) => {
      let intr = "";
      let final = baseRef.current;
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) final += r[0].transcript + " ";
        else intr += r[0].transcript;
      }
      setText(final);
      setInterim(intr);
    };
    rec.onerror = () => stopRec();
    rec.onend = () => { if (activeRef.current) rec.start(); };
    recRef.current = rec;
    activeRef.current = true;
    rec.start();
    setRecording(true);
  }, [SpeechAPI, text, stopRec]);

  const handleTTS = useCallback(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return; }
    const utt = new SpeechSynthesisUtterance(question);
    utt.rate = 0.95;
    utt.onend = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utt);
    setSpeaking(true);
  }, [speaking, question]);

  useEffect(() => () => { stopRec(); window.speechSynthesis?.cancel(); }, [stopRec]);

  const submit = () => {
    const answer = text.trim();
    if (!answer || submitting) return;
    setSubmitting(true);
    if (recording) stopRec();
    onSubmit({ transcript: answer, duration_seconds: elapsed });
  };

  const hasTTS = typeof window !== "undefined" && Boolean(window.speechSynthesis);

  return (
    <div className="animate-slide-up flex flex-col items-center gap-6 py-6">
      {/* Interviewer identity */}
      <div className="flex items-center gap-2 text-xs font-medium tracking-wide uppercase text-muted-foreground">
        <span className="w-5 h-5 rounded-full bg-gradient-to-br from-primary to-blue-700 flex items-center justify-center text-white text-[9px] font-bold">
          {interviewerName.charAt(0)}
        </span>
        <span className="text-primary">{interviewerName}</span>
        <span className="text-border">·</span>
        <span>{interviewerRole}{interviewerOrg ? ` @ ${interviewerOrg}` : ""}</span>
      </div>

      {/* Orb */}
      <div className="relative flex items-center justify-center w-36 h-36">
        {(recording || speaking) && (
          <>
            <span className={`absolute w-36 h-36 rounded-full animate-ping ${speaking ? "bg-amber-500/15" : "bg-primary/15"}`} style={{ animationDuration: "1.8s" }} />
            <span className={`absolute w-28 h-28 rounded-full animate-pulse ${speaking ? "bg-amber-500/10" : "bg-primary/10"}`} />
          </>
        )}
        <div
          className="w-28 h-28 rounded-full shadow-2xl"
          style={{
            background: "radial-gradient(circle at 35% 30%, #7DA6F5 0%, #3B6FE0 45%, #1E3A8A 100%)",
            boxShadow: "inset -6px -8px 20px rgba(0,0,0,0.35), 0 12px 40px rgba(37,99,235,0.30)",
          }}
        />
      </div>

      {/* Question */}
      <h2 className="max-w-2xl text-center text-2xl md:text-3xl font-bold text-foreground leading-snug text-balance px-2">
        {question}
      </h2>

      {/* Read aloud */}
      {hasTTS && (
        <button
          onClick={handleTTS}
          className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full border text-sm font-medium transition-colors ${
            speaking ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-300" : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
          }`}
        >
          {speaking ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          {speaking ? t("tools.stop") : t("tools.readAloud")}
        </button>
      )}

      {/* Unified mic-or-type input bar */}
      <div className="w-full max-w-2xl">
        <div className="relative flex items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-primary/30">
          {speechSupported && (
            <button
              onClick={() => (recording ? stopRec() : startRec())}
              title={recording ? t("tools.stopMic") : t("tools.speakYourAnswer")}
              className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center transition-all ${
                recording ? "bg-red-500 text-white animate-pulse" : "bg-primary text-white hover:bg-primary/90"
              }`}
            >
              {recording ? <Square className="w-4 h-4 fill-white" /> : <Mic className="w-4 h-4" />}
            </button>
          )}
          <textarea
            value={text + (interim ? (text.endsWith(" ") || !text ? "" : " ") + interim : "")}
            onChange={(e) => { setText(e.target.value); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            rows={1}
            readOnly={recording}
            placeholder={recording ? t("tools.listeningStopToEdit") : speechSupported ? t("tools.micOrTypeEnter") : t("tools.typeAnswerEnter")}
            className="flex-1 resize-none bg-transparent px-2 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none max-h-40"
          />
          <button
            onClick={submit}
            disabled={!text.trim() || submitting}
            className="shrink-0 inline-flex items-center gap-1.5 px-4 h-10 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary/90 transition-colors disabled:opacity-40"
          >
            {t("tools.send")} <Send className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="flex items-center justify-between px-2 mt-1.5 h-4">
          <span className="text-xs text-muted-foreground">
            {recording ? <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" /> {t("tools.listening")} {fmt(elapsed)}</span> : t("tools.shiftEnterNewLine")}
          </span>
        </div>
      </div>
    </div>
  );
}
