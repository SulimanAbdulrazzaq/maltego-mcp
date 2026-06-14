"""Shared response-formatting helpers and error handling.

Every tool supports two output formats (see :class:`ResponseFormat`): a concise
human-readable ``markdown`` form (default) and a complete ``json`` form for
programmatic use. Centralizing formatting here keeps the tools DRY.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


def to_json(payload: Any) -> str:
    """Serialize ``payload`` to indented JSON."""

    return json.dumps(payload, indent=2, ensure_ascii=False)


def error(message: str) -> str:
    """Format an actionable error string."""

    return f"Error: {message}"


def handle_exception(exc: Exception) -> str:
    """Map common exceptions to clear, actionable messages."""

    from maltego_mcp.graph.graph_store import EntityNotFoundError

    if isinstance(exc, EntityNotFoundError):
        return error(
            f"Entity '{exc}' not found in the active graph. Use "
            "maltego_list_entities to see valid entity ids."
        )
    if isinstance(exc, FileNotFoundError):
        return error(f"File not found: {exc}.")
    if isinstance(exc, KeyError):
        return error(f"No graph named {exc}. Use maltego_list_graphs to see open graphs.")
    if isinstance(exc, (ValueError, RuntimeError)):
        return error(str(exc))
    return error(f"Unexpected {type(exc).__name__}: {exc}")


# --- markdown builders -------------------------------------------------------
def entity_line(entity: Dict[str, Any]) -> str:
    """One-line markdown summary of an entity dict (from Entity.to_dict)."""

    return f"- **{entity['value']}** ({entity['type']}) — id `{entity['id']}`"


def entities_markdown(title: str, entities: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    if meta:
        meta_str = " | ".join(f"{k}: {v}" for k, v in meta.items())
        lines.append(meta_str)
        lines.append("")
    if not entities:
        lines.append("_No entities._")
        return "\n".join(lines)
    for ent in entities:
        lines.append(entity_line(ent))
        if ent.get("properties"):
            for name, val in ent["properties"].items():
                lines.append(f"    - {name}: {val}")
        if ent.get("notes"):
            lines.append(f"    - _notes_: {ent['notes']}")
    return "\n".join(lines)


def graph_summary_markdown(summary: Dict[str, Any], is_active: bool = False) -> str:
    marker = " (active)" if is_active else ""
    return (
        f"- **{summary['name']}**{marker} — "
        f"{summary['entity_count']} entities, {summary['link_count']} links"
        + (f", saved at `{summary['source_path']}`" if summary.get("source_path") else "")
    )
