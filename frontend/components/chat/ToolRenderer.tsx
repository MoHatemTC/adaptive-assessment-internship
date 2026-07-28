"use client";

import { Component, ReactNode } from "react";
import { ToolType } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import MCQWidget from "@/components/tools/MCQWidget";
import VoiceInterview from "@/components/tools/VoiceInterview";
import VideoWidget from "@/components/tools/VideoWidget";
import CodeEditor from "@/components/tools/CodeEditor";
import TaskWidget from "@/components/tools/TaskWidget";
import VisualizationWidget from "@/components/tools/VisualizationWidget";

interface Props {
  toolType: ToolType;
  payload: Record<string, unknown>;
  sessionId: string;
  onSubmit: (result: Record<string, unknown>) => void;
}

// Error boundary — prevents a widget crash from killing the whole chat
class WidgetErrorBoundary extends Component<{ children: ReactNode; onSubmit: (r: Record<string, unknown>) => void; t: (key: string, fallback?: string) => string }, { error: string | null }> {
  constructor(props: { children: ReactNode; onSubmit: (r: Record<string, unknown>) => void; t: (key: string, fallback?: string) => string }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(e: Error) { return { error: e.message }; }

  render() {
    if (this.state.error) {
      const { t } = this.props;
      return (
        <div className="bg-card border border-destructive/30 rounded-2xl p-5 flex flex-col gap-3">
          <p className="text-sm text-destructive font-medium">{t("cmp.toolRenderer.renderError", "This question could not be displayed.")}</p>
          <p className="text-xs text-muted-foreground">{this.state.error}</p>
          <button
            onClick={() => this.props.onSubmit({ error: "widget_render_failed", transcript: "" })}
            className="self-start px-4 py-2 rounded-xl border border-border text-sm hover:bg-secondary transition-colors"
          >
            {t("cmp.toolRenderer.skipContinue", "Skip and continue")}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function ToolSwitch({ toolType, payload, sessionId, onSubmit }: Props) {
  switch (toolType) {
    case "mcq":
      return <MCQWidget payload={payload} onSubmit={onSubmit} />;
    case "voice":
      return <VoiceInterview payload={payload} sessionId={sessionId} onSubmit={onSubmit} />;
    case "video":
      return <VideoWidget payload={payload} sessionId={sessionId} onSubmit={onSubmit} />;
    case "coding":
      return <CodeEditor payload={payload} onSubmit={onSubmit} />;
    case "task":
      return <TaskWidget payload={payload} sessionId={sessionId} onSubmit={onSubmit} />;
    case "visualization":
      return <VisualizationWidget payload={payload} onSubmit={onSubmit} />;
    default:
      return null;
  }
}

export default function ToolRenderer(props: Props) {
  const { t } = useI18n();
  return (
    <WidgetErrorBoundary onSubmit={props.onSubmit} t={t}>
      <ToolSwitch {...props} />
    </WidgetErrorBoundary>
  );
}
