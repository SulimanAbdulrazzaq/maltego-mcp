"""Parse a Maltego ``.mtgx`` archive into an in-memory :class:`Graph`.

The reader is deliberately tolerant: Maltego graphs produced by different
versions vary in namespace prefixes (``mtg`` vs ``maltego``), in whether the
``MaltegoEntity`` is wrapped in a ``<data>`` element, and in which GraphML
``<key>`` ids are used. To cope, the reader matches elements by their *local*
name (ignoring namespace prefix) rather than by exact qualified name.
"""

from __future__ import annotations

import json
import os
import zipfile
from typing import List, Optional
from xml.etree import ElementTree as ET

from maltego_mcp import entities as entity_catalog
from maltego_mcp.graph.graph_store import Entity, Graph, Link
from maltego_mcp.graph.mtgx_writer import MEMORY_MEMBER, SCORES_MEMBER


class MtgxParseError(Exception):
    """Raised when a ``.mtgx`` archive cannot be parsed."""


def _local(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an ElementTree tag."""

    return tag.rsplit("}", 1)[-1]


def _find_graphml_member(zf: zipfile.ZipFile) -> str:
    """Return the name of the GraphML member inside the archive."""

    candidates = [n for n in zf.namelist() if n.lower().endswith(".graphml")]
    if not candidates:
        raise MtgxParseError(
            "No .graphml member found inside the .mtgx archive. Is this a valid "
            "Maltego graph file?"
        )
    # Prefer the conventional Graphs/Graph1.graphml when present.
    for name in candidates:
        if name.replace("\\", "/").endswith("Graphs/Graph1.graphml"):
            return name
    return candidates[0]


def _child_by_local(parent: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in parent:
        if _local(child.tag) == local_name:
            return child
    return None


def _iter_by_local(parent: ET.Element, local_name: str):
    for child in parent:
        if _local(child.tag) == local_name:
            yield child


def _find_descendant_by_local(parent: ET.Element, local_name: str) -> Optional[ET.Element]:
    for el in parent.iter():
        if _local(el.tag) == local_name and el is not parent:
            return el
    return None


def _parse_properties(props_parent: ET.Element) -> dict:
    """Extract name -> value from a ``<Properties>`` (or inline) element."""

    result: dict = {}
    for prop in props_parent.iter():
        if _local(prop.tag) != "Property":
            continue
        name = prop.get("name")
        if not name:
            continue
        value_el = _child_by_local(prop, "Value")
        result[name] = (value_el.text or "") if value_el is not None else ""
    return result


def _parse_entity_node(node_el: ET.Element) -> Optional[Entity]:
    node_id = node_el.get("id")
    if not node_id:
        return None
    mtg_entity = _find_descendant_by_local(node_el, "MaltegoEntity")
    if mtg_entity is None:
        return None

    type_id = mtg_entity.get("type") or entity_catalog.DEFAULT_TYPE
    properties = _parse_properties(mtg_entity)

    main_name = entity_catalog.main_property_for(type_id)
    value = properties.pop(main_name, "")
    if not value and properties:
        # Fall back to the first property value if the main one is absent.
        first_key = next(iter(properties))
        value = properties.pop(first_key)

    notes_el = _find_descendant_by_local(mtg_entity, "Notes")
    notes = (notes_el.text or "") if notes_el is not None else ""

    # Recover Maltego's native node position (EntityRenderer/Position x,y).
    position = None
    pos_el = _find_descendant_by_local(node_el, "Position")
    if pos_el is not None:
        try:
            position = (float(pos_el.get("x")), float(pos_el.get("y")))
        except (TypeError, ValueError):
            position = None

    return Entity(
        id=node_id,
        type_id=type_id,
        value=value,
        properties=properties,
        notes=notes,
        position=position,
    )


def _parse_link_edge(edge_el: ET.Element) -> Optional[Link]:
    source = edge_el.get("source")
    target = edge_el.get("target")
    if not source or not target:
        return None
    edge_id = edge_el.get("id") or f"e_{source}_{target}"

    label = ""
    mtg_link = _find_descendant_by_local(edge_el, "MaltegoLink")
    if mtg_link is not None:
        props = _parse_properties(mtg_link)
        # The manual-link label lives under maltego.link.manual.type, but accept
        # any single property as the label for robustness.
        label = props.get("maltego.link.manual.type") or next(
            iter(props.values()), ""
        )
    return Link(id=edge_id, source_id=source, target_id=target, label=label)


def parse_graphml(graphml_bytes: bytes, name: str) -> Graph:
    """Parse raw GraphML bytes into a :class:`Graph` named ``name``."""

    try:
        root = ET.fromstring(graphml_bytes)
    except ET.ParseError as exc:  # pragma: no cover - defensive
        raise MtgxParseError(f"Invalid GraphML XML: {exc}") from exc

    graph_el = _child_by_local(root, "graph")
    if graph_el is None:
        raise MtgxParseError("GraphML document has no <graph> element.")

    graph = Graph(name)
    for node_el in _iter_by_local(graph_el, "node"):
        entity = _parse_entity_node(node_el)
        if entity is not None:
            graph.register_existing_entity(entity)
    for edge_el in _iter_by_local(graph_el, "edge"):
        link = _parse_link_edge(edge_el)
        if link is not None:
            graph.register_existing_link(link)
    return graph


def read_mtgx(path: str) -> Graph:
    """Load a ``.mtgx`` file from ``path`` into a :class:`Graph`.

    The graph is named after the file (without extension) and records the
    source path so it can be re-saved in place.
    """

    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    memory_bytes: Optional[bytes] = None
    scores_bytes: Optional[bytes] = None
    try:
        with zipfile.ZipFile(path, "r") as zf:
            member = _find_graphml_member(zf)
            graphml_bytes = zf.read(member)
            # Recover the maltego_mcp sidecars if this graph was written by us.
            # Absent for plain Maltego graphs -- that's fine.
            names = {n.replace("\\", "/") for n in zf.namelist()}
            if MEMORY_MEMBER in names:
                memory_bytes = zf.read(MEMORY_MEMBER)
            if SCORES_MEMBER in names:
                scores_bytes = zf.read(SCORES_MEMBER)
    except zipfile.BadZipFile as exc:
        raise MtgxParseError(
            f"'{path}' is not a valid .mtgx archive (not a ZIP file)."
        ) from exc

    name = os.path.splitext(os.path.basename(path))[0]
    graph = parse_graphml(graphml_bytes, name)
    graph.source_path = os.path.abspath(path)

    if memory_bytes is not None:
        try:
            graph.memory.load_dict(json.loads(memory_bytes.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            # Corrupt sidecar: ignore rather than fail the whole load.
            pass

    if scores_bytes is not None:
        try:
            scored = json.loads(scores_bytes.decode("utf-8"))
            for entity in graph.entities:
                if entity.id in scored:
                    entity.scores = {k: float(v) for k, v in scored[entity.id].items()}
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass

    return graph


def list_mtgx_in_dir(directory: str) -> List[str]:
    """Return absolute paths of ``.mtgx`` files in ``directory`` (non-recursive)."""

    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.abspath(os.path.join(directory, f))
        for f in os.listdir(directory)
        if f.lower().endswith(".mtgx")
    )
