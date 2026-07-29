"""Realtime competency DAG rendering for the Streamlit tester.

The primary renderer produces self-contained SVG, avoiding Graphviz, JavaScript,
and CDN dependencies. A Mermaid renderer remains available for diagnostics.
"""

from __future__ import annotations

from html import escape
import re

import streamlit as st

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.graph import CompetencyGraphService


def graph_service() -> CompetencyGraphService:
    return CompetencyGraphService(load_default_competency_graph())


def related_subgraph(
    service: CompetencyGraphService,
    focus_nodes: set[str],
) -> tuple[set[str], list[tuple[str, str, str]]]:
    """Nodes and edges (from, to, relation) around focus: ancestors, descendants, mains."""
    nodes: set[str] = set()
    for nid in focus_nodes:
        if nid not in service.graph.nodes:
            continue
        nodes.add(nid)
        nodes |= service.ancestors(nid, include_self=False)
        nodes |= service.descendants(nid, include_self=False)
        for main in service.mains_for_node(nid):
            nodes.add(main)

    edges: list[tuple[str, str, str]] = []
    for edge in service.graph.edges:
        if edge.from_id in nodes and edge.to_id in nodes:
            edges.append((edge.from_id, edge.to_id, edge.relation))
    return nodes, edges


def _node_style(
    nid: str,
    *,
    focus: set[str],
    path: set[str],
    blocked: set[str],
    mastered: set[str],
    contradicted: set[str],
    mains: set[str],
) -> str:
    if nid in contradicted:
        return "fill:#f8d7da,stroke:#842029,stroke-width:3px,color:#000"
    if nid in blocked:
        return "fill:#fff3cd,stroke:#664d03,stroke-width:2px,color:#000"
    if nid in mastered:
        return "fill:#d1e7dd,stroke:#0f5132,stroke-width:2px,color:#000"
    if nid in focus:
        return "fill:#cfe2ff,stroke:#084298,stroke-width:3px,color:#000"
    if nid in path:
        return "fill:#e2e3ff,stroke:#3d3d8f,stroke-width:2px,color:#000"
    if nid in mains:
        return "fill:#f8f9fa,stroke:#212529,stroke-width:2px,color:#000"
    return "fill:#ffffff,stroke:#6c757d,stroke-width:1px,color:#000"


def _mermaid_id(node_id: str) -> str:
    """Return an identifier accepted by Mermaid's flowchart grammar.

    Competency IDs deliberately contain dots (``PY.1``), but dots are syntax in
    Mermaid rather than valid bare node-id characters. Keep the real competency ID
    in the visible label and use a stable, sanitized internal identifier.
    """
    return "node_" + re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def mermaid_for_subgraph(
    service: CompetencyGraphService,
    *,
    focus_nodes: set[str],
    blocked: set[str] | None = None,
    mastered: set[str] | None = None,
    contradicted: set[str] | None = None,
) -> str:
    blocked = blocked or set()
    mastered = mastered or set()
    contradicted = contradicted or set()

    nodes, edges = related_subgraph(service, focus_nodes)
    if not nodes:
        # Fall back to full graph when nothing in session maps yet.
        nodes = set(service.graph.nodes)
        edges = [(e.from_id, e.to_id, e.relation) for e in service.graph.edges]

    path: set[str] = set()
    for nid in focus_nodes:
        path |= service.ancestors(nid, include_self=True)
        path |= service.descendants(nid, include_self=True)

    mains = {nid for nid, n in service.graph.nodes.items() if n.node_type == "main"}

    # Keep syntax deliberately conservative. This is rendered by a CDN Mermaid build,
    # and edge-label combinations accepted by newer parsers can fail as "syntax error in
    # text" on older cached builds.
    lines = ["graph TD"]
    for nid in sorted(nodes):
        mermaid_id = _mermaid_id(nid)
        title = service.graph.nodes[nid].title if nid in service.graph.nodes else nid
        label = f"{nid} - {title}" if title != nid else nid
        safe = label.replace("&", "and").replace('"', "'")
        lines.append(f'  {mermaid_id}["{safe}"]')

    for frm, to, relation in edges:
        mermaid_from = _mermaid_id(frm)
        mermaid_to = _mermaid_id(to)
        if relation == "PREREQUISITE":
            lines.append(f"  {mermaid_from} --> {mermaid_to}")
        elif relation == "CONTRIBUTES_TO":
            lines.append(f"  {mermaid_from} -.-> {mermaid_to}")
        else:
            lines.append(f"  {mermaid_from} --- {mermaid_to}")

    for nid in sorted(nodes):
        style = _node_style(
            nid,
            focus=focus_nodes,
            path=path,
            blocked=blocked,
            mastered=mastered,
            contradicted=contradicted,
            mains=mains,
        )
        lines.append(f"  style {_mermaid_id(nid)} {style}")

    return "\n".join(lines)


def svg_for_subgraph(
    service: CompetencyGraphService,
    *,
    focus_nodes: set[str],
    blocked: set[str] | None = None,
    mastered: set[str] | None = None,
    not_mastered: set[str] | None = None,
    contradicted: set[str] | None = None,
) -> str:
    """Render a dependency graph as self-contained SVG.

    This intentionally has no JavaScript or CDN dependency. Streamlit can render the SVG
    directly, so corporate content filters and Mermaid parser/version differences cannot
    turn the assessment graph into a blank iframe or a syntax-error message.
    """
    blocked = blocked or set()
    mastered = mastered or set()
    not_mastered = not_mastered or set()
    contradicted = contradicted or set()
    nodes, edges = related_subgraph(service, focus_nodes)
    if not nodes:
        nodes = set(service.graph.nodes)
        edges = [(e.from_id, e.to_id, e.relation) for e in service.graph.edges]

    mains = {nid for nid in nodes if service.graph.nodes[nid].node_type == "main"}
    prerequisite_edges = [
        (frm, to) for frm, to, relation in edges if relation == "PREREQUISITE"
    ]

    # Longest prerequisite distance gives a stable left-to-right topological layout.
    level = {nid: 0 for nid in nodes if nid not in mains}
    for _ in range(max(len(nodes), 1)):
        changed = False
        for frm, to in prerequisite_edges:
            candidate = level.get(frm, 0) + 1
            if candidate > level.get(to, 0):
                level[to] = candidate
                changed = True
        if not changed:
            break
    main_level = max(level.values(), default=0) + 1
    for nid in mains:
        level[nid] = main_level

    columns: dict[int, list[str]] = {}
    for nid in sorted(nodes):
        columns.setdefault(level.get(nid, 0), []).append(nid)

    box_w, box_h = 210, 66
    x_gap, y_gap = 70, 34
    margin_x, margin_y = 30, 52
    max_rows = max((len(column) for column in columns.values()), default=1)
    width = max(
        760,
        margin_x * 2 + (max(columns, default=0) + 1) * box_w
        + max(max(columns, default=0), 0) * x_gap,
    )
    height = margin_y * 2 + max_rows * box_h + max(max_rows - 1, 0) * y_gap

    positions: dict[str, tuple[float, float]] = {}
    for col, column_nodes in columns.items():
        column_height = len(column_nodes) * box_h + max(len(column_nodes) - 1, 0) * y_gap
        top = (height - column_height) / 2
        for row, nid in enumerate(column_nodes):
            positions[nid] = (
                margin_x + col * (box_w + x_gap),
                top + row * (box_h + y_gap),
            )

    def colors(nid: str) -> tuple[str, str, int]:
        # Fill encodes persisted state; focus is rendered as a blue stroke halo
        # that must remain visible even when the node is mastered/blocked.
        fill = "#ffffff"
        stroke = "#6c757d"
        stroke_width = 2

        if nid in contradicted:
            fill, stroke, stroke_width = "#f8d7da", "#842029", 3
        elif nid in not_mastered:
            fill, stroke, stroke_width = "#fde2e1", "#b42318", 3
        elif nid in blocked:
            fill, stroke, stroke_width = "#fff3cd", "#664d03", 3
        elif nid in mastered:
            fill, stroke, stroke_width = "#d1e7dd", "#0f5132", 3
        elif nid in mains:
            fill, stroke, stroke_width = "#f8f9fa", "#212529", 3
        elif nid in focus_nodes:
            # focus-only nodes (not mastered/blocked/contradicted): keep existing
            # purple-blue fill for readability.
            fill, stroke, stroke_width = "#cfe2ff", "#084298", 3

        if nid in focus_nodes:
            stroke, stroke_width = "#084298", 4

        return fill, stroke, stroke_width

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'height="{min(max(height, 420), 760)}" role="img" '
        'aria-label="Competency dependency graph" xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#56606a"/></marker>',
        "</defs>",
        '<rect width="100%" height="100%" fill="#fafafa" rx="10"/>',
        '<g aria-label="Graph legend" font-family="system-ui, sans-serif" '
        'font-size="11" fill="#333">',
        '<rect x="22" y="16" width="13" height="13" rx="2" fill="#cfe2ff" '
        'stroke="#084298" stroke-width="2"/><text x="41" y="27">focus</text>',
        '<rect x="88" y="16" width="13" height="13" rx="2" fill="#d1e7dd" '
        'stroke="#0f5132"/><text x="107" y="27">mastered</text>',
        '<rect x="181" y="16" width="13" height="13" rx="2" fill="#fff3cd" '
        'stroke="#664d03"/><text x="200" y="27">blocked</text>',
        '<rect x="257" y="16" width="13" height="13" rx="2" fill="#f8d7da" '
        'stroke="#842029"/><text x="276" y="27">contradicted</text>',
        '<line x1="366" y1="22" x2="397" y2="22" stroke="#56606a" stroke-width="2"/>'
        '<text x="403" y="27">prerequisite</text>',
        '<line x1="484" y1="22" x2="515" y2="22" stroke="#56606a" stroke-width="2" '
        'stroke-dasharray="7 5"/><text x="521" y="27">contributes to</text>',
        '<rect x="620" y="16" width="13" height="13" rx="2" fill="#fde2e1" '
        'stroke="#b42318"/><text x="639" y="27">not mastered</text>',
        "</g>",
    ]
    for frm, to, relation in edges:
        if frm not in positions or to not in positions:
            continue
        x1, y1 = positions[frm]
        x2, y2 = positions[to]
        dashed = ' stroke-dasharray="7 5"' if relation == "CONTRIBUTES_TO" else ""
        parts.append(
            f'<line x1="{x1 + box_w}" y1="{y1 + box_h / 2}" x2="{x2}" '
            f'y2="{y2 + box_h / 2}" stroke="#56606a" stroke-width="2"{dashed} '
            'marker-end="url(#arrow)"/>'
        )

    for nid in sorted(nodes):
        x, y = positions[nid]
        fill, stroke, stroke_width = colors(nid)
        node = service.graph.nodes[nid]
        title = node.title
        if len(title) > 29:
            title = title[:27].rstrip() + "…"
        parts.extend(
            [
                f'<g><title>{escape(nid)}: {escape(node.title)}</title>',
                f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="10" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>',
                f'<text x="{x + 12}" y="{y + 25}" font-family="system-ui, sans-serif" '
                f'font-size="16" font-weight="700" fill="#111">{escape(nid)}</text>',
                f'<text x="{x + 12}" y="{y + 48}" font-family="system-ui, sans-serif" '
                f'font-size="12" fill="#333">{escape(title)}</text></g>',
            ]
        )
    parts.append("</svg>")
    return "".join(parts)


def render_svg(diagram: str, *, height: int = 700) -> None:
    """Render SVG in an isolated, scrollable frame.

    ``st.html`` sanitizes SVG differently across Streamlit/browser versions and can
    leave a blank element. The component iframe renders the same self-contained SVG
    without a CDN and gives wide DAGs a horizontal scrollbar instead of shrinking node
    labels until they are unreadable.
    """
    document = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{ margin: 0; padding: 0; background: #fafafa; }}
    .dag-canvas {{
      box-sizing: border-box;
      width: 100%;
      min-height: {height - 8}px;
      overflow: auto;
      border: 1px solid #d7dce1;
      border-radius: 10px;
      background: #fafafa;
    }}
    .dag-canvas svg {{
      display: block;
      min-width: 900px;
      height: auto;
      margin: 0;
    }}
  </style>
</head>
<body>
  <div class="dag-canvas">{diagram}</div>
</body>
</html>
"""
    st.iframe(document, height=height, width="stretch")


def render_mermaid(diagram: str, *, height: int = 520) -> None:
    """Embed Mermaid via CDN inside Streamlit."""
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    body {{ margin: 0; background: #fafafa; font-family: system-ui, sans-serif; }}
    .legend {{ font-size: 12px; color: #333; padding: 8px 12px; }}
    .legend span {{ display: inline-block; margin-right: 12px; }}
    .swatch {{ width: 12px; height: 12px; display: inline-block; border: 1px solid #333;
               margin-right: 4px; vertical-align: middle; }}
  </style>
</head>
<body>
  <div class="legend">
    <span><i class="swatch" style="background:#cfe2ff"></i>focus</span>
    <span><i class="swatch" style="background:#e2e3ff"></i>path</span>
    <span><i class="swatch" style="background:#d1e7dd"></i>mastered</span>
    <span><i class="swatch" style="background:#fff3cd"></i>blocked</span>
    <span><i class="swatch" style="background:#f8d7da"></i>contradicted</span>
    <span><i class="swatch" style="background:#f8f9fa"></i>main</span>
  </div>
  <div class="mermaid">
{escape(diagram)}
  </div>
  <script>
    mermaid.initialize({{ startOnLoad: true, securityLevel: 'loose', theme: 'neutral' }});
  </script>
</body>
</html>
"""
    # Mermaid source must not be HTML-escaped inside the diagram div.
    html = html.replace(escape(diagram), diagram)
    st.iframe(html, height=height, width="stretch")
