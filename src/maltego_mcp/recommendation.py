"""Next Best Action engine.

A deterministic, explainable decision engine that ranks the most valuable next
investigative moves. It supersedes the simple heuristic in
:func:`maltego_mcp.analysis.suggest_next_steps` (which is retained for backward
compatibility) by weighing several signals drawn from the graph and the
Investigation Memory:

* **Entity importance** -- connectivity / investigation priority (from scoring).
* **Transform history** -- skip ``(entity, transform)`` pairs already attempted
  (no repetitive suggestions), recorded in Investigation Memory.
* **Previous outcomes** -- providers that have already yielded results in *this*
  investigation are favoured (observed average yield).
* **Provider availability** -- available transforms rank above ones whose API
  key is missing (but the latter are still surfaced, flagged).
* **Expected information gain** -- transforms that can produce more/varied output
  types score higher.
* **Confidence** -- the trigger entity's confidence score.

Each recommendation carries a deterministic ``score`` and a plain-English
``reason`` so the output is fully explainable, e.g.:

    "The email entity 'bob@x.com' has not yet been checked against
     HaveIBeenPwned; breach pivots have not been tried in this investigation."
"""

from __future__ import annotations

from typing import Dict, List

from maltego_mcp import learning, scoring
from maltego_mcp.graph.graph_store import Entity, Graph
from maltego_mcp.transforms import Transform, registry


def _round(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 3)


def _expected_gain(graph: Graph, transform: Transform) -> float:
    """Estimate information gain for a transform in [0, 1].

    Blends three deterministic signals:
      * the transform's output-type breadth,
      * its observed average yield in *this* investigation (from memory), and
      * a cross-investigation historical prior (from the opt-in learning store;
        0.0 when learning is disabled, so behaviour is unchanged by default).
    """

    breadth = min(len(transform.output_types) / 3.0, 1.0)
    prior = learning.store.prior(transform.name)  # 0.0 unless learning enabled

    yields = graph.memory.provider_yield(transform.provider)
    if yields:
        avg = sum(yields) / len(yields)
        observed = min(avg / 5.0, 1.0)  # 5+ new entities/step -> max
        base = 0.5 * breadth + 0.5 * observed
    else:
        base = 0.6 * breadth

    if prior > 0.0:
        # Let history nudge the estimate without overwhelming current signals.
        return _round(0.7 * base + 0.3 * prior)
    return _round(base)


def _id_key(e: Entity) -> int:
    return int(e.id[1:]) if e.id.startswith("n") and e.id[1:].isdigit() else 0


def _id_num(entity_id: str) -> int:
    """Numeric sort key for a node id string (e.g. 'n10' -> 10)."""

    return int(entity_id[1:]) if entity_id.startswith("n") and entity_id[1:].isdigit() else 0


def next_best_actions(graph: Graph, limit: int = 10) -> List[Dict[str, object]]:
    """Return ranked, explainable next-best-action recommendations.

    Each item:
        {
          "transform": str,
          "entity_id": str,
          "entity_value": str,
          "entity_type": str,
          "provider": str,
          "available": bool,
          "score": float,          # deterministic, in [0, 1]
          "expected_gain": float,
          "reason": str,
        }
    Ordered by score desc, then (entity id, transform name) for stable ties.
    Pairs already attempted (in Investigation Memory) are excluded so the engine
    never repeats itself.
    """

    recs: List[Dict[str, object]] = []
    for entity in graph.entities:
        scores = scoring.score_entity(graph, entity.id)
        already = graph.memory.transforms_run_on(entity.id)
        priority = scores["investigation_priority"]
        confidence = scores["confidence"]

        for transform in registry.for_type(entity.type_id):
            if transform.name in already:
                continue  # don't repeat an attempted pivot
            available = transform.is_available()
            gain = _expected_gain(graph, transform)

            # Deterministic weighted score. Unavailable transforms are penalized
            # but still surfaced so the analyst knows a key would unlock them.
            score = (
                0.35 * priority
                + 0.30 * gain
                + 0.20 * confidence
                + 0.15 * (1.0 if available else 0.0)
            )

            reason = _build_reason(graph, entity, transform, available, gain)
            recs.append(
                {
                    "transform": transform.name,
                    "entity_id": entity.id,
                    "entity_value": entity.value,
                    "entity_type": entity.type_id,
                    "provider": transform.provider,
                    "available": available,
                    "score": _round(score),
                    "expected_gain": gain,
                    "reason": reason,
                }
            )

    recs.sort(key=lambda r: (-r["score"], _id_num(r["entity_id"]), r["transform"]))
    return recs[:limit]


def _build_reason(
    graph: Graph, entity: Entity, transform: Transform, available: bool, gain: float
) -> str:
    """Deterministic natural-language justification for a recommendation."""

    provider_info = transform.provider
    parts = [
        f"The {entity.type_id.split('.')[-1]} entity '{entity.value}' has not yet "
        f"been investigated with '{transform.name}'."
    ]
    yields = graph.memory.provider_yield(transform.provider)
    hist = learning.store.stats(transform.name)
    if yields and sum(yields) > 0:
        avg = sum(yields) / len(yields)
        parts.append(
            f"{provider_info} has averaged {avg:.1f} new entities per run in this "
            "investigation."
        )
    elif hist["runs"] > 0 and hist["successes"] > 0:
        parts.append(
            f"Historically '{transform.name}' succeeded in "
            f"{int(hist['successes'])}/{int(hist['runs'])} past run(s)."
        )
    elif gain >= 0.6:
        parts.append(
            f"It can produce {', '.join(transform.output_types)}, a promising pivot."
        )
    if not available:
        parts.append(f"(Requires {transform.api_key_env}; set the key to enable.)")
    return " ".join(parts)
