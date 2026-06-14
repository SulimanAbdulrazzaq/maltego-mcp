"""In-memory model of a Maltego investigation graph and a multi-graph store.

These classes are deliberately framework-agnostic (no MCP / Pydantic imports) so
they can be unit-tested and reused by the ``.mtgx`` reader/writer and the
transform layer. The :class:`GraphStore` is the single source of truth the MCP
server holds for the lifetime of the process and tracks which graph is
"active" (the implicit target of entity/link operations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from maltego_mcp import entities as entity_catalog
from maltego_mcp.memory import InvestigationMemory


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class Entity:
    """A single node on the graph.

    Attributes:
        id: Stable node id, unique within a graph (e.g. ``n0``).
        type_id: Maltego entity type id (e.g. ``maltego.Domain``).
        value: Primary display value (e.g. ``example.com``).
        properties: Extra Maltego properties (name -> value), excluding the
            main value which is stored separately in :attr:`value`.
        notes: Free-text notes attached to the entity.
        weight: Maltego node weight (relevance), default 1.
        position: Optional (x, y) layout coordinates assigned by the layout
            engine. ``None`` means "no explicit position" (Maltego will
            auto-layout on import).
    """

    id: str
    type_id: str
    value: str
    properties: Dict[str, str] = field(default_factory=dict)
    notes: str = ""
    weight: int = 1
    position: Optional[Tuple[float, float]] = None
    #: Cached intelligence-quality scores (see maltego_mcp.scoring). Populated on
    #: demand by the scoring engine; ``None`` until computed.
    scores: Optional[Dict[str, float]] = None

    def main_property(self) -> str:
        return entity_catalog.main_property_for(self.type_id)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "type": self.type_id,
            "value": self.value,
            "properties": dict(self.properties),
            "notes": self.notes,
            "weight": self.weight,
        }
        # Only surface position when it has been computed, to keep payloads lean
        # and preserve the original dict shape for callers that ignore layout.
        if self.position is not None:
            data["position"] = [self.position[0], self.position[1]]
        if self.scores is not None:
            data["scores"] = dict(self.scores)
        return data


@dataclass
class Link:
    """A directed relationship between two entities.

    Attributes:
        id: Stable edge id, unique within a graph (e.g. ``e0``).
        source_id: Id of the source :class:`Entity`.
        target_id: Id of the target :class:`Entity`.
        label: Human-readable label drawn on the edge.
    """

    id: str
    source_id: str
    target_id: str
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source_id,
            "target": self.target_id,
            "label": self.label,
        }


class DuplicateEntityError(Exception):
    """Raised when adding an entity that already exists (same type+value)."""


class EntityNotFoundError(Exception):
    """Raised when referencing an entity id that is not on the graph."""


class Graph:
    """A single Maltego investigation graph held in memory."""

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.created_at: str = _now_iso()
        self.source_path: Optional[str] = None
        self._entities: Dict[str, Entity] = {}
        self._links: Dict[str, Link] = {}
        self._node_counter: int = 0
        self._edge_counter: int = 0
        # Index for fast duplicate detection: (type_id, value) -> entity id.
        self._value_index: Dict[tuple, str] = {}
        # Procedural memory: how/why this graph was built. Kept separate from the
        # graph structure but travels with it (serialized into the .mtgx).
        self.memory: InvestigationMemory = InvestigationMemory()

    # -- entity operations ----------------------------------------------------
    def _next_node_id(self) -> str:
        nid = f"n{self._node_counter}"
        self._node_counter += 1
        return nid

    def find_entity_by_value(self, type_id: str, value: str) -> Optional[Entity]:
        eid = self._value_index.get((type_id, value))
        return self._entities.get(eid) if eid else None

    def add_entity(
        self,
        type_id: str,
        value: str,
        properties: Optional[Dict[str, str]] = None,
        notes: str = "",
        weight: int = 1,
        dedupe: bool = True,
    ) -> Entity:
        """Add an entity and return it.

        If ``dedupe`` is true and an entity with the same ``type_id``/``value``
        already exists, the existing entity is returned unchanged (its
        properties are merged with any new ones) instead of creating a
        duplicate node.
        """

        if dedupe:
            existing = self.find_entity_by_value(type_id, value)
            if existing is not None:
                if properties:
                    existing.properties.update(properties)
                return existing

        entity = Entity(
            id=self._next_node_id(),
            type_id=type_id,
            value=value,
            properties=dict(properties or {}),
            notes=notes,
            weight=weight,
        )
        self._entities[entity.id] = entity
        self._value_index[(type_id, value)] = entity.id
        return entity

    def get_entity(self, entity_id: str) -> Entity:
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(entity_id)
        return entity

    def update_entity(
        self,
        entity_id: str,
        value: Optional[str] = None,
        properties: Optional[Dict[str, str]] = None,
        notes: Optional[str] = None,
        weight: Optional[int] = None,
    ) -> Entity:
        entity = self.get_entity(entity_id)
        if value is not None and value != entity.value:
            # Keep the value index consistent.
            self._value_index.pop((entity.type_id, entity.value), None)
            entity.value = value
            self._value_index[(entity.type_id, value)] = entity.id
        if properties:
            entity.properties.update(properties)
        if notes is not None:
            entity.notes = notes
        if weight is not None:
            entity.weight = weight
        return entity

    def delete_entity(self, entity_id: str) -> List[str]:
        """Delete an entity and all links touching it.

        Returns the list of link ids that were removed alongside the entity.
        """

        entity = self.get_entity(entity_id)
        removed_links = [
            lid
            for lid, link in self._links.items()
            if link.source_id == entity_id or link.target_id == entity_id
        ]
        for lid in removed_links:
            del self._links[lid]
        self._value_index.pop((entity.type_id, entity.value), None)
        del self._entities[entity_id]
        return removed_links

    # -- link operations ------------------------------------------------------
    def _next_edge_id(self) -> str:
        eid = f"e{self._edge_counter}"
        self._edge_counter += 1
        return eid

    def add_link(
        self, source_id: str, target_id: str, label: str = "", dedupe: bool = False
    ) -> Link:
        # Validate endpoints exist (raises EntityNotFoundError otherwise).
        self.get_entity(source_id)
        self.get_entity(target_id)
        if dedupe:
            for existing in self._links.values():
                if (
                    existing.source_id == source_id
                    and existing.target_id == target_id
                    and existing.label == label
                ):
                    return existing
        link = Link(
            id=self._next_edge_id(),
            source_id=source_id,
            target_id=target_id,
            label=label,
        )
        self._links[link.id] = link
        return link

    def delete_link(self, link_id: str) -> bool:
        """Delete a link by id. Returns True if it existed."""

        return self._links.pop(link_id, None) is not None

    # -- accessors ------------------------------------------------------------
    @property
    def entities(self) -> List[Entity]:
        return list(self._entities.values())

    @property
    def links(self) -> List[Link]:
        return list(self._links.values())

    def entity_count(self) -> int:
        return len(self._entities)

    def link_count(self) -> int:
        return len(self._links)

    def register_existing_entity(self, entity: Entity) -> None:
        """Insert a pre-built entity (used by the .mtgx reader).

        Keeps the id counter and value index consistent with loaded ids.
        """

        self._entities[entity.id] = entity
        self._value_index[(entity.type_id, entity.value)] = entity.id
        if entity.id.startswith("n") and entity.id[1:].isdigit():
            self._node_counter = max(self._node_counter, int(entity.id[1:]) + 1)

    def register_existing_link(self, link: Link) -> None:
        """Insert a pre-built link (used by the .mtgx reader)."""

        self._links[link.id] = link
        if link.id.startswith("e") and link.id[1:].isdigit():
            self._edge_counter = max(self._edge_counter, int(link.id[1:]) + 1)

    # -- graph structure helpers (analysis / layout) --------------------------
    def get_link(self, link_id: str) -> Optional[Link]:
        return self._links.get(link_id)

    def links_of(self, entity_id: str) -> List[Link]:
        """All links where ``entity_id`` is the source or target."""

        return [
            link
            for link in self._links.values()
            if link.source_id == entity_id or link.target_id == entity_id
        ]

    def degree(self, entity_id: str) -> int:
        """Number of links touching ``entity_id`` (in + out)."""

        return len(self.links_of(entity_id))

    def neighbors(self, entity_id: str) -> List[Tuple[Entity, Link, str]]:
        """Return ``(neighbor, link, direction)`` tuples for ``entity_id``.

        ``direction`` is ``"out"`` when ``entity_id`` is the link source and
        ``"in"`` when it is the target.
        """

        result: List[Tuple[Entity, Link, str]] = []
        for link in self._links.values():
            if link.source_id == entity_id and link.target_id in self._entities:
                result.append((self._entities[link.target_id], link, "out"))
            elif link.target_id == entity_id and link.source_id in self._entities:
                result.append((self._entities[link.source_id], link, "in"))
        return result

    def type_counts(self) -> Dict[str, int]:
        """Count of entities by Maltego type id (deterministic order by type)."""

        counts: Dict[str, int] = {}
        for entity in self._entities.values():
            counts[entity.type_id] = counts.get(entity.type_id, 0) + 1
        return dict(sorted(counts.items()))

    def entities_of_type(self, type_id: str) -> List[Entity]:
        return [e for e in self._entities.values() if e.type_id == type_id]

    def isolated_entities(self) -> List[Entity]:
        """Entities with no links (deterministic order by id)."""

        linked: set = set()
        for link in self._links.values():
            linked.add(link.source_id)
            linked.add(link.target_id)
        return sorted(
            (e for e in self._entities.values() if e.id not in linked),
            key=lambda e: e.id,
        )

    def set_positions(self, positions: Dict[str, Tuple[float, float]]) -> int:
        """Assign (x, y) positions to entities by id. Returns count assigned."""

        count = 0
        for eid, pos in positions.items():
            entity = self._entities.get(eid)
            if entity is not None:
                entity.position = (float(pos[0]), float(pos[1]))
                count += 1
        return count

    def merge_from(self, other: "Graph", dedupe: bool = True) -> Dict[str, int]:
        """Merge entities and links from ``other`` into this graph.

        Ids from ``other`` are remapped to fresh ids in this graph (so the two
        graphs' id spaces never collide). When ``dedupe`` is true, entities with
        a matching ``(type, value)`` already present are reused rather than
        duplicated. Returns counts of what was added/reused.
        """

        id_map: Dict[str, str] = {}
        added_entities = 0
        reused_entities = 0
        for entity in other.entities:
            existing = self.find_entity_by_value(entity.type_id, entity.value) if dedupe else None
            if existing is not None:
                if entity.properties:
                    existing.properties.update(entity.properties)
                id_map[entity.id] = existing.id
                reused_entities += 1
            else:
                new_entity = self.add_entity(
                    type_id=entity.type_id,
                    value=entity.value,
                    properties=entity.properties,
                    notes=entity.notes,
                    weight=entity.weight,
                    dedupe=False,
                )
                id_map[entity.id] = new_entity.id
                added_entities += 1

        added_links = 0
        for link in other.links:
            src = id_map.get(link.source_id)
            tgt = id_map.get(link.target_id)
            if src is None or tgt is None:
                continue
            # Avoid duplicating an identical (source, target, label) link.
            if dedupe and any(
                l.source_id == src and l.target_id == tgt and l.label == link.label
                for l in self._links.values()
            ):
                continue
            self.add_link(src, tgt, link.label)
            added_links += 1

        # Merge procedural memory too, remapping entity references.
        steps_merged = self.memory.remap_and_extend(other.memory, id_map)

        return {
            "entities_added": added_entities,
            "entities_reused": reused_entities,
            "links_added": added_links,
            "memory_steps_merged": steps_merged,
        }

    def summary(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "source_path": self.source_path,
            "entity_count": self.entity_count(),
            "link_count": self.link_count(),
        }


class GraphStore:
    """Holds all open graphs for the server and tracks the active one."""

    def __init__(self) -> None:
        self._graphs: Dict[str, Graph] = {}
        self._active: Optional[str] = None

    def create_graph(self, name: str, make_active: bool = True) -> Graph:
        if name in self._graphs:
            raise ValueError(f"A graph named '{name}' already exists.")
        graph = Graph(name)
        self._graphs[name] = graph
        if make_active or self._active is None:
            self._active = name
        return graph

    def add_graph(self, graph: Graph, make_active: bool = True) -> Graph:
        """Register an already-built :class:`Graph` (e.g. loaded from disk)."""

        # Avoid clobbering an existing graph with the same name.
        name = graph.name
        suffix = 1
        while name in self._graphs:
            suffix += 1
            name = f"{graph.name} ({suffix})"
        graph.name = name
        self._graphs[name] = graph
        if make_active or self._active is None:
            self._active = name
        return graph

    def get_graph(self, name: str) -> Graph:
        graph = self._graphs.get(name)
        if graph is None:
            raise KeyError(name)
        return graph

    def active_graph(self) -> Graph:
        if self._active is None:
            raise RuntimeError(
                "No active graph. Create one with maltego_create_graph or open "
                "an existing .mtgx file with maltego_open_graph first."
            )
        return self._graphs[self._active]

    def set_active(self, name: str) -> Graph:
        if name not in self._graphs:
            raise KeyError(name)
        self._active = name
        return self._graphs[name]

    @property
    def active_name(self) -> Optional[str]:
        return self._active

    def list_graphs(self) -> List[Graph]:
        return list(self._graphs.values())

    def rename_graph(self, old: str, new: str) -> Graph:
        """Rename an open graph. Updates the active pointer if needed."""

        if old not in self._graphs:
            raise KeyError(old)
        if new in self._graphs:
            raise ValueError(f"A graph named '{new}' already exists.")
        graph = self._graphs.pop(old)
        graph.name = new
        self._graphs[new] = graph
        if self._active == old:
            self._active = new
        return graph

    def remove_graph(self, name: str) -> None:
        """Remove an open graph from the store (does not touch any saved file)."""

        if name not in self._graphs:
            raise KeyError(name)
        del self._graphs[name]
        if self._active == name:
            # Fall back to any remaining graph, or no active graph.
            self._active = next(iter(self._graphs), None)
