"""Realtime competency DAG rendering for the Streamlit tester.

The primary renderer produces self-contained SVG, avoiding Graphviz, JavaScript,
and CDN dependencies. A Mermaid renderer remains available for diagnostics.
"""

from __future__ import annotations

from html import escape
import re

import streamlit as st

from app.services.competency_graph.graph import CompetencyGraphService
from app.services.orchestrator import registry

# Right edge of the fixed legend row, plus a margin.
LEGEND_WIDTH = 1200


def graph_service(bank_id: str | None = None) -> CompetencyGraphService | None:
    """The graph paired with `bank_id`. None when that bank declares none."""
    return registry.get_graph_service(bank_id)


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


def _prerequisite_order(
    service: CompetencyGraphService, members: list[str], edges: list[tuple[str, str, str]]
) -> list[str]:
    """Members sorted by longest prerequisite depth, then by id.

    Depth first so a chain reads left to right in teaching order; id second so the layout
    is stable across renders and two sessions of the same graph look the same.
    """
    member_set = set(members)
    depth = {nid: 0 for nid in members}
    prerequisites = [
        (frm, to)
        for frm, to, relation in edges
        if relation == "PREREQUISITE" and frm in member_set and to in member_set
    ]
    for _ in range(len(members)):
        changed = False
        for frm, to in prerequisites:
            if depth[frm] + 1 > depth[to]:
                depth[to] = depth[frm] + 1
                changed = True
        if not changed:
            break
    return sorted(members, key=lambda nid: (depth[nid], nid))


def _bands(
    service: CompetencyGraphService,
    nodes: set[str],
    edges: list[tuple[str, str, str]],
) -> list[tuple[str | None, list[str]]]:
    """Group sub-competencies under the main they contribute to.

    ONE LANE PER MAIN, rather than one global column per prerequisite depth. A bank whose
    sub-competencies form a chain — which is what an authored numbering order produces —
    puts every node in its own depth, so a depth-major layout renders sixteen columns of
    one node each, nearly five thousand pixels wide and unreadable. Competencies are what
    a reader is looking for; depth is a detail within one.
    """
    mains = [nid for nid in sorted(nodes) if service.graph.nodes[nid].node_type == "main"]
    by_main: dict[str | None, list[str]] = {main: [] for main in mains}
    orphans: list[str] = []

    for nid in sorted(nodes):
        if nid in by_main:
            continue
        owners = [m for m in service.mains_for_node(nid) if m in by_main]
        if not owners and "." in nid and nid.split(".", 1)[0] in by_main:
            owners = [nid.split(".", 1)[0]]
        if owners:
            by_main[owners[0]].append(nid)
        else:
            orphans.append(nid)

    bands: list[tuple[str | None, list[str]]] = [
        (main, _prerequisite_order(service, members, edges))
        for main, members in by_main.items()
    ]
    if orphans:
        bands.append((None, _prerequisite_order(service, orphans, edges)))
    return bands


def svg_for_subgraph(
    service: CompetencyGraphService,
    *,
    focus_nodes: set[str],
    blocked: set[str] | None = None,
    mastered: set[str] | None = None,
    not_mastered: set[str] | None = None,
    inferred: set[str] | None = None,
    preview_inferred: set[str] | None = None,
    preview_blocked: set[str] | None = None,
    contradicted: set[str] | None = None,
    max_columns: int = 6,
) -> str:
    """Render a dependency graph as self-contained SVG.

    This intentionally has no JavaScript or CDN dependency. Streamlit can render the SVG
    directly, so corporate content filters and Mermaid parser/version differences cannot
    turn the assessment graph into a blank iframe or a syntax-error message.

    Laid out as one band per main competency, wrapping at `max_columns`, so the width
    stays bounded however many sub-competencies a bank declares.
    """
    blocked = blocked or set()
    mastered = mastered or set()
    not_mastered = not_mastered or set()
    inferred = (inferred or set()) - mastered - not_mastered
    contradicted = contradicted or set()
    # A forecast from edges nobody has validated. It ranks below every measured state, so
    # a node the session actually asked about is never painted as a guess — and it is
    # drawn dashed, because the one thing a reader must not do is read it as a result.
    preview_inferred = (preview_inferred or set()) - mastered - not_mastered - inferred
    preview_blocked = (preview_blocked or set()) - mastered - not_mastered - blocked
    nodes, edges = related_subgraph(service, focus_nodes)
    if not nodes:
        nodes = set(service.graph.nodes)
        edges = [(e.from_id, e.to_id, e.relation) for e in service.graph.edges]

    mains = {nid for nid in nodes if service.graph.nodes[nid].node_type == "main"}
    bands = _bands(service, nodes, edges)

    box_w, box_h = 196, 62
    x_gap, y_gap = 44, 26
    margin_x, margin_y = 26, 56
    band_gap = 30
    main_column_w = box_w + 56

    columns = max(1, min(max_columns, max((len(members) for _main, members in bands), default=1)))
    width = margin_x * 2 + main_column_w + columns * box_w + (columns - 1) * x_gap
    # The legend is a fixed-width row. A graph narrower than it would clip its own key.
    width = max(width, LEGEND_WIDTH)

    positions: dict[str, tuple[float, float]] = {}
    band_extents: list[tuple[str | None, float, float]] = []
    y = margin_y
    for main, members in bands:
        rows = max(1, -(-len(members) // columns))  # ceiling division
        band_h = rows * box_h + (rows - 1) * y_gap
        if main is not None:
            positions[main] = (margin_x, y + (band_h - box_h) / 2)
        for index, nid in enumerate(members):
            row, col = divmod(index, columns)
            positions[nid] = (
                margin_x + main_column_w + col * (box_w + x_gap),
                y + row * (box_h + y_gap),
            )
        band_extents.append((main, y - 10, band_h + 20))
        y += band_h + band_gap
    height = y - band_gap + margin_y

    def colors(nid: str) -> tuple[str, str, int, str]:
        # Fill encodes persisted state; focus is rendered as a blue stroke halo
        # that must remain visible even when the node is mastered/blocked.
        fill = "#ffffff"
        stroke = "#6c757d"
        stroke_width = 2
        dash = ""

        if nid in contradicted:
            fill, stroke, stroke_width = "#f8d7da", "#842029", 3
        elif nid in not_mastered:
            fill, stroke, stroke_width = "#fde2e1", "#b42318", 3
        elif nid in blocked:
            fill, stroke, stroke_width = "#fff3cd", "#664d03", 3
        elif nid in mastered:
            fill, stroke, stroke_width = "#d1e7dd", "#0f5132", 3
        elif nid in inferred:
            # Deliberately NOT the mastered colour. An inference is a deduction from
            # another answer, it never entered the estimate, and an assessor reading this
            # graph must be able to tell the two apart at a glance.
            fill, stroke, stroke_width = "#e7f0e9", "#0f5132", 2
        elif nid in preview_blocked:
            fill, stroke, stroke_width, dash = "#fffaf0", "#664d03", 2, "5 4"
        elif nid in preview_inferred:
            fill, stroke, stroke_width, dash = "#f2f7f3", "#4b7a5c", 2, "5 4"
        elif nid in mains:
            fill, stroke, stroke_width = "#f8f9fa", "#212529", 3
        elif nid in focus_nodes:
            fill, stroke, stroke_width = "#cfe2ff", "#084298", 3

        if nid in focus_nodes:
            stroke, stroke_width = "#084298", 4

        return fill, stroke, stroke_width, dash

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'height="{min(max(height, 420), 900)}" role="img" '
        'aria-label="Competency dependency graph" xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#56606a"/></marker>',
        "</defs>",
        f'<rect width="{width}" height="{height}" fill="#fafafa" rx="10"/>',
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
        '<rect x="717" y="16" width="13" height="13" rx="2" fill="#e7f0e9" '
        'stroke="#0f5132" stroke-dasharray="3 2"/>'
        '<text x="736" y="27">inferred (never measured)</text>',
        '<rect x="890" y="16" width="13" height="13" rx="2" fill="#f2f7f3" '
        'stroke="#4b7a5c" stroke-width="2" stroke-dasharray="5 4"/>'
        '<text x="909" y="27">would be inferred (unvalidated edge)</text>',
        "</g>",
    ]

    # Band backgrounds first, so every edge and node draws on top of them.
    for main, band_y, band_h in band_extents:
        if main is None:
            continue
        parts.append(
            f'<rect x="{margin_x - 12}" y="{band_y}" width="{width - 2 * margin_x + 24}" '
            f'height="{band_h}" rx="12" fill="#ffffff" stroke="#e6e9ec"/>'
        )

    for frm, to, relation in edges:
        if frm not in positions or to not in positions:
            continue
        x1, y1 = positions[frm]
        x2, y2 = positions[to]
        dashed = ' stroke-dasharray="7 5"' if relation == "CONTRIBUTES_TO" else ""
        # Same row: edge along the row. Different row (a wrap, or a contribution to the
        # main on the left): centre to centre, which stays readable without a router.
        if abs(y1 - y2) < 1 and x2 > x1:
            start_x, start_y, end_x, end_y = x1 + box_w, y1 + box_h / 2, x2, y2 + box_h / 2
        else:
            start_x, start_y = x1 + box_w / 2, y1 + box_h / 2
            end_x, end_y = x2 + box_w / 2, y2 + box_h / 2
        parts.append(
            f'<line x1="{start_x:.1f}" y1="{start_y:.1f}" x2="{end_x:.1f}" '
            f'y2="{end_y:.1f}" stroke="#56606a" stroke-width="2"{dashed} '
            'marker-end="url(#arrow)" opacity="0.55"/>'
        )

    for nid in sorted(nodes):
        if nid not in positions:
            continue
        x, y_pos = positions[nid]
        fill, stroke, stroke_width, dash = colors(nid)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        node = service.graph.nodes[nid]
        title = node.title
        if len(title) > 26:
            title = title[:24].rstrip() + "…"
        parts.extend(
            [
                f'<g><title>{escape(nid)}: {escape(node.title)}</title>',
                f'<rect x="{x}" y="{y_pos}" width="{box_w}" height="{box_h}" rx="10" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"'
                f"{dash_attr}/>",
                f'<text x="{x + 11}" y="{y_pos + 24}" font-family="system-ui, sans-serif" '
                f'font-size="15" font-weight="700" fill="#111">{escape(nid)}</text>',
                f'<text x="{x + 11}" y="{y_pos + 45}" font-family="system-ui, sans-serif" '
                f'font-size="11" fill="#333">{escape(title)}</text></g>',
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
