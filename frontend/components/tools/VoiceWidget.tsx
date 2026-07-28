"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface Props {
  payload: Record<string, unknown>;
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
    (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionInstance }).SpeechRecognition ||
    (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionInstance }).webkitSpeechRecognition ||
    null
  );
}

export default function VoiceWidget({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  // Payload key may be "body" (backend convention) — defensive fallback
  const questionBody =
    (payload.body as string) ||
    (payload.question_body as string) ||
    t("report.voice.defaultQuestion", "Please answer the following question verbally.");

  const SpeechAPI = getSpeechAPI();
  const speechSupported = Boolean(SpeechAPI);

  const [active, setActive] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interimText, setInterimText] = useState("");
  const [duration, setDuration] = useState(0);
  const [fallbackMode, setFallbackMode] = useState(!speechSupported);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const finalRef = useRef("");
  const activeRef = useRef(false);

  const stop = () => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    activeRef.current = false;
    if (timerRef.current) clearInterval(timerRef.current);
    setActive(false);
    setInterimText("");
  };

  const start = () => {
    if (!SpeechAPI) { setFallbackMode(true); return; }
    finalRef.current = transcript;

    const rec = new SpeechAPI();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";

    rec.onresult = (e: SpeechRecognitionEvent) => {
      let interim = "";
      let final = finalRef.current;
      for (let i = 0; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) { final += r[0].transcript + " "; }
        else           { interim += r[0].transcript; }
      }
      finalRef.current = final;
      setTranscript(final);
      setInterimText(interim);
    };

    rec.onerror = () => { setFallbackMode(true); stop(); };
    // Auto-restart keeps continuous recording across natural pauses
    rec.onend = () => { if (activeRef.current) rec.start(); };

    recognitionRef.current = rec;
    activeRef.current = true;
    rec.start();
    setActive(true);
    timerRef.current = setInterval(() => setDuration((d) => d + 1), 1000);
  };

  useEffect(() => () => stop(), []); // eslint-disable-line react-hooks/exhaustive-deps

  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  return (
    <div className="animate-slide-up bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
      {/* Question */}
      <p className="text-sm font-medium text-foreground leading-relaxed">{questionBody}</p>

      {/* Voice recording mode */}
      {!fallbackMode && (
        <>
          <div className="flex items-center gap-3">
            <button
              onClick={active ? stop : start}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                active
                  ? "bg-destructive text-white hover:bg-destructive/90"
                  : "bg-primary text-white hover:bg-primary/90"
              }`}
            >
              {active ? <><Square className="w-4 h-4" /> {t("report.voice.stop", "Stop")}</> : <><Mic className="w-4 h-4" /> {t("report.voice.startRecording", "Start Recording")}</>}
            </button>
            {active && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                <span className="font-mono">{fmt(duration)}</span>
              </div>
            )}
          </div>

          {/* Live transcript */}
          {(transcript || interimText) && (
            <div className="bg-muted rounded-xl p-4 min-h-[72px]">
              <p className="text-xs text-muted-foreground mb-1 font-medium uppercase tracking-wider">{t("report.voice.yourAnswer", "Your answer")}</p>
              <p className="text-sm text-foreground leading-relaxed">
                {transcript}
                {interimText && <span className="text-muted-foreground italic">{interimText}</span>}
              </p>
            </div>
          )}

          <button
            type="button"
            onClick={() => { stop(); setFallbackMode(true); }}
            className="text-xs text-muted-foreground underline underline-offset-2 self-start"
          >
            {t("report.voice.preferType", "Prefer to type instead?")}
          </button>
        </>
      )}

      {/* Text fallback */}
      {fallbackMode && (
        <div className="flex flex-col gap-2">
          {!speechSupported && (
            <p className="text-xs text-amber-600 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2">
              {t("report.voice.notAvailable", "Speech recognition is not available in this browser. Please type your answer below.")}
            </p>
          )}
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={t("report.voice.typePlaceholder", "Type your answer here…")}
            rows={5}
            className="w-full border border-border rounded-xl px-4 py-3 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
          />
          {speechSupported && (
            <button
              type="button"
              onClick={() => setFallbackMode(false)}
              className="text-xs text-muted-foreground underline underline-offset-2 self-start"
            >
              {t("report.voice.switchToVoice", "Switch to voice recording")}
            </button>
          )}
        </div>
      )}

      <button
        onClick={() => onSubmit({ transcript: transcript.trim(), duration_seconds: duration })}
        disabled={!transcript.trim()}
        className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-40 transition-colors"
      >
        {t("report.voice.submit", "Submit Answer")}
      </button>
    </div>
  );
}
