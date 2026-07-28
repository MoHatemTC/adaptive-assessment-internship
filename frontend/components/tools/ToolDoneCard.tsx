"use client";

import { ToolType } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

interface Props {
  toolType: ToolType;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
}

export default function ToolDoneCard({ toolType, payload, result }: Props) {
  if (toolType === "mcq") return <MCQDoneCard payload={payload} result={result} />;
  if (toolType === "voice") return <VoiceDoneCard payload={payload} result={result} />;
  if (toolType === "coding") return <CodingDoneCard payload={payload} result={result} />;
  return <GenericDoneCard toolType={toolType} />;
}

function Check() {
  return (
    <svg className="w-3 h-3 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
    </svg>
  );
}

// Friendly, past-tense labels shown in the "done" card after submission
function DoneHeader({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-5 h-5 rounded-full bg-green-500/20 flex items-center justify-center flex-shrink-0">
        <Check />
      </div>
      <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</span>
    </div>
  );
}

function MCQDoneCard({ payload, result }: { payload: Record<string, unknown>; result: Record<string, unknown> }) {
  const { t } = useI18n();
  const question = (payload.question_body ?? payload.body ?? "") as string;
  const selectedId = (result.selected_id ?? "") as string;
  const selectedLabel = (result.selected_label ?? "") as string;

  return (
    <div className="bg-secondary/40 border border-border rounded-xl p-4 flex flex-col gap-2 opacity-80">
      <DoneHeader label={t("tools.answerSaved")} />
      {question && <p className="text-xs text-muted-foreground line-clamp-2">{question}</p>}
      <p className="text-sm text-foreground">
        <span className="font-mono text-xs text-muted-foreground mr-1.5">{selectedId}.</span>
        {selectedLabel}
      </p>
    </div>
  );
}

function VoiceDoneCard({ result }: { payload: Record<string, unknown>; result: Record<string, unknown> }) {
  const { t } = useI18n();
  const transcript = (result.transcript ?? "") as string;
  const duration = (result.duration_seconds ?? 0) as number;
  const turns = result.turns as Array<{ question: string; answer: string }> | undefined;
  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  return (
    <div className="bg-secondary/40 border border-border rounded-xl p-4 flex flex-col gap-2 opacity-80">
      <div className="flex items-center justify-between">
        <DoneHeader label={t("tools.verbalAnswerSaved")} />
        {duration > 0 && <span className="text-xs text-muted-foreground font-mono">{fmt(duration)}</span>}
      </div>
      {turns && turns.length > 0 ? (
        <div className="flex flex-col gap-1.5 mt-1">
          {turns.slice(0, 3).map((turn, i) => (
            <div key={i} className="text-xs text-muted-foreground leading-relaxed">
              <span className="font-medium text-foreground/60">{t("tools.answerAbbrev")}{i + 1}:</span>{" "}
              {turn.answer.slice(0, 90)}{turn.answer.length > 90 ? "…" : ""}
            </div>
          ))}
        </div>
      ) : transcript ? (
        <p className="text-xs text-muted-foreground italic line-clamp-2">
          &ldquo;{transcript.slice(0, 120)}{transcript.length > 120 ? "…" : ""}&rdquo;
        </p>
      ) : null}
    </div>
  );
}

function CodingDoneCard({ payload, result }: { payload: Record<string, unknown>; result: Record<string, unknown> }) {
  const { t } = useI18n();
  const question = (payload.body ?? "") as string;
  const code = (result.code ?? "") as string;
  const language = ((result.language ?? payload.language ?? "code") as string).toLowerCase();
  const preview = code.split("\n").slice(0, 3).join("\n");

  return (
    <div className="bg-secondary/40 border border-border rounded-xl p-4 flex flex-col gap-2 opacity-80">
      <DoneHeader label={`${t("tools.codeSaved")} · ${language}`} />
      {question && <p className="text-xs text-muted-foreground line-clamp-1">{question}</p>}
      {preview && (
        <pre className="text-xs text-foreground/70 font-mono bg-background/60 rounded-lg p-2 overflow-hidden line-clamp-3 whitespace-pre-wrap">
          {preview}{code.split("\n").length > 3 ? "\n…" : ""}
        </pre>
      )}
    </div>
  );
}

function GenericDoneCard({ toolType }: { toolType: ToolType }) {
  const { t } = useI18n();
  const label: Record<string, string> = {
    task:          t("tools.taskSubmitted"),
    visualization: t("tools.analysisSaved"),
  };
  return (
    <div className="bg-secondary/40 border border-border rounded-xl px-4 py-3 flex items-center gap-2 opacity-80">
      <DoneHeader label={label[toolType as string] ?? t("tools.answerSubmitted")} />
    </div>
  );
}
