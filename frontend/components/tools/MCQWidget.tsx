"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

interface Option { id: string; text: string; }
interface Props { payload: Record<string, unknown>; onSubmit: (r: Record<string, unknown>) => void; }

export default function MCQWidget({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const options = (payload.options as Option[]) ?? [];
  const [selected, setSelected] = useState<string | null>(null);

  const handleSubmit = () => {
    if (!selected) return;
    const opt = options.find((o) => o.id === selected);
    onSubmit({ ...payload, selected_id: selected, selected_label: opt?.text ?? "" });
  };

  const questionText = (payload.body as string) || (payload.question_body as string) || "";

  return (
    <div className="animate-slide-up bg-card border border-border rounded-2xl p-5 flex flex-col gap-4">
      <p className="text-sm font-medium text-foreground">{questionText}</p>
      <div className="flex flex-col gap-2">
        {options.map((opt) => (
          <button key={opt.id} onClick={() => setSelected(opt.id)}
            className={cn("text-left px-4 py-3 rounded-xl border text-sm transition-all",
              selected === opt.id ? "border-primary bg-primary/10 text-primary font-medium" : "border-border hover:border-primary/50 text-foreground")}>
            <span className="font-mono text-xs mr-2 text-muted-foreground">{opt.id}.</span>{opt.text}
          </button>
        ))}
      </div>
      <button onClick={handleSubmit} disabled={!selected} className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-40 transition-colors">
        {t("tools.doneSubmit")}
      </button>
    </div>
  );
}
