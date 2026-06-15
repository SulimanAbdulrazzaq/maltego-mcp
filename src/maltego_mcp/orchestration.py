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

import asyncio
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


async def _execute(transform: Transform, entity: Entity, *, emit_events: bool = True):
    """Run one transform's network call WITHOUT mutating the graph.

    Returns ``(result, error)`` — exactly one is non-None. Kept side-effect-free
    (besides the started event) so many executions can be gathered concurrently
    and their results applied later in a deterministic order.
    """

    if emit_events:
        bus.emit(
            TRANSFORM_STARTED,
            {"transform": transform.name, "entity_id": entity.id, "entity_value": entity.value},
        )
    try:
        result = await transform.run(entity.value, dict(entity.properties))
        return result, None
    except Exception as exc:  # noqa: BLE001 - one transform must never abort a run
        return None, exc


def _apply(
    graph: Graph,
    transform: Transform,
    entity: Entity,
    reason: str,
    result,
    error,
    *,
    add_to_graph: bool = True,
    emit_events: bool = True,
) -> Tuple[List[Entity], InvestigationStep]:
    """Apply an executed transform's result to the graph and record memory.

    Pure synchronous (no ``await``) so callers can apply gathered results one by
    one in a fixed order — guaranteeing deterministic entity ids regardless of
    network completion order.
    """

    if error is not None:
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
            message=str(error),
        )
        learning.store.record(transform.name, STATUS_ERROR, 0)
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

    learning.store.record(transform.name, status, len(added))  # opt-in; no-op when off

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


async def run_and_record(
    graph: Graph,
    transform: Transform,
    entity: Entity,
    reason: str,
    *,
    add_to_graph: bool = True,
    emit_events: bool = True,
) -> Tuple[List[Entity], InvestigationStep]:
    """Run one transform on one entity, apply results, and record memory.

    The single choke-point for the manual ``maltego_run_transform`` tool. Returns
    ``(added_entities, step)``. (The orchestration engine uses the lower-level
    :func:`_execute`/:func:`_apply` split so it can run a round concurrently.)
    """

    result, error = await _execute(transform, entity, emit_events=emit_events)
    return _apply(
        graph, transform, entity, reason, result, error,
        add_to_graph=add_to_graph, emit_events=emit_events,
    )


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

        # Build this round's (entity, transform, reason) work list in a fully
        # deterministic order (entities in graph order, transforms sorted).
        work = []
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
                work.append((entity, transform, reason))

        if not work:
            report.rounds = round_no + 1
            break

        # Execute all network calls CONCURRENTLY (asyncio.gather preserves input
        # order in its results), then APPLY them sequentially in that fixed order
        # — fast (parallel I/O) and deterministic (stable entity ids).
        executions = await asyncio.gather(
            *[_execute(t, e) for (e, t, _r) in work]
        )

        progressed = False
        for (entity, transform, reason), (result, error) in zip(work, executions):
            links_before = graph.link_count()
            added, step = _apply(graph, transform, entity, reason, result, error)
            report.transforms_run += 1
            if step.message:
                report.messages.append(f"{transform.name}: {step.message}")
            report.links_added += graph.link_count() - links_before
            if added:
                report.entities_added += len(added)
                progressed = True
        report.rounds = round_no + 1
        if not progressed:
            break
    return report
