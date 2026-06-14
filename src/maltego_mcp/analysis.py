"""Deterministic graph analysis for the "Maltego Copilot" experience.

These functions compute structured, reproducible facts about an investigation
graph that an LLM can narrate to the user. They are intentionally deterministic
(stable ordering, no randomness, no network) so that the same graph always
yields the same analysis -- important for reproducible reports and evaluations.

Nothing here mutates the graph.
"""

from __future__ import annotations

from typing import Dict, List

from maltego_mcp import entities as entity_catalog
from maltego_mcp.graph.graph_store import Entity, Graph
from maltego_mcp.transforms import registry


def _entity_label(entity: Entity) -> str:
    display = entity_catalog.get_entity_type(entity.type_id)
    type_name = display.display_name if display else entity.type_id
    return f"{entity.value} ({type_name})"


def _sorted_by_degree(graph: Graph) -> List[Entity]:
    """Entities sorted by degree desc, then id asc (deterministic tie-break)."""

    def id_key(e: Entity):
        return int(e.id[1:]) if e.id.startswith("n") and e.id[1:].isdigit() else 0

    return sorted(graph.entities, key=lambda e: (-graph.degree(e.id), id_key(e)))


def summarize_graph(graph: Graph, top: int = 5) -> Dict[str, object]:
    """Return a structured summary of the whole graph.

    Schema::

        {
          "name": str,
          "entity_count": int,
          "link_count": int,
          "type_breakdown": {type_id: count, ...},   # sorted by type id
          "most_connected": [ {id, value, type, degree}, ... ],  # up to `top`
          "isolated_count": int,
          "isolated": [ {id, value, type}, ... ],     # up to `top`
        }
    """

    most = _sorted_by_degree(graph)[:top]
    isolated = graph.isolated_entities()
    return {
        "name": graph.name,
        "entity_count": graph.entity_count(),
        "link_count": graph.link_count(),
        "type_breakdown": graph.type_counts(),
        "most_connected": [
            {"id": e.id, "value": e.value, "type": e.type_id, "degree": graph.degree(e.id)}
            for e in most
            if graph.degree(e.id) > 0
        ],
        "isolated_count": len(isolated),
        "isolated": [
            {"id": e.id, "value": e.value, "type": e.type_id} for e in isolated[:top]
        ],
    }


def explain_entity(graph: Graph, entity_id: str) -> Dict[str, object]:
    """Explain one entity: its data, neighbours, and how it could be expanded.

    Raises ``EntityNotFoundError`` (from the graph) if the id is unknown.
    """

    entity = graph.get_entity(entity_id)
    neighbors = graph.neighbors(entity_id)
    out_neighbors = [
        {"id": nb.id, "value": nb.value, "type": nb.type_id, "label": link.label}
        for nb, link, direction in neighbors
        if direction == "out"
    ]
    in_neighbors = [
        {"id": nb.id, "value": nb.value, "type": nb.type_id, "label": link.label}
        for nb, link, direction in neighbors
        if direction == "in"
    ]
    applicable = registry.for_type(entity.type_id)
    return {
        "id": entity.id,
        "value": entity.value,
        "type": entity.type_id,
        "properties": dict(entity.properties),
        "notes": entity.notes,
        "degree": graph.degree(entity_id),
        "outgoing": out_neighbors,
        "incoming": in_neighbors,
        "applicable_transforms": [
            {"name": t.name, "available": t.is_available(), "provider": t.provider}
            for t in sorted(applicable, key=lambda x: x.name)
        ],
    }


def identify_interesting_pivots(graph: Graph, limit: int = 10) -> List[Dict[str, object]]:
    """Rank entities that are promising pivot points.

    A pivot is an entity that connects many others (high degree) -- e.g. a
    shared IP that several domains resolve to, or an email tied to multiple
    accounts. Ranking is deterministic: degree desc, then id asc. Only entities
    with degree >= 2 are considered interesting.
    """

    ranked = []
    for entity in _sorted_by_degree(graph):
        degree = graph.degree(entity.id)
        if degree < 2:
            continue
        # Count distinct neighbour types as a "diversity" signal.
        neighbor_types = {nb.type_id for nb, _link, _dir in graph.neighbors(entity.id)}
        ranked.append(
            {
                "id": entity.id,
                "value": entity.value,
                "type": entity.type_id,
                "degree": degree,
                "neighbor_type_diversity": len(neighbor_types),
                "reason": (
                    f"Connected to {degree} entities across "
                    f"{len(neighbor_types)} type(s)."
                ),
            }
        )
        if len(ranked) >= limit:
            break
    return ranked


def suggest_next_steps(graph: Graph, limit: int = 10) -> List[Dict[str, object]]:
    """Suggest concrete next transforms to run, grouped by entity.

    For each entity type present, finds registered transforms that accept it and
    have not yet been "exhausted" (heuristic: suggest available transforms for
    entities, preferring the most-connected entities first). Deterministic order.
    Unavailable (missing-key) transforms are still suggested but flagged so the
    user knows a key is required.
    """

    suggestions: List[Dict[str, object]] = []
    seen_types: set = set()
    for entity in _sorted_by_degree(graph):
        if entity.type_id in seen_types:
            continue
        applicable = registry.for_type(entity.type_id)
        if not applicable:
            continue
        seen_types.add(entity.type_id)
        for t in sorted(applicable, key=lambda x: (not x.is_available(), x.name)):
            suggestions.append(
                {
                    "transform": t.name,
                    "on_entity": entity.id,
                    "on_value": entity.value,
                    "produces": t.output_types,
                    "available": t.is_available(),
                    "note": "" if t.is_available() else f"requires {t.api_key_env}",
                }
            )
            if len(suggestions) >= limit:
                return suggestions
    return suggestions
