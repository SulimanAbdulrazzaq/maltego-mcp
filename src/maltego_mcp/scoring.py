"""Investigation Risk & Confidence engine.

Computes deterministic intelligence-quality metrics for each entity, combining
the current graph structure with the Investigation Memory (which providers
discovered an entity, how, and with what yield). All scores are floats in
``[0, 1]`` rounded to two decimals, so the same graph + memory always yields the
same scores (reproducible).

Metrics
-------
* ``confidence``            -- likelihood the information is correct (driven by
  source reliability and corroboration across multiple providers).
* ``source_reliability``    -- trustworthiness of the most reliable source that
  produced the entity (provider reliability from :class:`ProviderInfo`).
* ``linkage_strength``      -- how strongly the entity is wired into the graph
  (degree + diversity of link labels).
* ``investigation_priority``-- potential investigative value (blend of
  importance, novelty, confidence) -- used to rank what to look at next.
* ``novelty``               -- how genuinely new the finding is (rarer entity
  types score higher; abundant types score lower).

The reliability of a source is provider-agnostic: it is read from the provider's
registered :class:`ProviderInfo.reliability`, so adding a new provider
automatically participates with no changes here. Manually-entered (seed)
entities are attributed to the synthetic source ``"user"`` with full reliability.
"""

from __future__ import annotations

from typing import Dict, List

from maltego_mcp.graph.graph_store import Entity, Graph
from maltego_mcp.transforms import providers as provider_registry

# Reliability assigned to entities the analyst entered manually (not discovered
# by any transform). Analyst-provided seeds are treated as fully trusted.
USER_RELIABILITY = 1.0
# Fallback reliability for a provider that has no registered ProviderInfo.
DEFAULT_RELIABILITY = 0.7


def _clamp(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 2)


def _provider_reliability(provider: str) -> float:
    if provider == "user":
        return USER_RELIABILITY
    info = provider_registry.get(provider)
    return info.reliability if info is not None else DEFAULT_RELIABILITY


def _sources(graph: Graph, entity_id: str) -> List[str]:
    """Providers that discovered the entity, or ['user'] for a seed entity."""

    srcs = graph.memory.sources_for_entity(entity_id)
    return srcs if srcs else ["user"]


def score_entity(graph: Graph, entity_id: str, *, cache: bool = True) -> Dict[str, float]:
    """Compute and return the score dict for one entity.

    When ``cache`` is true the result is stored on ``entity.scores`` so other
    tools/reports can surface it without recomputing. Raises
    ``EntityNotFoundError`` if the id is unknown.
    """

    entity = graph.get_entity(entity_id)
    sources = _sources(graph, entity_id)

    # Source reliability: trust the most reliable corroborating source.
    reliabilities = [_provider_reliability(s) for s in sources]
    source_reliability = max(reliabilities)

    # Confidence: reliability plus a bounded corroboration bonus for multiple
    # independent sources confirming the same entity.
    corroboration = min(0.1 * (len(sources) - 1), 0.2)
    property_bonus = 0.05 if entity.properties else 0.0
    confidence = _clamp(source_reliability + corroboration + property_bonus)

    # Linkage strength: how embedded the entity is in the graph.
    degree = graph.degree(entity_id)
    distinct_labels = len({l.label for l in graph.links_of(entity_id) if l.label})
    linkage_strength = _clamp(0.4 + 0.12 * degree + 0.08 * distinct_labels)

    # Novelty: rarer entity types are more novel. Seeds and singletons score high.
    type_count = len(graph.entities_of_type(entity.type_id))
    novelty = _clamp(1.0 - 0.15 * (type_count - 1))

    # Importance from connectivity, normalized so a hub scores high.
    importance = degree / (degree + 3.0)

    investigation_priority = _clamp(
        0.35 * importance + 0.35 * novelty + 0.30 * confidence
    )

    scores = {
        "confidence": confidence,
        "source_reliability": _clamp(source_reliability),
        "linkage_strength": linkage_strength,
        "investigation_priority": investigation_priority,
        "novelty": novelty,
    }
    if cache:
        entity.scores = dict(scores)
    return scores


def explain_scores(graph: Graph, entity_id: str) -> Dict[str, object]:
    """Return scores plus a deterministic, human-readable rationale."""

    entity = graph.get_entity(entity_id)
    sources = _sources(graph, entity_id)
    scores = score_entity(graph, entity_id)
    degree = graph.degree(entity_id)
    type_count = len(graph.entities_of_type(entity.type_id))

    factors = [
        f"Discovered via {len(sources)} source(s): {', '.join(sources)} "
        f"(most reliable = {max(_provider_reliability(s) for s in sources):.2f}).",
        f"Connected to {degree} entit(y/ies)"
        + (f" with {len({l.label for l in graph.links_of(entity_id) if l.label})} "
           "distinct relationship label(s)." if degree else "."),
        f"{type_count} entit(y/ies) of type {entity.type_id} exist "
        f"(rarer = more novel).",
    ]
    if entity.properties:
        factors.append("Carries enriching properties (+confidence).")
    return {
        "id": entity.id,
        "value": entity.value,
        "type": entity.type_id,
        "sources": sources,
        "scores": scores,
        "factors": factors,
    }


def rank_entities(graph: Graph, limit: int = 20) -> List[Dict[str, object]]:
    """Rank all entities by investigation priority (desc), deterministic ties.

    Computes and caches scores for every entity, then returns the top ``limit``
    as ``{id, value, type, scores}`` ordered by investigation_priority desc, then
    node-id ascending for stable ties.
    """

    rows = []
    for entity in graph.entities:
        scores = score_entity(graph, entity.id)
        rows.append((entity, scores))

    def id_key(e: Entity) -> int:
        return int(e.id[1:]) if e.id.startswith("n") and e.id[1:].isdigit() else 0

    rows.sort(key=lambda r: (-r[1]["investigation_priority"], id_key(r[0])))
    return [
        {"id": e.id, "value": e.value, "type": e.type_id, "scores": s}
        for e, s in rows[:limit]
    ]


# --- importance scoring for memory steps (used by orchestration) -------------
def step_importance(new_count: int, output_types: int, novelty: float) -> float:
    """Deterministic importance score for a recorded transform execution.

    Higher when a transform yields more new entities, can produce more varied
    output types, and surfaces novel findings. Bounded to [0, 1].
    """

    base = 0.2 + 0.12 * new_count + 0.05 * output_types
    return _clamp(base + 0.2 * novelty if new_count else 0.05)
