"""Serialize a :class:`~maltego_mcp.graph.graph_store.Graph` to a ``.mtgx`` file.

A Maltego ``.mtgx`` file is a ZIP archive. The essential member that the
Maltego client reads is ``Graphs/Graph1.graphml`` -- a GraphML document that
embeds Maltego-specific ``<mtg:MaltegoEntity>`` / ``<mtg:MaltegoLink>`` payloads
inside the standard GraphML ``<node>`` / ``<edge>`` elements.

The XML shape produced here matches a real Maltego export:

    <graphml xmlns="http://graphml.graphdrawing.org/xmlns"
             xmlns:mtg="http://maltego.paterva.com/xml/mtgx"
             xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <key id="d4" for="node" attr.name="MaltegoEntity"/>
      <key id="d6" for="edge" attr.name="MaltegoLink"/>
      <graph edgedefault="directed">
        <node id="n0">
          <data key="d4">
            <mtg:MaltegoEntity type="maltego.Domain">
              <mtg:Properties>
                <mtg:Property name="fqdn" displayName="Domain Name" type="string">
                  <mtg:Value>example.com</mtg:Value>
                </mtg:Property>
              </mtg:Properties>
            </mtg:MaltegoEntity>
          </data>
        </node>
        <edge id="e0" source="n0" target="n1">
          <data key="d6">
            <mtg:MaltegoLink type="maltego.link.manual-link">
              <mtg:Properties>
                <mtg:Property name="maltego.link.manual.type" displayName="Label" type="string">
                  <mtg:Value>resolves to</mtg:Value>
                </mtg:Property>
              </mtg:Properties>
            </mtg:MaltegoLink>
          </data>
        </edge>
      </graph>
    </graphml>
"""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from maltego_mcp import entities as entity_catalog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from maltego_mcp.graph.graph_store import Entity, Graph, Link

# Namespaces (match a real Maltego export).
GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"
MTG_NS = "http://maltego.paterva.com/xml/mtgx"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
YFILES_NS = "http://www.yworks.com/xml/graphml"

# GraphML <key> ids, mirroring Maltego's own files exactly:
#   d0 graphml resources, d1-d3 port graphics, d4 MaltegoEntity (node),
#   d5 node graphics (holds Maltego's EntityRenderer/Position), d6 MaltegoLink
#   (edge), d7 edge graphics.
RESOURCES_KEY = "d0"
NODE_KEY = "d4"
NODE_GRAPHICS_KEY = "d5"
EDGE_KEY = "d6"

# Link constants used by Maltego for manually-created links.
MANUAL_LINK_TYPE = "maltego.link.manual-link"
MANUAL_LINK_LABEL_PROP = "maltego.link.manual.type"

# Default grid placement for entities that have no explicit layout position.
# Maltego reads a "center" from every node's EntityRenderer/Position; a missing
# position is what triggers its NullPointerException on import, so we ALWAYS emit
# one.
_GRID_SPACING_X = 220.0
_GRID_SPACING_Y = 160.0
_GRID_COLS = 6

# The single graphml file Maltego reads inside the archive.
GRAPH_MEMBER = "Graphs/Graph1.graphml"

# Sidecar member holding maltego_mcp Investigation Memory. Maltego ignores
# unknown archive members, so this never affects graph compatibility.
MEMORY_MEMBER = "maltego_mcp/investigation_memory.json"

# Sidecar member holding cached per-entity intelligence-quality scores.
SCORES_MEMBER = "maltego_mcp/entity_scores.json"


def _q(ns: str, tag: str) -> str:
    """Build a Clark-notation qualified tag name for ElementTree."""

    return f"{{{ns}}}{tag}"


def _positions_for(graph: "Graph") -> dict:
    """Return an (x, y) position for EVERY entity.

    Uses each entity's explicit ``position`` when set; otherwise assigns a
    deterministic grid slot so Maltego always finds a node center on import.
    """

    positions = {}
    fallback = 0
    for entity in graph.entities:
        if entity.position is not None:
            positions[entity.id] = (float(entity.position[0]), float(entity.position[1]))
        else:
            col = fallback % _GRID_COLS
            row = fallback // _GRID_COLS
            positions[entity.id] = (col * _GRID_SPACING_X, row * _GRID_SPACING_Y)
            fallback += 1
    return positions


def _property_element(name: str, display_name: str, value: str, ptype: str = "string") -> ET.Element:
    prop = ET.Element(
        _q(MTG_NS, "Property"),
        {"name": name, "displayName": display_name, "type": ptype},
    )
    val = ET.SubElement(prop, _q(MTG_NS, "Value"))
    val.text = value
    return prop


def _entity_node(entity: "Entity", x: float, y: float) -> ET.Element:
    node = ET.Element(_q(GRAPHML_NS, "node"), {"id": entity.id})
    data = ET.SubElement(node, _q(GRAPHML_NS, "data"), {"key": NODE_KEY})
    mtg_entity = ET.SubElement(
        data, _q(MTG_NS, "MaltegoEntity"), {"type": entity.type_id}
    )
    props = ET.SubElement(mtg_entity, _q(MTG_NS, "Properties"))

    # Main value property first.
    main_name = entity_catalog.main_property_for(entity.type_id)
    main_display = entity_catalog.main_display_name_for(entity.type_id)
    props.append(_property_element(main_name, main_display, entity.value))

    # Additional properties (skip a duplicate of the main property name).
    for name, value in entity.properties.items():
        if name == main_name:
            continue
        props.append(_property_element(name, name, str(value)))

    if entity.notes:
        note = ET.SubElement(mtg_entity, _q(MTG_NS, "Notes"))
        note.text = entity.notes

    # Maltego's native node position lives in the d5 (nodegraphics) data as an
    # EntityRenderer/Position. This is what Maltego reads as the node "center".
    gdata = ET.SubElement(node, _q(GRAPHML_NS, "data"), {"key": NODE_GRAPHICS_KEY})
    renderer = ET.SubElement(gdata, _q(MTG_NS, "EntityRenderer"))
    ET.SubElement(renderer, _q(MTG_NS, "Position"), {"x": f"{x:.1f}", "y": f"{y:.1f}"})
    return node


def _link_edge(link: "Link") -> ET.Element:
    edge = ET.Element(
        _q(GRAPHML_NS, "edge"),
        {"id": link.id, "source": link.source_id, "target": link.target_id},
    )
    data = ET.SubElement(edge, _q(GRAPHML_NS, "data"), {"key": EDGE_KEY})
    mtg_link = ET.SubElement(
        data, _q(MTG_NS, "MaltegoLink"), {"type": MANUAL_LINK_TYPE}
    )
    props = ET.SubElement(mtg_link, _q(MTG_NS, "Properties"))
    props.append(
        _property_element(MANUAL_LINK_LABEL_PROP, "Label", link.label or "")
    )
    return edge


def _add_key(root: ET.Element, attrs: dict) -> None:
    ET.SubElement(root, _q(GRAPHML_NS, "key"), attrs)


def build_graphml(graph: "Graph") -> bytes:
    """Return the GraphML document for ``graph`` as UTF-8 encoded bytes.

    The structure mirrors a real Maltego export: the full d0-d7 key set, a
    ``graph id="G"``, nodes carrying both a MaltegoEntity (d4) and a native
    EntityRenderer/Position (d5), edges carrying a MaltegoLink (d6), and a
    trailing yFiles resources element.
    """

    # Register namespaces so ElementTree emits the expected prefixes.
    ET.register_namespace("", GRAPHML_NS)
    ET.register_namespace("mtg", MTG_NS)
    ET.register_namespace("xsi", XSI_NS)
    ET.register_namespace("y", YFILES_NS)

    root = ET.Element(_q(GRAPHML_NS, "graphml"))

    # Key declarations, exactly as Maltego writes them.
    _add_key(root, {"for": "graphml", "id": RESOURCES_KEY, "yfiles.type": "resources"})
    _add_key(root, {"for": "port", "id": "d1", "yfiles.type": "portgraphics"})
    _add_key(root, {"for": "port", "id": "d2", "yfiles.type": "portgeometry"})
    _add_key(root, {"for": "port", "id": "d3", "yfiles.type": "portuserdata"})
    _add_key(root, {"attr.name": "MaltegoEntity", "for": "node", "id": NODE_KEY})
    _add_key(root, {"for": "node", "id": NODE_GRAPHICS_KEY, "yfiles.type": "nodegraphics"})
    _add_key(root, {"attr.name": "MaltegoLink", "for": "edge", "id": EDGE_KEY})
    _add_key(root, {"for": "edge", "id": "d7", "yfiles.type": "edgegraphics"})

    positions = _positions_for(graph)

    graph_el = ET.SubElement(
        root, _q(GRAPHML_NS, "graph"), {"edgedefault": "directed", "id": "G"}
    )
    for entity in graph.entities:
        x, y = positions[entity.id]
        graph_el.append(_entity_node(entity, x, y))
    for link in graph.links:
        graph_el.append(_link_edge(link))

    # Trailing yFiles resources element (present in real Maltego graphs).
    res_data = ET.SubElement(root, _q(GRAPHML_NS, "data"), {"key": RESOURCES_KEY})
    ET.SubElement(res_data, _q(YFILES_NS, "Resources"))

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ("<?xml version='1.0' encoding='UTF-8'?>\n" + body).encode("utf-8")


def write_mtgx(graph: "Graph", path: str) -> str:
    """Write ``graph`` to ``path`` as a Maltego ``.mtgx`` archive.

    Returns the path written. The archive contains the GraphML graph plus a
    minimal ``version.properties`` member, and maltego_mcp sidecar members
    (Investigation Memory, entity scores) that Maltego ignores.
    """

    graphml_bytes = build_graphml(graph)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(GRAPH_MEMBER, graphml_bytes)
        zf.writestr(
            "version.properties",
            "maltego.client.version=4.5.0\n"
            "maltego.client.subtitle=\n"
            "maltego.graph.version=1.2\n",
        )
        # Persist Investigation Memory as a sidecar JSON member (only if present).
        memory = getattr(graph, "memory", None)
        if memory is not None and not memory.is_empty():
            zf.writestr(
                MEMORY_MEMBER,
                json.dumps(memory.to_dict(), indent=2, ensure_ascii=False),
            )
        # Persist cached entity scores as a sidecar (only entities that have them).
        scored = {e.id: e.scores for e in graph.entities if e.scores is not None}
        if scored:
            zf.writestr(
                SCORES_MEMBER,
                json.dumps(scored, indent=2, ensure_ascii=False),
            )
    return path
