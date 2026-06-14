"""Deterministic investigation report generation (Markdown and HTML).

Reports are assembled purely from :mod:`maltego_mcp.analysis` output and the
graph itself, with stable ordering throughout, so the same graph always produces
byte-identical reports (reproducible and shareable).

A report contains:

* Executive summary -- size and composition of the investigation.
* Key findings -- the most-connected entities (pivots).
* Important entities -- a per-type inventory.
* Relationship highlights -- notable links.
* Suggested next steps -- concrete transforms to run next.
"""

from __future__ import annotations

import html
from typing import Dict, List

from maltego_mcp import analysis, entities as entity_catalog, scoring
from maltego_mcp.graph.graph_store import Graph


def _type_name(type_id: str) -> str:
    et = entity_catalog.get_entity_type(type_id)
    return et.display_name if et else type_id


def build_report_data(graph: Graph) -> Dict[str, object]:
    """Assemble the structured data backing a report (deterministic)."""

    summary = analysis.summarize_graph(graph, top=5)
    pivots = analysis.identify_interesting_pivots(graph, limit=5)
    next_steps = analysis.suggest_next_steps(graph, limit=8)
    ranked = scoring.rank_entities(graph, limit=5)

    # Relationship highlights: links involving the most-connected entities,
    # ordered deterministically by source then target id.
    pivot_ids = {p["id"] for p in pivots}
    highlights = []
    for link in sorted(graph.links, key=lambda l: (l.source_id, l.target_id)):
        if link.source_id in pivot_ids or link.target_id in pivot_ids:
            src = graph.get_entity(link.source_id)
            tgt = graph.get_entity(link.target_id)
            highlights.append(
                {
                    "source": src.value,
                    "target": tgt.value,
                    "label": link.label,
                }
            )
    return {
        "summary": summary,
        "pivots": pivots,
        "next_steps": next_steps,
        "highlights": highlights[:15],
        "ranked": ranked,
    }


def _exec_summary_sentence(summary: Dict[str, object]) -> str:
    types = summary["type_breakdown"]  # type: ignore[index]
    type_desc = ", ".join(f"{c} {_type_name(t)}" for t, c in types.items()) or "no"
    return (
        f"Investigation '{summary['name']}' contains {summary['entity_count']} "
        f"entities and {summary['link_count']} relationships ({type_desc})."
    )


def build_markdown_report(graph: Graph) -> str:
    """Return a Markdown investigation report."""

    data = build_report_data(graph)
    summary = data["summary"]  # type: ignore[index]
    lines: List[str] = []
    lines.append(f"# Investigation Report: {summary['name']}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(_exec_summary_sentence(summary))
    lines.append("")

    lines.append("## Key Findings")
    if data["pivots"]:
        for p in data["pivots"]:  # type: ignore[index]
            lines.append(
                f"- **{p['value']}** ({_type_name(p['type'])}) — {p['reason']}"
            )
    else:
        lines.append("- No strongly-connected pivot entities yet.")
    lines.append("")

    lines.append("## Important Entities")
    for type_id, count in summary["type_breakdown"].items():  # type: ignore[index]
        lines.append(f"### {_type_name(type_id)} ({count})")
        for e in sorted(graph.entities_of_type(type_id), key=lambda x: x.value):
            lines.append(f"- {e.value} (`{e.id}`)")
    lines.append("")

    lines.append("## Relationship Highlights")
    if data["highlights"]:
        for h in data["highlights"]:  # type: ignore[index]
            label = f" — {h['label']}" if h["label"] else ""
            lines.append(f"- {h['source']} → {h['target']}{label}")
    else:
        lines.append("- No relationships recorded.")
    lines.append("")

    lines.append("## Intelligence Quality (top by priority)")
    if data["ranked"]:
        for r in data["ranked"]:  # type: ignore[index]
            sc = r["scores"]
            lines.append(
                f"- **{r['value']}** ({_type_name(r['type'])}) — "
                f"priority {sc['investigation_priority']}, confidence {sc['confidence']}, "
                f"reliability {sc['source_reliability']}, novelty {sc['novelty']}"
            )
    else:
        lines.append("- No entities to score.")
    lines.append("")

    lines.append("## Suggested Next Steps")
    if data["next_steps"]:
        for s in data["next_steps"]:  # type: ignore[index]
            avail = "" if s["available"] else f" _(needs {s['note'].split()[-1]})_"
            lines.append(
                f"- Run `{s['transform']}` on **{s['on_value']}** "
                f"→ {', '.join(_type_name(t) for t in s['produces'])}{avail}"
            )
    else:
        lines.append("- No further automated steps available.")
    lines.append("")
    return "\n".join(lines)


def build_html_report(graph: Graph) -> str:
    """Return a self-contained HTML investigation report."""

    data = build_report_data(graph)
    summary = data["summary"]  # type: ignore[index]
    esc = html.escape

    parts: List[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append(f"<title>Investigation Report: {esc(str(summary['name']))}</title>")
    parts.append(
        "<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "max-width:860px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;line-height:1.5}"
        "h1{border-bottom:2px solid #444}h2{margin-top:1.8rem;color:#2a4d8f}"
        "code{background:#f0f0f0;padding:.1rem .3rem;border-radius:3px}"
        "li{margin:.2rem 0}.muted{color:#777}</style></head><body>"
    )
    parts.append(f"<h1>Investigation Report: {esc(str(summary['name']))}</h1>")

    parts.append("<h2>Executive Summary</h2>")
    parts.append(f"<p>{esc(_exec_summary_sentence(summary))}</p>")

    parts.append("<h2>Key Findings</h2><ul>")
    if data["pivots"]:
        for p in data["pivots"]:  # type: ignore[index]
            parts.append(
                f"<li><strong>{esc(str(p['value']))}</strong> "
                f"({esc(_type_name(str(p['type'])))}) — {esc(str(p['reason']))}</li>"
            )
    else:
        parts.append("<li>No strongly-connected pivot entities yet.</li>")
    parts.append("</ul>")

    parts.append("<h2>Important Entities</h2>")
    for type_id, count in summary["type_breakdown"].items():  # type: ignore[index]
        parts.append(f"<h3>{esc(_type_name(type_id))} ({count})</h3><ul>")
        for e in sorted(graph.entities_of_type(type_id), key=lambda x: x.value):
            parts.append(f"<li>{esc(e.value)} <code>{esc(e.id)}</code></li>")
        parts.append("</ul>")

    parts.append("<h2>Relationship Highlights</h2><ul>")
    if data["highlights"]:
        for h in data["highlights"]:  # type: ignore[index]
            label = f" — {esc(str(h['label']))}" if h["label"] else ""
            parts.append(f"<li>{esc(str(h['source']))} → {esc(str(h['target']))}{label}</li>")
    else:
        parts.append("<li>No relationships recorded.</li>")
    parts.append("</ul>")

    parts.append("<h2>Intelligence Quality (top by priority)</h2><ul>")
    if data["ranked"]:
        for r in data["ranked"]:  # type: ignore[index]
            sc = r["scores"]
            parts.append(
                f"<li><strong>{esc(str(r['value']))}</strong> "
                f"({esc(_type_name(str(r['type'])))}) — priority "
                f"{sc['investigation_priority']}, confidence {sc['confidence']}, "
                f"reliability {sc['source_reliability']}, novelty {sc['novelty']}</li>"
            )
    else:
        parts.append("<li>No entities to score.</li>")
    parts.append("</ul>")

    parts.append("<h2>Suggested Next Steps</h2><ul>")
    if data["next_steps"]:
        for s in data["next_steps"]:  # type: ignore[index]
            avail = "" if s["available"] else f' <span class="muted">(needs {esc(s["note"].split()[-1])})</span>'
            produces = ", ".join(_type_name(t) for t in s["produces"])
            parts.append(
                f"<li>Run <code>{esc(str(s['transform']))}</code> on "
                f"<strong>{esc(str(s['on_value']))}</strong> → {esc(produces)}{avail}</li>"
            )
    else:
        parts.append("<li>No further automated steps available.</li>")
    parts.append("</ul>")

    parts.append("</body></html>")
    return "\n".join(parts)
