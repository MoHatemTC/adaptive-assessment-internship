"use client";

import { useState } from "react";
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import type { VisualizationPayload } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import MermaidDiagram from "./MermaidDiagram";

const COLORS = ["hsl(221,83%,53%)", "#34d399", "#fb923c", "#a78bfa", "#f472b6", "#38bdf8"];

interface Props {
  payload: Record<string, unknown>;
  onSubmit: (result: Record<string, unknown>) => void;
}

export default function VisualizationWidget({ payload, onSubmit }: Props) {
  const { t } = useI18n();
  const viz = payload as unknown as VisualizationPayload;
  const [answer, setAnswer] = useState("");
  const [showFollowUp, setShowFollowUp] = useState(false);

  const chartData = buildChartData(viz, t);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!answer.trim()) return;
    onSubmit({ answer_text: answer, tool_type: "visualization" });
  };

  return (
    <div className="border border-border rounded-2xl bg-card overflow-hidden flex flex-col gap-0">
      {/* Visual artifact — the generator picks the kind (chart / mermaid / svg / html) */}
      <div className="p-5 border-b border-border">
        {(viz.title || viz.chart_title) && (
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">{viz.title || viz.chart_title}</p>
        )}
        {renderArtifact(viz, chartData)}
      </div>

      {/* Question */}
      <div className="p-5 flex flex-col gap-4">
        <div className="bg-primary/5 border border-primary/20 rounded-xl p-4">
          <p className="text-sm font-medium text-foreground leading-relaxed">{viz.question}</p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onFocus={() => setShowFollowUp(true)}
            placeholder={t("tools.shareAnalysis")}
            rows={4}
            className="w-full border border-border rounded-xl px-4 py-3 text-sm bg-background focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
          />

          {showFollowUp && viz.follow_up && (
            <div className="text-xs text-muted-foreground bg-secondary/40 rounded-lg px-3 py-2 border border-border">
              <span className="font-medium">{t("tools.alsoConsider")}</span>{viz.follow_up}
            </div>
          )}

          <button
            type="submit"
            disabled={!answer.trim()}
            className="w-full py-3 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-40"
          >
            {t("tools.submitAnalysis")}
          </button>
        </form>
      </div>
    </div>
  );
}

function buildChartData(viz: VisualizationPayload, t: (key: string, fallback?: string) => string) {
  // LLM may return labels/datasets at the root level instead of nested in chart_data
  const raw = viz.chart_data ?? (viz as unknown as { labels?: string[]; datasets?: { label: string; data: number[] }[] });
  const labels: string[] = raw?.labels ?? [];
  const datasets: { label: string; data: number[] }[] = raw?.datasets ?? [];

  if (!labels.length) return [{ name: t("tools.noData"), value: 0 }];

  if (viz.chart_type === "pie") {
    return labels.map((label, i) => ({ name: label, value: datasets[0]?.data[i] ?? 0 }));
  }
  return labels.map((label, i) => {
    const point: Record<string, string | number> = { name: label };
    datasets.forEach((ds) => { point[ds.label] = ds.data[i] ?? 0; });
    return point;
  });
}

function renderArtifact(viz: VisualizationPayload, chartData: Record<string, string | number>[]) {
  const kind = viz.visual_kind ?? "chart";

  if (kind === "mermaid" && viz.mermaid) {
    return <div className="min-h-[8rem] py-2"><MermaidDiagram source={viz.mermaid} /></div>;
  }
  if (kind === "svg" && viz.svg) {
    // Render LLM SVG via a data-URI <img>: scripts inside SVG never execute in an <img> context.
    const src = "data:image/svg+xml;utf8," + encodeURIComponent(viz.svg);
    // eslint-disable-next-line @next/next/no-img-element
    return <div className="overflow-auto flex justify-center bg-white rounded-lg p-2"><img src={src} alt="" className="max-w-full h-auto" /></div>;
  }
  if (kind === "html" && viz.html) {
    // Sandboxed iframe (no allow-scripts) → untrusted HTML is fully isolated.
    return <iframe srcDoc={viz.html} sandbox="" title="artifact" className="w-full h-80 rounded-lg border border-border bg-white" />;
  }
  // default: data chart (Recharts)
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        {renderChart(viz.chart_type ?? "bar", chartData)}
      </ResponsiveContainer>
    </div>
  );
}

function renderChart(type: string, data: Record<string, string | number>[]) {
  const ds0Key = Object.keys(data[0] ?? {}).find((k) => k !== "name") ?? "value";

  switch (type) {
    case "bar":
      return (
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {Object.keys(data[0] ?? {}).filter((k) => k !== "name").map((k, i) => (
            <Bar key={k} dataKey={k} fill={COLORS[i % COLORS.length]} radius={[4, 4, 0, 0]} animationBegin={0} />
          ))}
        </BarChart>
      );

    case "line":
      return (
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {Object.keys(data[0] ?? {}).filter((k) => k !== "name").map((k, i) => (
            <Line key={k} type="monotone" dataKey={k} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={{ r: 4 }} animationBegin={0} />
          ))}
        </LineChart>
      );

    case "pie":
      return (
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} animationBegin={0}>
            {data.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      );

    default: // scatter
      return (
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis dataKey={ds0Key} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Scatter data={data} fill={COLORS[0]} animationBegin={0} />
        </ScatterChart>
      );
  }
}
