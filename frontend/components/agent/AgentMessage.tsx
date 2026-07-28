"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import AgentAvatar from "./AgentAvatar";
import TypingDots from "./TypingDots";

interface Props {
  content: string;
  animate?: boolean;
  skillSnapshot?: Record<string, number>;
}

// Inline markdown renderer: handles **bold**, line breaks, and paragraphs.
function renderMarkdown(text: string): React.ReactNode {
  return text.split(/\n\n+/).map((para, pIdx) => {
    const parts = para.split(/(\*\*[^*]+\*\*)/g);
    const inline = parts.map((chunk, cIdx) => {
      if (chunk.startsWith("**") && chunk.endsWith("**")) {
        return <strong key={cIdx}>{chunk.slice(2, -2)}</strong>;
      }
      return chunk.split("\n").flatMap((line, lIdx, arr) =>
        lIdx < arr.length - 1 ? [line, <br key={`${cIdx}-${lIdx}`} />] : [line]
      );
    });
    return (
      <p key={pIdx} className={pIdx > 0 ? "mt-2" : undefined}>
        {inline}
      </p>
    );
  });
}

const SKILL_LABEL_KEYS: Record<string, string> = {
  thinking: "chat.skillThinking", soft: "chat.skillSoft", work: "chat.skillWork",
  digital_ai: "chat.skillDigitalAi", growth: "chat.skillGrowth",
};

function SkillSnapshotPill({ snapshot }: { snapshot: Record<string, number> }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const entries = Object.entries(snapshot);

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-primary/70 hover:text-primary transition-colors"
      >
        <span className="text-[10px]">{open ? "▾" : "▸"}</span>
        {t("chat.progressSoFar")}
      </button>
      {open && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {entries.map(([skill, score]) => {
            const label = SKILL_LABEL_KEYS[skill] ? t(SKILL_LABEL_KEYS[skill]) : skill;
            const pct = Math.round((score / 5) * 100);
            const color =
              score >= 3.5 ? "bg-green-500/15 text-green-700 dark:text-green-400" :
              score >= 2.5 ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" :
                             "bg-red-500/15 text-red-700 dark:text-red-400";
            return (
              <span key={skill} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${color}`}>
                {label}
                <span className="opacity-70">{score.toFixed(1)}/5</span>
                <span className="opacity-50">({pct}%)</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function AgentMessage({ content, animate = true, skillSnapshot }: Props) {
  const [visible, setVisible] = useState(!animate);
  const [typing, setTyping] = useState(animate);

  useEffect(() => {
    if (!animate) return;
    const t = setTimeout(() => { setTyping(false); setVisible(true); }, 600);
    return () => clearTimeout(t);
  }, [animate]);

  return (
    <div className="flex items-start gap-3 animate-fade-in">
      <AgentAvatar size="sm" pulse={false} />
      <div className="bg-accent text-accent-foreground rounded-2xl rounded-bl-md px-4 py-3 max-w-[80%] text-sm leading-relaxed">
        {typing ? <TypingDots /> : visible ? (
          <>
            {renderMarkdown(content)}
            {skillSnapshot && Object.keys(skillSnapshot).length > 0 && (
              <SkillSnapshotPill snapshot={skillSnapshot} />
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
