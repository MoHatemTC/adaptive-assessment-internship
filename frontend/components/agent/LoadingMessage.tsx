"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { ToolType } from "@/lib/types";

// Per-tool messages — rotate to avoid repetition. Values are i18n keys resolved at render.
const TOOL_MESSAGES: Record<string, string[]> = {
  mcq: [
    "chat.loadMcq0",
    "chat.loadMcq1",
    "chat.loadMcq2",
    "chat.loadMcq3",
    "chat.loadMcq4",
    "chat.loadMcq5",
    "chat.loadMcq6",
    "chat.loadMcq7",
  ],
  coding: [
    "chat.loadCoding0",
    "chat.loadCoding1",
    "chat.loadCoding2",
    "chat.loadCoding3",
    "chat.loadCoding4",
    "chat.loadCoding5",
    "chat.loadCoding6",
    "chat.loadCoding7",
  ],
  voice: [
    "chat.loadVoice0",
    "chat.loadVoice1",
    "chat.loadVoice2",
    "chat.loadVoice3",
    "chat.loadVoice4",
    "chat.loadVoice5",
    "chat.loadVoice6",
    "chat.loadVoice7",
  ],
  visualization: [
    "chat.loadViz0",
    "chat.loadViz1",
    "chat.loadViz2",
    "chat.loadViz3",
    "chat.loadViz4",
  ],
  task: [
    "chat.loadTask0",
    "chat.loadTask1",
    "chat.loadTask2",
    "chat.loadTask3",
    "chat.loadTask4",
  ],
  // Transition-specific keys
  start: [
    "chat.loadStart0",
    "chat.loadStart1",
    "chat.loadStart2",
    "chat.loadStart3",
    "chat.loadStart4",
  ],
  between: [
    "chat.loadBetween0",
    "chat.loadBetween1",
    "chat.loadBetween2",
    "chat.loadBetween3",
    "chat.loadBetween4",
  ],
  default: [
    "chat.loadDefault0",
    "chat.loadDefault1",
    "chat.loadDefault2",
    "chat.loadDefault3",
    "chat.loadDefault4",
  ],
};

interface Props {
  activeTool?: ToolType;
  phase?: "start" | "between" | "question";
}

export default function LoadingMessage({ activeTool, phase }: Props) {
  const { t } = useI18n();
  // Determine which pool to use:
  // 1. Explicit phase hints (start, between) take priority
  // 2. Then active tool type
  // 3. Then default
  const poolKey =
    phase === "start" ? "start" :
    phase === "between" ? "between" :
    activeTool ?? "default";

  const pool = TOOL_MESSAGES[poolKey] ?? TOOL_MESSAGES.default;

  const [index, setIndex] = useState(() => Math.floor(Math.random() * pool.length));
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const cycle = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIndex((i) => (i + 1) % pool.length);
        setVisible(true);
      }, 300);
    }, 2400);
    return () => clearInterval(cycle);
  }, [pool.length]);

  return (
    <div className="flex items-center gap-3 py-1">
      {/* Animated orb */}
      <div className="relative w-8 h-8 shrink-0">
        <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping" />
        <div className="relative w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
          <div className="w-3 h-3 rounded-full bg-primary/60 animate-pulse" />
        </div>
      </div>

      {/* Rotating message */}
      <span
        className="text-sm text-muted-foreground transition-opacity duration-300"
        style={{ opacity: visible ? 1 : 0 }}
      >
        {t(pool[index])}
      </span>
    </div>
  );
}
