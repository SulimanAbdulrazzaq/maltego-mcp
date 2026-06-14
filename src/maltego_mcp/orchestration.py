"""Investigation orchestration engine.

This sits on top of the transform layer and lets a single high-level call expand
a graph by running many transforms automatically -- the foundation for the
``investigate_*`` tools and for :mod:`maltego_mcp.machines`.

The engine performs a breadth-first expansion: starting from a seed entity, it
repeatedly applies the eligible transforms to every entity, adding results (with
links) to the graph and de-duplicating as it goes. It tracks which
``(entity, transform)`` pairs it has already run so it never repeats work, and
it bounds total work by ``max_rounds``.

Transform selection is configurable so callers can build "passive" (no network)
or provider-specific investigations:

* ``transform_names`` -- restrict to an explicit set of transforms.
* ``allow_network``   -- include transforms that perform network I/O.
* ``available_only``  -- skip transforms whose API key is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from maltego_mcp import learning, scoring
from maltego_mcp.events import (
    ENTITY_DISCOVERED,
    TRANSFORM_COMPLETED,
    TRANSFORM_STARTED,
    bus,
)
from maltego_mcp.graph.graph_store import Entity, Graph
from maltego_mcp.memory import STATUS_EMPTY, STATUS_ERROR, STATUS_SUCCESS, InvestigationStep
from maltego_mcp.transforms import Transform, registry


@dataclass
class ExpansionReport:
    """Outcome of an orchestration run."""

    seed_id: str
    seed_value: str
    transforms_run: int = 0
    entities_added: int = 0
    links_added: int = 0
    rounds: int = 0
    messages: List[str] = field(default_factory=list)
    skipped_unavailable: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seed_id": self.seed_id,
            "seed_value": self.seed_value,
            "transforms_run": self.transforms_run,
            "entities_added": self.entities_added,
            "links_added": self.links_added,
            "rounds": self.rounds,
            "skipped_unavailable": sorted(set(self.skipped_unavailable)),
            "messages": self.messages,
        }


def _eligible_transforms(
    type_id: str,
    transform_names: Optional[Set[str]],
    allow_network: bool,
    available_only: bool,
) -> List[Transform]:
    result = []
    for t in registry.for_type(type_id):
        if transform_names is not None and t.name not in transform_names:
            continue
        if not allow_network and t.requires_network:
            continue
        if available_only and not t.is_available():
            continue
        result.append(t)
    return sorted(result, key=lambda x: x.name)


async def run_and_record(
    graph: Graph,
    transform: Transform,
    entity: Entity,
    reason: str,
    *,
    add_to_graph: bool = True,
    emit_events: bool = True,
) -> Tuple[List[Entity], InvestigationStep]:
    """Run one transform on one entity, applying results and recording memory.

    This is the single choke-point used by both the orchestration engine and the
    manual ``maltego_run_transform`` tool, so procedural memory and real-time
    events are captured consistently everywhere.

    Returns ``(added_entities, step)`` where ``added_entities`` are the entities
    newly created on the graph (empty if ``add_to_graph`` is False or nothing new
    was found) and ``step`` is the recorded :class:`InvestigationStep`.
    """

    if emit_events:
        bus.emit(
            TRANSFORM_STARTED,
            {"transform": transform.name, "entity_id": entity.id, "entity_value": entity.value},
        )

    # Execute the transform, capturing any failure as an error step.
    try:
        result = await transform.run(entity.value, dict(entity.properties))
    except Exception as exc:  # noqa: BLE001 - one transform must never abort a run
        step = graph.memory.record(
            trigger_entity_id=entity.id,
            trigger_entity_value=entity.value,
            transform=transform.name,
            provider=transform.provider,
            reason=reason,
            new_entity_ids=[],
            importance_score=0.0,
            status=STATUS_ERROR,
            reconsider=True,
            message=str(exc),
        )
        if emit_events:
            bus.emit(
                TRANSFORM_COMPLETED,
                {"transform": transform.name, "entity_id": entity.id, "status": STATUS_ERROR},
            )
        return [], step

    added: List[Entity] = []
    if add_to_graph:
        for res in result.entities:
            before = graph.entity_count()
            new_entity = graph.add_entity(
                type_id=res.type_id, value=res.value, properties=res.properties
            )
            is_new = graph.entity_count() > before
            if new_entity.id != entity.id:
                graph.add_link(entity.id, new_entity.id, res.link_label, dedupe=True)
            if is_new:
                added.append(new_entity)
                if emit_events:
                    bus.emit(
                        ENTITY_DISCOVERED,
                        {
                            "entity_id": new_entity.id,
                            "value": new_entity.value,
                            "type": new_entity.type_id,
                            "via_transform": transform.name,
                            "from_entity": entity.id,
                        },
                    )

    # Importance from yield + output breadth + novelty of discoveries.
    novelty = 0.0
    for e in added:
        s = scoring.score_entity(graph, e.id)
        novelty = max(novelty, s["novelty"])
    importance = scoring.step_importance(len(added), len(transform.output_types), novelty)

    if result.entities and not added and add_to_graph:
        status = STATUS_EMPTY  # produced results but all were duplicates
    elif added:
        status = STATUS_SUCCESS
    else:
        status = STATUS_EMPTY
    reconsider = status == STATUS_EMPTY

    # Cross-investigation learning (opt-in; no-op when disabled).
    learning.store.record(transform.name, status, len(added))

    step = graph.memory.record(
        trigger_entity_id=entity.id,
        trigger_entity_value=entity.value,
        transform=transform.name,
        provider=transform.provider,
        reason=reason,
        new_entity_ids=[e.id for e in added],
        importance_score=importance,
        status=status,
        reconsider=reconsider,
        message=result.message,
    )
    if emit_events:
        bus.emit(
            TRANSFORM_COMPLETED,
            {
                "transform": transform.name,
                "entity_id": entity.id,
                "status": status,
                "new_entities": len(added),
            },
        )
    return added, step


async def expand(
    graph: Graph,
    seed_id: str,
    *,
    transform_names: Optional[List[str]] = None,
    allow_network: bool = True,
    available_only: bool = True,
    max_rounds: int = 2,
) -> ExpansionReport:
    """Breadth-first expand ``graph`` starting from entity ``seed_id``.

    Returns an :class:`ExpansionReport`. The seed entity must already exist.
    """

    seed = graph.get_entity(seed_id)
    report = ExpansionReport(seed_id=seed.id, seed_value=seed.value)
    names: Optional[Set[str]] = set(transform_names) if transform_names else None
    done: Set[Tuple[str, str]] = set()  # (entity_id, transform_name)

    # Track unavailable transforms we would have run, for transparency.
    def note_unavailable(type_id: str) -> None:
        if not available_only:
            return
        for t in registry.for_type(type_id):
            if names is not None and t.name not in names:
                continue
            if not allow_network and t.requires_network:
                continue
            if not t.is_available():
                report.skipped_unavailable.append(t.name)

    for round_no in range(max_rounds):
        # Snapshot current entities so results added this round are processed
        # in the next round (true breadth-first expansion).
        current = list(graph.entities)
        progressed = False
        for entity in current:
            note_unavailable(entity.type_id)
            for transform in _eligible_transforms(
                entity.type_id, names, allow_network, available_only
            ):
                key = (entity.id, transform.name)
                if key in done:
                    continue
                done.add(key)
                reason = (
                    f"Round {round_no + 1}: '{transform.name}' is applicable to "
                    f"{entity.type_id} entity '{entity.value}'."
                )
                links_before = graph.link_count()
                added, step = await run_and_record(graph, transform, entity, reason)
                report.transforms_run += 1
                if step.message:
                    report.messages.append(f"{transform.name}: {step.message}")
                # Count links from the real graph delta (dedupe-aware), and
                # entities from what was actually newly created.
                report.links_added += graph.link_count() - links_before
                if added:
                    report.entities_added += len(added)
                    progressed = True
        report.rounds = round_no + 1
        if not progressed:
            break
    return report
