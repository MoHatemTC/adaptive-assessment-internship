"use client";

import { useState } from "react";
import Image from "next/image";
import { useI18n } from "@/lib/i18n";

interface Props { payload: Record<string, unknown>; onSubmit: (r: Record<string, unknown>) => void; }

export default function DiagramViewer({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const [answer, setAnswer] = useState("");

  return (
    <div className="animate-slide-up bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
      {payload.image_url && (
        <div className="relative w-full h-56 rounded-xl overflow-hidden border border-border bg-muted">
          <Image src={payload.image_url as string} alt={t("report.diagram.imageAlt", "Diagram")} fill className="object-contain" />
        </div>
      )}
      <p className="text-sm font-medium text-foreground">{payload.question_body as string}</p>
      <textarea className="w-full border border-border rounded-xl px-4 py-3 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 min-h-[100px] resize-none" placeholder={t("report.diagram.placeholder", "Describe what you see and answer the question…")} value={answer} onChange={(e) => setAnswer(e.target.value)} />
      <button onClick={() => onSubmit({ ...payload, answer_text: answer })} disabled={!answer.trim()} className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-40 transition-colors">
        {t("report.diagram.submit", "Submit Answer")}
      </button>
    </div>
  );
}
