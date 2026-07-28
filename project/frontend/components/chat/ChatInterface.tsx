"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useI18n } from "@/lib/i18n";
import { sendChatTurn, submitAssessment } from "@/lib/sessionApi";
import { ToolType, AgentMessage } from "@/lib/types";
import AgentShell from "@/components/agent/AgentShell";
import AgentMessageView from "@/components/agent/AgentMessage";
import UserReply from "@/components/agent/UserReply";
import TypingDots from "@/components/agent/TypingDots";
import LoadingMessage from "@/components/agent/LoadingMessage";
import ToolRenderer from "./ToolRenderer";
import ToolDoneCard from "@/components/tools/ToolDoneCard";
import StackFeedbackCard from "@/components/tools/StackFeedbackCard";
import TabMonitor from "@/components/proctoring/TabMonitor";
import ClipboardMonitor from "@/components/proctoring/ClipboardMonitor";
import IntegrityBanner from "@/components/proctoring/IntegrityBanner";
import AlwaysOnCamera from "@/components/camera/AlwaysOnCamera";

interface Message {
  role: "agent" | "user" | "tool_done" | "stack_feedback";
  content: string;
  tool_type?: ToolType;
  tool_payload?: Record<string, unknown> | null;
  tool_result?: Record<string, unknown> | null;
  set_retro_completed_type?: string;
  set_retro_scores?: number[];
  skill_snapshot?: Record<string, number>;
}

interface Props {
  sessionId: string;
  userId: string;
  candidateName: string;
  timeLimitMinutes?: number;
  assessmentType?: "track" | "discover";
  skipReferenceCapture?: boolean;
}

export default function ChatInterface({ sessionId, userId, candidateName, timeLimitMinutes = 60, assessmentType = "track", skipReferenceCapture = false }: Props) {
  const router = useRouter();
  const { t } = useI18n();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [activeTool, setActiveTool] = useState<ToolType>(null);
  const [activePayload, setActivePayload] = useState<Record<string, unknown> | null>(null);
  const [violations, setViolations] = useState(0);
  const [lastViolationType, setLastViolationType] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [questionCount, setQuestionCount] = useState(0);
  const [totalSlots] = useState(0);
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(timeLimitMinutes * 60);
  const inputRef = useRef<HTMLInputElement>(null);
  const startedRef = useRef(false);
  const autoSubmittedRef = useRef(false);

  // Countdown timer
  useEffect(() => {
    const t = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 0) { clearInterval(t); return 0; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // Auto-submit when time runs out
  useEffect(() => {
    if (secondsLeft === 0 && !complete && !submitting && !autoSubmittedRef.current) {
      autoSubmittedRef.current = true;
      handleEarlySubmit();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secondsLeft]);

  // Send initial greeting turn once
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    sendTurn(`Hi, I'm ${candidateName}. Ready to start.`);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sendTurn = useCallback(async (message: string, toolResult?: Record<string, unknown>) => {
    setSending(true);
    if (message && !toolResult) {
      setMessages((m) => [...m, { role: "user", content: message }]);
    }
    try {
      const res = await sendChatTurn({ session_id: sessionId, message, tool_result: toolResult });

      // First turn returns res.messages = [orientationMsg, questionMsg].
      // All other turns return only res.message.
      const agentMsgs: AgentMessage[] = (res as { messages?: AgentMessage[] }).messages ?? [res.message];
      const lastMsg = agentMsgs[agentMsgs.length - 1];

      const toAppend: Message[] = [];

      // Stack-boundary retro lives on the last message
      if (lastMsg.set_retro) {
        toAppend.push({
          role: "stack_feedback",
          content: lastMsg.set_retro.text,
          set_retro_completed_type: lastMsg.set_retro.completed_type,
          set_retro_scores: lastMsg.set_retro.scores,
        });
      }

      for (const msg of agentMsgs) {
        toAppend.push({
          role: "agent",
          content: msg.content,
          tool_type: msg.tool_type,
          tool_payload: msg.tool_payload,
          skill_snapshot: msg.skill_snapshot,
        });
      }

      setMessages((m) => [...m, ...toAppend]);

      if (lastMsg.tool_type) {
        setActiveTool(lastMsg.tool_type);
        setActivePayload(lastMsg.tool_payload ?? null);
        setQuestionCount((q) => q + 1);
      } else {
        setActiveTool(null);
        setActivePayload(null);
      }

      if (res.session_complete) {
        setComplete(true);
        setTimeout(() => router.push(assessmentType === "discover" ? `/discover/results/${sessionId}` : `/report/${sessionId}`), 4000);
      }
    } catch (e) {
      console.error("Chat turn error:", e);
      setMessages((m) => [...m, {
        role: "agent",
        content: t("chat.errorGeneric"),
        tool_type: null,
        tool_payload: null,
      }]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }, [sessionId, router]);

  const handleToolSubmit = (toolResult: Record<string, unknown>) => {
    // Snapshot current tool state, then clear — card stays in history
    if (activeTool && activePayload) {
      setMessages((m) => [...m, {
        role: "tool_done" as const,
        content: "",
        tool_type: activeTool,
        tool_payload: activePayload,
        tool_result: toolResult,
      }]);
    }
    setActiveTool(null);
    setActivePayload(null);
    sendTurn("", toolResult);
  };

  const handleTextSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || sending) return;
    const msg = input.trim();
    setInput("");
    sendTurn(msg);
  };

  const handleViolation = useCallback((type?: string) => {
    setViolations((v) => v + 1);
    if (type) setLastViolationType(type);
  }, []);

  const handleEarlySubmit = async () => {
    setShowSubmitConfirm(false);
    setSubmitting(true);
    try {
      const res = await submitAssessment(sessionId);
      setMessages((m) => [...m, {
        role: "agent",
        content: res.message.content,
        tool_type: null,
        tool_payload: null,
      }]);
      setActiveTool(null);
      setActivePayload(null);
      setComplete(true);
      setTimeout(() => router.push(assessmentType === "discover" ? `/discover/results/${sessionId}` : `/report/${sessionId}`), 4000);
    } catch (e) {
      console.error("Submit error:", e);
    } finally {
      setSubmitting(false);
    }
  };

  const minutesLeft = secondsLeft / 60;

  return (
    <>
      <TabMonitor sessionId={sessionId} onViolation={handleViolation} />
      <ClipboardMonitor sessionId={sessionId} onViolation={handleViolation} />
      <IntegrityBanner violations={violations} lastType={lastViolationType} />
      <AlwaysOnCamera sessionId={sessionId} userId={userId} onViolation={handleViolation} skipReferenceCapture={skipReferenceCapture} />

      {/* Time warning / auto-submit banner */}
      {secondsLeft > 0 && secondsLeft <= 120 && !complete && (
        <div className="fixed top-0 inset-x-0 z-[90] bg-amber-500 text-white text-center py-2 text-sm font-medium animate-pulse">
          {`${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")} ${t("chat.timeRemainingWarning")}`}
        </div>
      )}
      {secondsLeft === 0 && !complete && (
        <div className="fixed top-0 inset-x-0 z-[90] bg-destructive text-white text-center py-2 text-sm font-medium">
          {t("chat.timeUp")}
        </div>
      )}

      {/* Early submit confirmation dialog */}
      {showSubmitConfirm && (
        <div className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-2xl shadow-2xl p-6 max-w-sm w-full flex flex-col gap-4">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-full bg-yellow-500/10 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-yellow-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
              </div>
              <div>
                <p className="font-semibold text-foreground text-sm">{t("chat.confirmTitle")}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {totalSlots > 0 && questionCount < totalSlots
                    ? `${t("chat.answeredCountPrefix")} ${questionCount} ${t("chat.answeredCountMiddle")} ${totalSlots} ${t("chat.answeredCountSuffix")}`
                    : t("chat.reportEmailNote")}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleEarlySubmit}
                className="flex-1 py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors"
              >
                {t("chat.confirmFinish")}
              </button>
              <button
                onClick={() => setShowSubmitConfirm(false)}
                className="flex-1 py-2.5 rounded-xl border border-border text-sm font-medium hover:bg-secondary transition-colors"
              >
                {t("chat.keepGoing")}
              </button>
            </div>
          </div>
        </div>
      )}

      <AgentShell
        title={t("chat.assessmentTitle")}
        step={questionCount > 0 ? questionCount : undefined}
        totalSteps={totalSlots > 0 ? totalSlots : undefined}
        timeRemainingMinutes={minutesLeft}
        messageCount={messages.length}
        exitHref="/dashboard"
      >
        {messages.map((m, i) =>
          m.role === "stack_feedback" ? (
            <StackFeedbackCard
              key={i}
              text={m.content}
              completedType={m.set_retro_completed_type ?? ""}
              scores={m.set_retro_scores ?? []}
            />
          ) : m.role === "tool_done" ? (
            <ToolDoneCard
              key={i}
              toolType={m.tool_type ?? null}
              payload={m.tool_payload ?? {}}
              result={m.tool_result ?? {}}
            />
          ) : m.role === "agent" ? (
            <div key={i} className="flex flex-col gap-3">
              <AgentMessageView content={m.content} animate={i === messages.length - 1} skillSnapshot={m.skill_snapshot} />
              {i === messages.length - 1 && activeTool && activePayload && (
                <ToolRenderer
                  toolType={activeTool}
                  payload={activePayload}
                  sessionId={sessionId}
                  onSubmit={handleToolSubmit}
                />
              )}
            </div>
          ) : (
            <UserReply key={i} content={m.content} />
          )
        )}

        {sending && (
          <LoadingMessage
            activeTool={activeTool}
            phase={
              messages.length === 0 ? "start" :
              messages[messages.length - 1]?.role === "stack_feedback" ? "between" :
              undefined
            }
          />
        )}

        {complete && (
          <div className="text-center py-8 animate-fade-in flex flex-col items-center gap-3">
            <div className="w-14 h-14 rounded-full bg-green-500/10 flex items-center justify-center">
              <svg className="w-7 h-7 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-base font-semibold text-foreground">{t("chat.youDidIt")}</p>
            <p className="text-sm text-muted-foreground">
              {assessmentType === "discover" ? t("chat.analyzingTrackFit") : t("chat.puttingResults")}
            </p>
          </div>
        )}

        {!activeTool && !sending && !complete && (
          <form onSubmit={handleTextSend} className="mt-4 flex gap-2">
            <input
              ref={inputRef}
              className="flex-1 border border-border rounded-xl px-4 py-3 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder={t("chat.inputPlaceholder")}
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
            <button
              type="submit"
              disabled={!input.trim()}
              className="px-5 py-3 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-40 transition-colors"
            >
              {t("chat.send")}
            </button>
          </form>
        )}

        {activeTool && activePayload && (
          <div className="mt-3 flex justify-end">
            <button
              type="button"
              onClick={() => handleToolSubmit({ skipped: true })}
              disabled={sending}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40"
            >
              {t("chat.skipQuestion", "Skip question")}
            </button>
          </div>
        )}

        {!complete && (
          <div className="mt-3 flex justify-end">
            <button
              onClick={() => setShowSubmitConfirm(true)}
              disabled={submitting || sending}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border border-border hover:border-foreground/30 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40"
            >
              {submitting ? (
                <div className="w-3 h-3 rounded-full border-2 border-muted-foreground border-t-transparent animate-spin" />
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              )}
              {submitting ? t("chat.submitting") : t("chat.finishSubmit")}
            </button>
          </div>
        )}
      </AgentShell>
    </>
  );
}
