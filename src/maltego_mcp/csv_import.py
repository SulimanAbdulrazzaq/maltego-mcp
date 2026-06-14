"""Build graph entities from CSV files.

Supports a simple, CE-friendly schema::

    type,value
    Domain,example.com
    Email,user@example.com
    IPv4Address,1.2.3.4

The ``type`` column accepts both friendly aliases (``Domain``, ``Email``,
``IP``) and fully-qualified Maltego type ids (``maltego.Domain``). Optional
``notes`` and ``link_to`` columns are honoured when present:

    type,value,notes,link_to
    Domain,example.com,seed,
    IPv4Address,1.2.3.4,,example.com

A ``link_to`` value links the row's entity to an earlier row whose value
matches (creating a relationship without manual id wrangling).

Entities are deduplicated via the graph's normal dedupe logic. Rows with an
unknown/unsupported type are skipped and reported as warnings rather than
aborting the whole import.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Dict, List, Optional, Tuple

from maltego_mcp import entities as entity_catalog
from maltego_mcp.graph.graph_store import Graph

# Friendly aliases -> Maltego type ids. Keys are compared case-insensitively.
TYPE_ALIASES: Dict[str, str] = {
    "domain": "maltego.Domain",
    "dns": "maltego.DNSName",
    "dnsname": "maltego.DNSName",
    "email": "maltego.EmailAddress",
    "emailaddress": "maltego.EmailAddress",
    "ip": "maltego.IPv4Address",
    "ipv4": "maltego.IPv4Address",
    "ipv4address": "maltego.IPv4Address",
    "ipv6": "maltego.IPv6Address",
    "ipv6address": "maltego.IPv6Address",
    "url": "maltego.URL",
    "website": "maltego.Website",
    "person": "maltego.Person",
    "phone": "maltego.PhoneNumber",
    "phonenumber": "maltego.PhoneNumber",
    "company": "maltego.Company",
    "organization": "maltego.Organization",
    "netblock": "maltego.Netblock",
    "as": "maltego.AS",
    "hash": "maltego.Hash",
    "phrase": "maltego.Phrase",
}


def resolve_type(raw: str) -> Optional[str]:
    """Map a CSV ``type`` cell to a Maltego type id, or ``None`` if unknown."""

    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("maltego."):
        return raw
    return TYPE_ALIASES.get(raw.lower())


def import_csv_text(graph: Graph, text: str) -> Dict[str, object]:
    """Import entities/links from CSV ``text`` into ``graph``.

    Returns a report dict::

        {
          "entities_added": int,
          "entities_reused": int,
          "links_added": int,
          "rows_skipped": int,
          "warnings": [str, ...],
        }
    """

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return {
            "entities_added": 0,
            "entities_reused": 0,
            "links_added": 0,
            "rows_skipped": 0,
            "warnings": ["CSV is empty or has no header row."],
        }

    # Normalize header names (case-insensitive, stripped).
    field_map = {name.strip().lower(): name for name in reader.fieldnames}
    if "type" not in field_map or "value" not in field_map:
        return {
            "entities_added": 0,
            "entities_reused": 0,
            "links_added": 0,
            "rows_skipped": 0,
            "warnings": [
                "CSV must have 'type' and 'value' columns. Found: "
                + ", ".join(reader.fieldnames)
            ],
        }

    type_col = field_map["type"]
    value_col = field_map["value"]
    notes_col = field_map.get("notes")
    link_col = field_map.get("link_to")

    added = reused = links_added = skipped = 0
    warnings: List[str] = []
    # Map a row value -> entity id, so link_to can reference earlier rows.
    value_to_id: Dict[str, str] = {}

    for line_no, row in enumerate(reader, start=2):  # line 1 is the header
        raw_type = (row.get(type_col) or "").strip()
        value = (row.get(value_col) or "").strip()
        if not value:
            skipped += 1
            warnings.append(f"Row {line_no}: empty value, skipped.")
            continue
        type_id = resolve_type(raw_type)
        if type_id is None:
            skipped += 1
            warnings.append(f"Row {line_no}: unknown type '{raw_type}', skipped.")
            continue

        notes = (row.get(notes_col) or "").strip() if notes_col else ""
        before = graph.entity_count()
        entity = graph.add_entity(type_id=type_id, value=value, notes=notes, dedupe=True)
        if graph.entity_count() > before:
            added += 1
        else:
            reused += 1
        value_to_id[value] = entity.id

        link_target_value = (row.get(link_col) or "").strip() if link_col else ""
        if link_target_value:
            target_id = value_to_id.get(link_target_value)
            if target_id and target_id != entity.id:
                graph.add_link(target_id, entity.id, "csv link")
                links_added += 1
            else:
                warnings.append(
                    f"Row {line_no}: link_to '{link_target_value}' not found among "
                    "earlier rows; link skipped."
                )

    return {
        "entities_added": added,
        "entities_reused": reused,
        "links_added": links_added,
        "rows_skipped": skipped,
        "warnings": warnings,
    }


def import_csv_file(graph: Graph, path: str) -> Dict[str, object]:
    """Import entities/links from a CSV file at ``path`` into ``graph``."""

    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return import_csv_text(graph, fh.read())


def export_csv_text(graph: Graph) -> str:
    """Serialize a graph's entities to CSV text (round-trips with import).

    Emits the ``type,value,notes,link_to`` schema. ``type`` uses the full Maltego
    type id. ``link_to`` carries the value of one entity this row links *from*
    (so re-importing reconstructs the relationships); entities are emitted in a
    stable id order so a link target always appears before the rows referencing it
    where possible.
    """

    # Map entity id -> value for resolving link_to references.
    id_to_value = {e.id: e.value for e in graph.entities}
    # For each entity, find one incoming link (a source pointing at it) to encode
    # as the row's link_to (deterministic: lowest source id).
    incoming_source: Dict[str, str] = {}
    for link in sorted(graph.links, key=lambda l: (l.source_id, l.target_id)):
        incoming_source.setdefault(link.target_id, id_to_value.get(link.source_id, ""))

    def _id_key(e):
        return int(e.id[1:]) if e.id.startswith("n") and e.id[1:].isdigit() else 0

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["type", "value", "notes", "link_to"])
    for entity in sorted(graph.entities, key=_id_key):
        writer.writerow(
            [
                entity.type_id,
                entity.value,
                entity.notes,
                incoming_source.get(entity.id, ""),
            ]
        )
    return buf.getvalue()


def export_csv_file(graph: Graph, path: str) -> str:
    """Write the graph's entities as CSV to ``path``. Returns the path."""

    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(export_csv_text(graph))
    return path
