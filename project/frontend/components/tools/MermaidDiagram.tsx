"use client";

import { useEffect, useId, useState } from "react";

/** Renders a Mermaid diagram source string to inline SVG on the client.
 *  securityLevel "strict" makes Mermaid sanitize output and never run scripts.
 *  Falls back to showing the raw source if the diagram can't be parsed. */
export default function MermaidDiagram({ source }: { source: string }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);
  const rid = useId().replace(/[^a-zA-Z0-9]/g, "");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
        const { svg: out } = await mermaid.render("mmd" + rid, (source || "").trim());
        if (!cancelled) setSvg(out);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => { cancelled = true; };
  }, [source, rid]);

  if (failed) {
    return (
      <pre className="text-xs text-muted-foreground overflow-auto p-3 bg-secondary/40 rounded-lg whitespace-pre-wrap">
        {source}
      </pre>
    );
  }
  if (!svg) {
    return <div className="text-xs text-muted-foreground py-8 text-center">Rendering diagram…</div>;
  }
  return (
    <div
      className="flex justify-center overflow-auto [&_svg]:max-w-full [&_svg]:h-auto"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
