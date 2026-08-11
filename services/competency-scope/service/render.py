"""Drawing an induced sub-graph, as mermaid.

WHY THE SERVICE RENDERS THIS AT ALL

A scope is a graph, and the question an author actually asks about one is "what did I just
select" — which is a picture. Rendering it beside the manifest means the drawing and the
allowlist can never disagree, whereas a client that redrew the graph from the manifest would
be a second implementation of the induction rules.

Deliberately mermaid text rather than an image: it renders in a pull request, in the docs,
and in every markdown viewer, and it diffs.
"""

from __future__ import annotations

from adaptive_contracts import ScopeManifest

#: Mermaid parses `-` in a node id as an operator, and bank taxonomies use dots. Both are
#: replaced for the ID; the ORIGINAL is always what the label shows.
_SAFE = str.maketrans({".": "_", "-": "_", " ": "_"})


def _node_id(node_id: str) -> str:
    return f"n_{node_id.translate(_SAFE)}"


def render_mermaid(manifest: ScopeManifest) -> str:
    """The retained sub-graph, with the excluded siblings shown greyed beside it.

    The excluded nodes earn their place: a scope is a statement about what is NOT being
    measured at least as much as about what is, and a picture showing only the retained
    nodes makes a two-of-seven selection look like a whole competency.
    """
    lines = [
        "flowchart BT",
        f"    %% scope {manifest.scope_id} over {manifest.bank_id}@{manifest.bank_version}",
    ]
    by_id = {node.node_id: node for node in manifest.nodes}

    for node in manifest.nodes:
        if node.node_type == "main":
            row = next((m for m in manifest.mains if m.main == node.node_id), None)
            suffix = ""
            if row is not None and row.partial:
                suffix = f"<br/>PARTIAL · retained {row.retained_weight}"
            if row is not None and row.implied:
                suffix += "<br/>implied by a shared node"
            lines.append(f'    {_node_id(node.node_id)}(("{node.node_id}{suffix}"))')
        else:
            flag = " · critical" if node.critical else ""
            lines.append(
                f'    {_node_id(node.node_id)}["{node.node_id}{flag}'
                f'<br/>{node.item_count} items"]'
            )

    # Excluded siblings, so the omission is visible rather than merely absent.
    excluded = sorted({e for row in manifest.mains for e in row.excluded})
    for node_id in excluded:
        lines.append(f'    x_{node_id.translate(_SAFE)}["{node_id} — excluded"]')

    for edge in manifest.edges:
        tail, head = _node_id(edge.from_id), _node_id(edge.to_id)
        if edge.relation == "CONTRIBUTES_TO":
            label = f"{edge.weight}"
            if abs(edge.weight - edge.weight_original) > 1e-9:
                label = f"{edge.weight_original} → {edge.weight}"
            lines.append(f'    {tail} -.->|"{label}"| {head}')
        elif edge.relation == "PREREQUISITE":
            lines.append(f'    {tail} ==>|"{edge.strength}"| {head}')
        else:
            lines.append(f'    {tail} --->|"{edge.relation}"| {head}')

    for dangling in manifest.dangling_prerequisites:
        inside = dangling.from_id if dangling.direction == "leaves" else dangling.to_id
        outside = dangling.to_id if dangling.direction == "leaves" else dangling.from_id
        if inside not in by_id:
            continue
        lines.append(
            f'    {_node_id(inside)} -. "prerequisite {dangling.direction} the scope" '
            f'.-> x_{outside.translate(_SAFE)}'
        )

    lines += [
        "    classDef main fill:#dfe8f5,stroke:#3f5f8f,color:#16243a",
        "    classDef kept fill:#e6f2ea,stroke:#3f7a58,color:#153025",
        "    classDef dropped fill:#f2f2f2,stroke:#bbbbbb,color:#888888",
    ]
    mains = [_node_id(m.main) for m in manifest.mains if m.main in by_id]
    kept = [
        _node_id(n.node_id) for n in manifest.nodes if n.node_type != "main"
    ]
    dropped = [f"x_{e.translate(_SAFE)}" for e in excluded]
    if mains:
        lines.append(f"    class {','.join(mains)} main")
    if kept:
        lines.append(f"    class {','.join(kept)} kept")
    if dropped:
        lines.append(f"    class {','.join(dropped)} dropped")
    return "\n".join(lines)
