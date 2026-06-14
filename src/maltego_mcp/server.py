#!/usr/bin/env python3
"""MCP server for driving Maltego CE investigation graphs.

Maltego Community Edition has no live API, so this server uses Maltego's native
``.mtgx`` graph file (a ZIP of GraphML XML) as the integration surface. Tools
let an LLM build, read, and edit an investigation graph held in memory, run
transforms to expand it, and save it as a ``.mtgx`` file the user opens (or
refreshes) inside Maltego CE.

Run locally over stdio::

    python -m maltego_mcp.server
    # or, once installed:
    maltego-mcp
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from maltego_mcp import analysis, entities as entity_catalog, layout, reporting
from maltego_mcp import csv_import, learning, machines, recommendation, scoring
from maltego_mcp.detect import detect
from maltego_mcp.events import REPORT_GENERATED, RECOMMENDATION_UPDATED, bus
from maltego_mcp.formatting import (
    ResponseFormat,
    entities_markdown,
    entity_line,
    error,
    graph_summary_markdown,
    handle_exception,
    to_json,
)
from maltego_mcp.graph.graph_store import Graph, GraphStore
from maltego_mcp.graph.mtgx_reader import read_mtgx
from maltego_mcp.graph.mtgx_writer import write_mtgx
from maltego_mcp.models import (
    AddEntityInput,
    AddLinkInput,
    AnalysisLimitInput,
    ApplyLayoutInput,
    CreateGraphInput,
    DeleteEntityInput,
    DeleteGraphInput,
    DeleteLinkInput,
    ExplainEntityInput,
    ExplainTransformInput,
    ExplainWhyInput,
    ExportPathInput,
    ExportReportInput,
    GenerateReportInput,
    GetEntityInput,
    GetEventsInput,
    ImportCsvInput,
    ImportGraphInput,
    InvestigateInput,
    InvestigateQueryInput,
    ListEntitiesInput,
    ListEntityTypesInput,
    ListLinksInput,
    ListStepsInput,
    ListTransformsInput,
    LoadGraphInput,
    OpenGraphInput,
    RankEntitiesInput,
    RenameGraphInput,
    ReportFormat,
    RunMachineInput,
    RunTransformInput,
    SaveGraphInput,
    ScoreEntityInput,
    SetActiveGraphInput,
    SummarizeGraphInput,
    UpdateEntityInput,
)
from maltego_mcp.orchestration import expand, run_and_record
from maltego_mcp.transforms import providers as provider_registry, registry

mcp = FastMCP("maltego_mcp")

# Single source of truth for all open graphs in this process.
STORE = GraphStore()


def _active_or_create(default_name: str) -> Graph:
    """Return the active graph, creating a named one if none is active yet.

    Lets the high-level investigate_* tools work even before the user has
    explicitly created a graph.
    """

    try:
        return STORE.active_graph()
    except RuntimeError:
        # Ensure a unique name if the default is already taken.
        name = default_name
        suffix = 1
        while any(g.name == name for g in STORE.list_graphs()):
            suffix += 1
            name = f"{default_name} ({suffix})"
        return STORE.create_graph(name)


async def _investigate(seed_type: str, params: InvestigateInput, label: str) -> str:
    """Shared body for investigate_domain/email/ip."""

    try:
        graph = _active_or_create(f"{label}-{params.value}")
        seed = graph.add_entity(type_id=seed_type, value=params.value, dedupe=True)
        report = await expand(
            graph,
            seed.id,
            transform_names=None,
            allow_network=params.allow_network,
            available_only=True,
            max_rounds=params.max_rounds,
        )
        data = report.to_dict()
        lines = [
            f"Investigated {label} '{params.value}' on graph '{graph.name}':",
            f"- ran {data['transforms_run']} transform(s) over {data['rounds']} round(s)",
            f"- added {data['entities_added']} entities and {data['links_added']} links",
            f"- graph now has {graph.entity_count()} entities, {graph.link_count()} links",
        ]
        if data["skipped_unavailable"]:
            lines.append(
                "- skipped (no API key): " + ", ".join(data["skipped_unavailable"])
            )
        lines.append("Use maltego_summarize_graph or maltego_generate_report for details.")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- graph management tools --------------------------------------------------
@mcp.tool(
    name="maltego_create_graph",
    annotations={
        "title": "Create Maltego Graph",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_create_graph(params: CreateGraphInput) -> str:
    """Create a new, empty in-memory Maltego graph and make it active.

    The graph lives in server memory until you save it with maltego_save_graph,
    which writes a .mtgx file that opens in Maltego CE. Subsequent entity/link
    operations target the active graph.

    Args:
        params (CreateGraphInput):
            - name (str): Unique name for the new graph.

    Returns:
        str: Confirmation message including the graph name.
    """
    try:
        graph = STORE.create_graph(params.name)
        return f"Created graph '{graph.name}' and set it active. It currently has 0 entities."
    except Exception as exc:  # noqa: BLE001 - mapped to actionable message
        return handle_exception(exc)


@mcp.tool(
    name="maltego_open_graph",
    annotations={
        "title": "Open Maltego Graph (.mtgx)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_open_graph(params: OpenGraphInput) -> str:
    """Open an existing Maltego ``.mtgx`` file into memory and make it active.

    Parses the GraphML inside the archive into entities and links so they can be
    inspected and edited. The graph remembers its source path so maltego_save_graph
    can re-save in place.

    Args:
        params (OpenGraphInput):
            - path (str): Path to an existing .mtgx file.

    Returns:
        str: Summary of the loaded graph (entity/link counts) or an error.
    """
    try:
        graph = read_mtgx(params.path)
        STORE.add_graph(graph)
        return (
            f"Opened '{graph.name}' from {graph.source_path}: "
            f"{graph.entity_count()} entities, {graph.link_count()} links. It is now active."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_save_graph",
    annotations={
        "title": "Save Maltego Graph (.mtgx)",
        "readOnlyHint": False,
        "destructiveHint": True,  # may overwrite an existing file
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_save_graph(params: SaveGraphInput) -> str:
    """Save a graph to a Maltego ``.mtgx`` file that opens in Maltego CE.

    Writes the active graph (or the named graph) as a .mtgx archive. If no path
    is supplied, re-saves to the path the graph was opened from. Open the
    resulting file in Maltego CE via File > Open, or refresh it if already open.

    Args:
        params (SaveGraphInput):
            - path (Optional[str]): Destination .mtgx path. Defaults to the
              graph's original source path.
            - graph_name (Optional[str]): Graph to save; defaults to active graph.

    Returns:
        str: The absolute path written, or an actionable error.
    """
    try:
        graph = (
            STORE.get_graph(params.graph_name)
            if params.graph_name
            else STORE.active_graph()
        )
        target = params.path or graph.source_path
        if not target:
            return error(
                "No path given and this graph has no source path. Provide 'path', "
                "e.g. 'C:/Users/me/Desktop/case.mtgx'."
            )
        if not target.lower().endswith(".mtgx"):
            target = f"{target}.mtgx"
        write_mtgx(graph, target)
        graph.source_path = target
        # Persist cross-investigation learning (no-op unless learning is enabled).
        learning.store.flush()
        return (
            f"Saved '{graph.name}' ({graph.entity_count()} entities, "
            f"{graph.link_count()} links) to {target}. Open it in Maltego CE."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_list_graphs",
    annotations={
        "title": "List Open Graphs",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_graphs() -> str:
    """List all graphs currently open in the server and mark the active one.

    Returns:
        str: Markdown bullet list of open graphs with entity/link counts, or a
        note if none are open.
    """
    try:
        graphs = STORE.list_graphs()
        if not graphs:
            return "No graphs are open. Create one with maltego_create_graph."
        active = STORE.active_name
        lines = ["# Open graphs", ""]
        for g in graphs:
            lines.append(graph_summary_markdown(g.summary(), is_active=(g.name == active)))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_set_active_graph",
    annotations={
        "title": "Set Active Graph",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_set_active_graph(params: SetActiveGraphInput) -> str:
    """Make a previously-opened graph the active target for entity/link operations.

    Args:
        params (SetActiveGraphInput):
            - name (str): Name of an open graph (see maltego_list_graphs).

    Returns:
        str: Confirmation or an actionable error.
    """
    try:
        graph = STORE.set_active(params.name)
        return f"Active graph is now '{graph.name}' ({graph.entity_count()} entities)."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- entity tools ------------------------------------------------------------
@mcp.tool(
    name="maltego_add_entity",
    annotations={
        "title": "Add Entity",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_add_entity(params: AddEntityInput) -> str:
    """Add an entity (node) to the active graph.

    Use this to place domains, IPs, people, emails, etc. on the investigation
    graph. By default duplicate entities (same type + value) are merged rather
    than re-created. Unknown ``maltego.*`` types are accepted so investigations
    are never blocked by a missing catalog entry.

    Args:
        params (AddEntityInput):
            - type (str): Maltego entity type id (e.g. 'maltego.Domain').
            - value (str): Primary value (e.g. 'example.com').
            - properties (Optional[Dict[str,str]]): Extra properties.
            - notes (Optional[str]): Free-text notes.
            - dedupe (bool): Reuse existing identical entity (default True).

    Returns:
        str: Confirmation including the new (or existing) entity id, plus a hint
        if the entity type is not in the known catalog.
    """
    try:
        graph = STORE.active_graph()
        entity = graph.add_entity(
            type_id=params.type,
            value=params.value,
            properties=params.properties,
            notes=params.notes or "",
            dedupe=params.dedupe,
        )
        hint = ""
        if not entity_catalog.is_known_type(params.type):
            hint = (
                f" Note: '{params.type}' is not in the built-in catalog; it will "
                "still be saved, but verify the type id is valid in Maltego."
            )
        return f"Added entity {entity.id}: {entity.value} ({entity.type_id}).{hint}"
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_add_link",
    annotations={
        "title": "Add Link",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_add_link(params: AddLinkInput) -> str:
    """Add a directed link (edge) between two existing entities on the active graph.

    Args:
        params (AddLinkInput):
            - source_id (str): Source entity id (e.g. 'n0').
            - target_id (str): Target entity id (e.g. 'n1').
            - label (Optional[str]): Label drawn on the link.

    Returns:
        str: Confirmation including the new link id, or an actionable error if an
        endpoint id does not exist.
    """
    try:
        graph = STORE.active_graph()
        link = graph.add_link(params.source_id, params.target_id, params.label or "")
        label = f" ('{link.label}')" if link.label else ""
        return f"Added link {link.id}: {link.source_id} -> {link.target_id}{label}."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_list_entities",
    annotations={
        "title": "List Entities",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_entities(params: ListEntitiesInput) -> str:
    """List entities on the active graph, with optional filtering and pagination.

    Args:
        params (ListEntitiesInput):
            - type_filter (Optional[str]): Restrict to a Maltego type.
            - value_contains (Optional[str]): Case-insensitive substring filter.
            - limit (int): Max results (1-500, default 50).
            - offset (int): Results to skip (default 0).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: In JSON form, an object:
            {
              "total": int,        # entities matching filters
              "count": int,        # entities in this page
              "offset": int,
              "has_more": bool,
              "next_offset": int | None,
              "entities": [ {id, type, value, properties, notes, weight}, ... ]
            }
        In markdown form, a readable bullet list.
    """
    try:
        graph = STORE.active_graph()
        items = graph.entities
        if params.type_filter:
            items = [e for e in items if e.type_id == params.type_filter]
        if params.value_contains:
            needle = params.value_contains.lower()
            items = [e for e in items if needle in e.value.lower()]

        total = len(items)
        page = items[params.offset : params.offset + params.limit]
        dict_page = [e.to_dict() for e in page]
        has_more = params.offset + len(page) < total
        next_offset = params.offset + len(page) if has_more else None

        if params.response_format == ResponseFormat.JSON:
            return to_json(
                {
                    "total": total,
                    "count": len(page),
                    "offset": params.offset,
                    "has_more": has_more,
                    "next_offset": next_offset,
                    "entities": dict_page,
                }
            )
        meta = {
            "graph": graph.name,
            "total": total,
            "showing": len(page),
            "offset": params.offset,
            "has_more": has_more,
        }
        return entities_markdown("Entities", dict_page, meta)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_get_entity",
    annotations={
        "title": "Get Entity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_get_entity(params: GetEntityInput) -> str:
    """Fetch full details of one entity on the active graph by id.

    Args:
        params (GetEntityInput):
            - entity_id (str): Entity id (e.g. 'n0').
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Entity detail. JSON form is {id, type, value, properties, notes,
        weight}; markdown form is a readable block. Error if the id is unknown.
    """
    try:
        graph = STORE.active_graph()
        entity = graph.get_entity(params.entity_id)
        data = entity.to_dict()
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        lines = [entity_line(data)]
        for name, val in data["properties"].items():
            lines.append(f"    - {name}: {val}")
        if data["notes"]:
            lines.append(f"    - _notes_: {data['notes']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_update_entity",
    annotations={
        "title": "Update Entity",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_update_entity(params: UpdateEntityInput) -> str:
    """Update an entity's value, properties, notes, or weight on the active graph.

    Only the supplied fields change; properties are merged (not replaced).

    Args:
        params (UpdateEntityInput):
            - entity_id (str): Entity to update.
            - value (Optional[str]): New primary value.
            - properties (Optional[Dict[str,str]]): Properties to set/merge.
            - notes (Optional[str]): Replacement notes.
            - weight (Optional[int]): Node weight (0-100).

    Returns:
        str: Confirmation or an actionable error.
    """
    try:
        graph = STORE.active_graph()
        entity = graph.update_entity(
            entity_id=params.entity_id,
            value=params.value,
            properties=params.properties,
            notes=params.notes,
            weight=params.weight,
        )
        return f"Updated entity {entity.id}: {entity.value} ({entity.type_id})."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_delete_entity",
    annotations={
        "title": "Delete Entity",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_delete_entity(params: DeleteEntityInput) -> str:
    """Delete an entity and all links touching it from the active graph.

    Args:
        params (DeleteEntityInput):
            - entity_id (str): Entity to delete.

    Returns:
        str: Confirmation including how many links were removed, or an error.
    """
    try:
        graph = STORE.active_graph()
        removed_links = graph.delete_entity(params.entity_id)
        return (
            f"Deleted entity {params.entity_id} and {len(removed_links)} "
            f"connected link(s)."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_list_entity_types",
    annotations={
        "title": "List Entity Types",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_entity_types(params: ListEntityTypesInput) -> str:
    """List supported Maltego entity types from the built-in catalog.

    Use this to discover valid ``type`` ids for maltego_add_entity. The catalog
    is a curated subset of Maltego's built-in types; custom types also work.

    Args:
        params (ListEntityTypesInput):
            - category (Optional[str]): Filter to one category (e.g.
              'infrastructure', 'personal', 'social', 'organization', 'location').

    Returns:
        str: Markdown grouped by category, listing each type id and its main
        property.
    """
    try:
        grouped = entity_catalog.list_categories()
        if params.category:
            grouped = {k: v for k, v in grouped.items() if k == params.category}
            if not grouped:
                return error(
                    f"Unknown category '{params.category}'. Known categories: "
                    + ", ".join(sorted(entity_catalog.list_categories().keys()))
                )
        lines = ["# Maltego entity types", ""]
        for category, types in sorted(grouped.items()):
            lines.append(f"## {category}")
            for et in types:
                lines.append(
                    f"- `{et.type_id}` — {et.display_name} (main property: `{et.main_property}`)"
                )
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- transform tools ---------------------------------------------------------
@mcp.tool(
    name="maltego_list_transforms",
    annotations={
        "title": "List Transforms",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_transforms(params: ListTransformsInput) -> str:
    """List available transforms, optionally filtered by accepted input type.

    Transforms expand an entity into related entities (e.g. a domain into its IP
    addresses). The built-in 'local' provider needs no API keys; more providers
    (a Maltego API, OSINT services) can be added without changing these tools.

    Args:
        params (ListTransformsInput):
            - input_type (Optional[str]): Only transforms accepting this type.

    Returns:
        str: Markdown list of transforms with name, accepted input types, output
        types, provider, and whether they require network access.
    """
    try:
        transforms = (
            registry.for_type(params.input_type)
            if params.input_type
            else registry.all()
        )
        if not transforms:
            scope = f" accepting '{params.input_type}'" if params.input_type else ""
            return f"No transforms available{scope}."
        lines = ["# Available transforms", ""]
        for t in sorted(transforms, key=lambda x: x.name):
            net = " [network]" if t.requires_network else ""
            lines.append(f"- `{t.name}` — {t.display_name}{net}")
            lines.append(f"    - {t.description}")
            lines.append(f"    - input: {', '.join(t.input_types)}")
            lines.append(f"    - output: {', '.join(t.output_types)} (provider: {t.provider})")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_run_transform",
    annotations={
        "title": "Run Transform",
        "readOnlyHint": False,  # may add entities/links to the graph
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,  # network transforms reach external services
    },
)
async def maltego_run_transform(params: RunTransformInput) -> str:
    """Run a transform on an entity in the active graph to discover related entities.

    Looks up the named transform, runs it against the given input entity, and
    (by default) adds the resulting entities to the active graph with links back
    to the input entity. Results are de-duplicated against existing entities.

    Args:
        params (RunTransformInput):
            - transform_name (str): Transform to run (see maltego_list_transforms).
            - entity_id (str): Input entity id in the active graph.
            - add_to_graph (bool): Add results to the graph (default True).

    Returns:
        str: Summary of discovered entities (and the ids created when added), or
        an actionable error (unknown transform, type mismatch, no results).
    """
    try:
        graph = STORE.active_graph()
        transform = registry.get(params.transform_name)
        if transform is None:
            return error(
                f"Unknown transform '{params.transform_name}'. Use "
                "maltego_list_transforms to see available transforms."
            )
        source = graph.get_entity(params.entity_id)
        if not transform.accepts(source.type_id):
            return error(
                f"Transform '{transform.name}' does not accept type "
                f"'{source.type_id}'. It accepts: {', '.join(transform.input_types)}."
            )

        if not params.add_to_graph:
            # Preview run: execute without mutating the graph or memory.
            result = await transform.run(source.value, dict(source.properties))
            if not result.entities:
                return result.message or "Transform returned no results."
            lines = [f"Preview of '{transform.name}' on {source.id} ({source.value}):"]
            if result.message:
                lines.append(result.message)
            for res in result.entities:
                lines.append(f"- {res.value} ({res.type_id}) [not added]")
            return "\n".join(lines)

        # Run through the shared choke-point so Investigation Memory and events
        # are recorded consistently with the orchestration engine.
        reason = f"Manual run of '{transform.name}' on '{source.value}'."
        added, step = await run_and_record(graph, transform, source, reason)

        lines = [
            f"Transform '{transform.name}' on {source.id} ({source.value}) "
            f"[{step.status}, step {step.step}, exec {step.execution_id}]:"
        ]
        if step.message:
            lines.append(step.message)
        if added:
            for entity in added:
                lines.append(f"- {entity.id} {entity.value} ({entity.type_id})")
        else:
            lines.append("No new entities added (all results were duplicates or none returned).")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- high-level investigation workflows --------------------------------------
@mcp.tool(
    name="maltego_investigate_domain",
    annotations={
        "title": "Investigate Domain",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def maltego_investigate_domain(params: InvestigateInput) -> str:
    """Automatically investigate a domain by adding it and running transforms.

    A one-call workflow: ensures an active graph (creating one if needed), adds a
    Domain entity, then expands the graph by running every applicable + available
    transform (DNS, plus any configured OSINT providers) for a few rounds,
    de-duplicating results. Use this instead of chaining transforms manually.

    Args:
        params (InvestigateInput):
            - value (str): The domain to investigate (e.g. 'example.com').
            - allow_network (bool): Run network transforms (default True).
            - max_rounds (int): Expansion depth 1-4 (default 2).

    Returns:
        str: Summary of how many transforms ran and what was discovered, plus a
        note of any transforms skipped for missing API keys.
    """
    return await _investigate("maltego.Domain", params, "domain")


@mcp.tool(
    name="maltego_investigate_email",
    annotations={
        "title": "Investigate Email",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def maltego_investigate_email(params: InvestigateInput) -> str:
    """Automatically investigate an email address (domain, breaches, footprint).

    Adds an Email Address entity and expands the graph: derives the domain,
    checks breach exposure (if a HaveIBeenPwned key is configured), and expands
    the related domain's basic footprint. See maltego_investigate_domain for the
    shared behaviour and return shape.

    Args:
        params (InvestigateInput):
            - value (str): The email to investigate (e.g. 'bob@example.com').
            - allow_network (bool): Run network transforms (default True).
            - max_rounds (int): Expansion depth 1-4 (default 2).

    Returns:
        str: Summary of what was discovered.
    """
    return await _investigate("maltego.EmailAddress", params, "email")


@mcp.tool(
    name="maltego_investigate_ip",
    annotations={
        "title": "Investigate IP Address",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def maltego_investigate_ip(params: InvestigateInput) -> str:
    """Automatically investigate an IPv4 address (reverse DNS, ports, services).

    Adds an IPv4 Address entity and expands the graph using reverse DNS plus any
    configured host-intelligence providers (Shodan, Censys). See
    maltego_investigate_domain for the shared behaviour and return shape.

    Args:
        params (InvestigateInput):
            - value (str): The IPv4 address (e.g. '8.8.8.8').
            - allow_network (bool): Run network transforms (default True).
            - max_rounds (int): Expansion depth 1-4 (default 2).

    Returns:
        str: Summary of what was discovered.
    """
    return await _investigate("maltego.IPv4Address", params, "ip")


# --- graph import / continuation ---------------------------------------------
@mcp.tool(
    name="maltego_load_graph",
    annotations={
        "title": "Load Graph (.mtgx)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_load_graph(params: LoadGraphInput) -> str:
    """Load an existing .mtgx file as a new active graph (continue an investigation).

    Equivalent to maltego_open_graph: parses all entities, links, properties, and
    any stored layout positions so you can continue editing and re-save (full
    round-trip).

    Args:
        params (LoadGraphInput):
            - path (str): Path to the .mtgx file.

    Returns:
        str: Summary of the loaded graph, or an actionable error.
    """
    try:
        graph = read_mtgx(params.path)
        STORE.add_graph(graph)
        return (
            f"Loaded '{graph.name}' from {graph.source_path}: "
            f"{graph.entity_count()} entities, {graph.link_count()} links. It is now active."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_import_graph",
    annotations={
        "title": "Import/Merge Graph into Active",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_import_graph(params: ImportGraphInput) -> str:
    """Merge a .mtgx file's contents into the ACTIVE graph (combine investigations).

    Unlike maltego_load_graph (which opens a separate graph), this merges another
    graph's entities and links into the current active graph, remapping ids and
    de-duplicating by (type, value) when ``dedupe`` is true.

    Args:
        params (ImportGraphInput):
            - path (str): Path to the .mtgx file to merge in.
            - dedupe (bool): Reuse matching entities instead of duplicating.

    Returns:
        str: Counts of entities/links added and reused, or an actionable error.
    """
    try:
        active = STORE.active_graph()
        other = read_mtgx(params.path)
        stats = active.merge_from(other, dedupe=params.dedupe)
        return (
            f"Merged '{other.name}' into '{active.name}': "
            f"{stats['entities_added']} entities added, "
            f"{stats['entities_reused']} reused, {stats['links_added']} links added. "
            f"Active graph now has {active.entity_count()} entities."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- AI-oriented graph analysis ----------------------------------------------
@mcp.tool(
    name="maltego_summarize_graph",
    annotations={
        "title": "Summarize Graph",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_summarize_graph(params: SummarizeGraphInput) -> str:
    """Summarize the active investigation graph (composition + key entities).

    Deterministic overview: entity/link totals, a breakdown by entity type, the
    most-connected entities, and any isolated entities. Ideal for presenting an
    investigation's state to a user.

    Args:
        params (SummarizeGraphInput):
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: JSON form matches analysis.summarize_graph (name, entity_count,
        link_count, type_breakdown, most_connected, isolated_count, isolated);
        markdown form is a readable brief.
    """
    try:
        graph = STORE.active_graph()
        data = analysis.summarize_graph(graph)
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        lines = [f"# Summary: {data['name']}", ""]
        lines.append(f"- Entities: {data['entity_count']} | Links: {data['link_count']}")
        lines.append("- By type:")
        for type_id, count in data["type_breakdown"].items():
            lines.append(f"    - {entity_catalog.main_display_name_for(type_id)} ({type_id}): {count}")
        if data["most_connected"]:
            lines.append("- Most connected:")
            for e in data["most_connected"]:
                lines.append(f"    - {e['value']} ({e['type']}) — degree {e['degree']} [`{e['id']}`]")
        lines.append(f"- Isolated entities: {data['isolated_count']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_explain_entity",
    annotations={
        "title": "Explain Entity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_explain_entity(params: ExplainEntityInput) -> str:
    """Explain one entity: its data, neighbours, and how it can be expanded.

    Deterministic context for a single node: properties, notes, degree, incoming
    and outgoing neighbours (with link labels), and which transforms apply to it
    (and whether each is currently available).

    Args:
        params (ExplainEntityInput):
            - entity_id (str): Entity id (e.g. 'n0').
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Detailed explanation, or an error if the id is unknown.
    """
    try:
        graph = STORE.active_graph()
        data = analysis.explain_entity(graph, params.entity_id)
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        lines = [f"# {data['value']} ({data['type']}) [`{data['id']}`]", ""]
        lines.append(f"- Degree: {data['degree']}")
        if data["properties"]:
            lines.append("- Properties:")
            for k, v in data["properties"].items():
                lines.append(f"    - {k}: {v}")
        if data["notes"]:
            lines.append(f"- Notes: {data['notes']}")
        if data["outgoing"]:
            lines.append("- Outgoing:")
            for nb in data["outgoing"]:
                lines.append(f"    - → {nb['value']} ({nb['type']}) '{nb['label']}' [`{nb['id']}`]")
        if data["incoming"]:
            lines.append("- Incoming:")
            for nb in data["incoming"]:
                lines.append(f"    - ← {nb['value']} ({nb['type']}) '{nb['label']}' [`{nb['id']}`]")
        avail = [t["name"] for t in data["applicable_transforms"] if t["available"]]
        if avail:
            lines.append("- Available transforms: " + ", ".join(avail))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_identify_pivots",
    annotations={
        "title": "Identify Interesting Pivots",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_identify_pivots(params: AnalysisLimitInput) -> str:
    """Identify the most promising pivot entities in the active graph.

    Deterministically ranks entities that connect many others (degree >= 2),
    which are typically the best points to pivot an investigation (shared IPs,
    central emails, etc.).

    Args:
        params (AnalysisLimitInput):
            - limit (int): Max pivots to return (1-50, default 10).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Ranked pivots with id, value, type, degree, and a reason.
    """
    try:
        graph = STORE.active_graph()
        pivots = analysis.identify_interesting_pivots(graph, limit=params.limit)
        if params.response_format == ResponseFormat.JSON:
            return to_json({"count": len(pivots), "pivots": pivots})
        if not pivots:
            return "No pivot entities found (need entities with 2+ connections)."
        lines = ["# Interesting pivots", ""]
        for p in pivots:
            lines.append(f"- **{p['value']}** ({p['type']}) [`{p['id']}`] — {p['reason']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_suggest_next_steps",
    annotations={
        "title": "Suggest Next Steps",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_suggest_next_steps(params: AnalysisLimitInput) -> str:
    """Suggest concrete next transforms to run on the active graph.

    Deterministically recommends transforms to advance the investigation,
    prioritising the most-connected entities and available transforms first.
    Unavailable (missing-key) transforms are still listed but flagged.

    Args:
        params (AnalysisLimitInput):
            - limit (int): Max suggestions (1-50, default 10).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Suggestions of (transform, target entity, expected output types).
    """
    try:
        graph = STORE.active_graph()
        steps = analysis.suggest_next_steps(graph, limit=params.limit)
        if params.response_format == ResponseFormat.JSON:
            return to_json({"count": len(steps), "suggestions": steps})
        if not steps:
            return "No suggestions: the graph is empty or no transforms apply."
        lines = ["# Suggested next steps", ""]
        for s in steps:
            note = "" if s["available"] else f" ({s['note']})"
            lines.append(
                f"- `{s['transform']}` on {s['on_value']} → "
                f"{', '.join(s['produces'])}{note}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- layout ------------------------------------------------------------------
@mcp.tool(
    name="maltego_apply_layout",
    annotations={
        "title": "Apply Graph Layout",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_apply_layout(params: ApplyLayoutInput) -> str:
    """Compute and assign (x, y) layout positions to entities on the active graph.

    Positions are stored on the graph and persisted into the .mtgx on save. Re-run
    after adding entities to refresh the layout. Layouts are deterministic.

    Args:
        params (ApplyLayoutInput):
            - algorithm (str): 'hierarchical', 'radial', or 'force'.

    Returns:
        str: Confirmation with the number of entities positioned.
    """
    try:
        graph = STORE.active_graph()
        count = layout.apply_layout(graph, params.algorithm)
        return (
            f"Applied '{params.algorithm}' layout: positioned {count} entities on "
            f"'{graph.name}'. Positions will be saved in the .mtgx."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- CSV import --------------------------------------------------------------
@mcp.tool(
    name="maltego_import_csv",
    annotations={
        "title": "Import Entities from CSV",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_import_csv(params: ImportCsvInput) -> str:
    """Build entities (and optional links) on the active graph from CSV.

    Accepts a CSV file path or raw CSV text with a 'type,value' header (optional
    'notes' and 'link_to' columns). The 'type' column accepts friendly aliases
    ('Domain', 'Email', 'IP') or full Maltego type ids. Entities are deduplicated;
    rows with unknown types are skipped and reported.

    Args:
        params (ImportCsvInput):
            - path (Optional[str]): Path to a CSV file, OR
            - content (Optional[str]): Raw CSV text.

    Returns:
        str: Counts of entities added/reused, links added, rows skipped, and any
        per-row warnings.
    """
    try:
        if not params.path and not params.content:
            return error("Provide either 'path' or 'content'.")
        graph = _active_or_create("csv-import")
        if params.path:
            report = csv_import.import_csv_file(graph, params.path)
        else:
            report = csv_import.import_csv_text(graph, params.content or "")
        lines = [
            f"CSV import into '{graph.name}':",
            f"- entities added: {report['entities_added']}",
            f"- entities reused: {report['entities_reused']}",
            f"- links added: {report['links_added']}",
            f"- rows skipped: {report['rows_skipped']}",
        ]
        warnings = report["warnings"]
        if warnings:
            lines.append("- warnings:")
            for w in warnings[:10]:
                lines.append(f"    - {w}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- providers ---------------------------------------------------------------
@mcp.tool(
    name="maltego_list_providers",
    annotations={
        "title": "List OSINT Providers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_providers() -> str:
    """List OSINT transform providers and whether each is configured.

    Shows the built-in 'local' provider plus external providers (VirusTotal,
    Shodan, SecurityTrails, Censys, Hunter.io, HaveIBeenPwned), the environment
    variable(s) each needs, and whether those are currently set.

    Returns:
        str: Markdown list grouped by tier with configuration status and the
        env vars required to enable each provider.
    """
    try:
        lines = ["# OSINT providers", ""]
        for p in provider_registry.all():
            status = "configured" if p.is_configured() else "NOT configured"
            envs = ", ".join(p.env_vars) if p.env_vars else "no key needed"
            lines.append(f"- **{p.display_name}** (`{p.name}`, {p.tier}) — {status}")
            lines.append(f"    - {p.description}")
            lines.append(f"    - env: {envs}")
            if p.website:
                lines.append(f"    - get a key: {p.website}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- reporting ---------------------------------------------------------------
def _render_report(graph: Graph, fmt: ReportFormat) -> str:
    if fmt == ReportFormat.HTML:
        return reporting.build_html_report(graph)
    return reporting.build_markdown_report(graph)


@mcp.tool(
    name="maltego_generate_report",
    annotations={
        "title": "Generate Investigation Report",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_generate_report(params: GenerateReportInput) -> str:
    """Generate a deterministic investigation report for the active graph.

    The report includes an executive summary, key findings (pivots), an entity
    inventory by type, relationship highlights, and suggested next steps. Returns
    the report text inline (use maltego_export_report to write it to a file).

    Args:
        params (GenerateReportInput):
            - format (ReportFormat): 'markdown' (default) or 'html'.

    Returns:
        str: The full report as Markdown or HTML text.
    """
    try:
        graph = STORE.active_graph()
        text = _render_report(graph, params.format)
        bus.emit(REPORT_GENERATED, {"graph": graph.name, "format": params.format.value})
        return text
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_export_report",
    annotations={
        "title": "Export Investigation Report to File",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_export_report(params: ExportReportInput) -> str:
    """Write a deterministic investigation report for the active graph to a file.

    Same content as maltego_generate_report, written to ``path``. Use a .md path
    for markdown or .html for HTML.

    Args:
        params (ExportReportInput):
            - path (str): Destination file path.
            - format (ReportFormat): 'markdown' (default) or 'html'.

    Returns:
        str: Confirmation including the path written.
    """
    try:
        graph = STORE.active_graph()
        text = _render_report(graph, params.format)
        with open(params.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        bus.emit(REPORT_GENERATED, {"graph": graph.name, "format": params.format.value, "path": params.path})
        return f"Wrote {params.format.value} report for '{graph.name}' to {params.path}."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- investigation machines --------------------------------------------------
@mcp.tool(
    name="maltego_list_machines",
    annotations={
        "title": "List Investigation Machines",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_machines() -> str:
    """List available investigation machines (reusable workflow templates).

    Machines run a curated set of transforms over several rounds (e.g. 'Passive
    Domain Investigation'). Run one with maltego_run_machine.

    Returns:
        str: Markdown list of machines with their seed type and description.
    """
    try:
        lines = ["# Investigation machines", ""]
        for m in machines.registry.all():
            lines.append(f"- `{m.name}` — {m.display_name}")
            lines.append(f"    - {m.description}")
            lines.append(f"    - seed type: {m.seed_type}, rounds: {m.max_rounds}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_run_machine",
    annotations={
        "title": "Run Investigation Machine",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def maltego_run_machine(params: RunMachineInput) -> str:
    """Run an investigation machine against a seed value on the active graph.

    Ensures an active graph (creating one if needed), seeds the machine's entity
    type with ``seed_value``, and expands the graph per the machine's recipe.
    Missing/unavailable transforms are skipped gracefully.

    Args:
        params (RunMachineInput):
            - machine_name (str): e.g. 'passive_domain' (see maltego_list_machines).
            - seed_value (str): Seed value (domain/email/ip).
            - allow_network (Optional[bool]): Override the machine's network setting.
            - max_rounds (Optional[int]): Override expansion depth.

    Returns:
        str: Summary of what the machine discovered, or an actionable error.
    """
    try:
        machine = machines.registry.get(params.machine_name)
        if machine is None:
            return error(
                f"Unknown machine '{params.machine_name}'. Use maltego_list_machines."
            )
        graph = _active_or_create(f"{params.machine_name}-{params.seed_value}")
        report = await machines.run_machine(
            graph,
            params.machine_name,
            params.seed_value,
            allow_network=params.allow_network,
            max_rounds=params.max_rounds,
        )
        data = report.to_dict()
        lines = [
            f"Ran machine '{params.machine_name}' on '{params.seed_value}' "
            f"(graph '{graph.name}'):",
            f"- transforms run: {data['transforms_run']} over {data['rounds']} round(s)",
            f"- entities added: {data['entities_added']}, links added: {data['links_added']}",
            f"- graph total: {graph.entity_count()} entities, {graph.link_count()} links",
        ]
        if data["skipped_unavailable"]:
            lines.append("- skipped (no API key): " + ", ".join(data["skipped_unavailable"]))
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- Investigation Memory ----------------------------------------------------
@mcp.tool(
    name="maltego_list_investigation_steps",
    annotations={
        "title": "List Investigation Steps",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_investigation_steps(params: ListStepsInput) -> str:
    """List recorded investigation steps (procedural memory) for the active graph.

    Each step records a transform execution: what ran, why, the trigger entity,
    what it discovered, status, and importance. This is the investigation's
    reasoning trace, not just its data.

    Args:
        params (ListStepsInput):
            - limit/offset (int): Pagination.
            - status (Optional[str]): Filter by 'success', 'empty', or 'error'.
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: JSON form is {total, count, offset, steps:[...]}; markdown is a list.
    """
    try:
        graph = STORE.active_graph()
        steps = graph.memory.timeline()
        if params.status:
            steps = [s for s in steps if s.status == params.status]
        total = len(steps)
        page = steps[params.offset : params.offset + params.limit]
        if params.response_format == ResponseFormat.JSON:
            return to_json(
                {
                    "total": total,
                    "count": len(page),
                    "offset": params.offset,
                    "steps": [s.to_dict() for s in page],
                }
            )
        if not page:
            return "No investigation steps recorded yet (run transforms or an investigation)."
        lines = [f"# Investigation steps ({total})", ""]
        for s in page:
            lines.append(
                f"- **#{s.step}** `{s.execution_id}` `{s.transform}` on "
                f"{s.trigger_entity_value} → {s.new_entities} new "
                f"[{s.status}, importance {s.importance_score}]"
            )
            lines.append(f"    - why: {s.reason}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_explain_why",
    annotations={
        "title": "Explain Why (entity provenance)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_explain_why(params: ExplainWhyInput) -> str:
    """Explain why an entity is on the graph: which step/transform discovered it.

    Uses Investigation Memory to trace an entity's provenance — the transform
    that produced it, the entity that triggered that transform, and the recorded
    reason. Seed entities (added manually or via CSV) report as analyst-provided.

    Args:
        params (ExplainWhyInput):
            - entity_id (str): Entity id (e.g. 'n3').
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Provenance explanation, or an error if the id is unknown.
    """
    try:
        graph = STORE.active_graph()
        entity = graph.get_entity(params.entity_id)
        discovering = graph.memory.discovering_steps(params.entity_id)
        payload = {
            "entity_id": entity.id,
            "value": entity.value,
            "type": entity.type_id,
            "origin": "discovered" if discovering else "analyst-provided (seed)",
            "discovered_by": [s.to_dict() for s in discovering],
        }
        if params.response_format == ResponseFormat.JSON:
            return to_json(payload)
        if not discovering:
            return (
                f"{entity.value} ({entity.type_id}) was provided by the analyst "
                "(seed / manual / CSV import); it was not discovered by a transform."
            )
        lines = [f"# Why is {entity.value} ({entity.type_id}) here?", ""]
        for s in discovering:
            lines.append(
                f"- Discovered by `{s.transform}` (step #{s.step}, `{s.execution_id}`) "
                f"triggered by {s.trigger_entity_value}."
            )
            lines.append(f"    - reason: {s.reason}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_explain_transform",
    annotations={
        "title": "Explain Transform Execution",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_explain_transform(params: ExplainTransformInput) -> str:
    """Explain one recorded transform execution by its execution id.

    Args:
        params (ExplainTransformInput):
            - transform_execution_id (str): e.g. 'x0' (see maltego_list_investigation_steps).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Full step detail, or an error if the execution id is unknown.
    """
    try:
        graph = STORE.active_graph()
        step = graph.memory.get(params.transform_execution_id)
        if step is None:
            return error(
                f"No step with execution id '{params.transform_execution_id}'. "
                "Use maltego_list_investigation_steps."
            )
        if params.response_format == ResponseFormat.JSON:
            return to_json(step.to_dict())
        lines = [
            f"# Step #{step.step} — `{step.transform}` (`{step.execution_id}`)",
            "",
            f"- triggered by: {step.trigger_entity_value} (`{step.trigger_entity_id}`)",
            f"- provider: {step.provider}",
            f"- reason: {step.reason}",
            f"- status: {step.status}; new entities: {step.new_entities}",
            f"- importance: {step.importance_score}; reconsider: {step.reconsider}",
            f"- discovered ids: {', '.join(step.new_entity_ids) or 'none'}",
        ]
        if step.message:
            lines.append(f"- message: {step.message}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_get_investigation_timeline",
    annotations={
        "title": "Get Investigation Timeline",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_get_investigation_timeline() -> str:
    """Return the chronological timeline of the active investigation.

    A compact, ordered narrative of every transform execution recorded in
    Investigation Memory — useful for review, audit, and explaining the path the
    investigation took.

    Returns:
        str: Markdown timeline (one line per step in execution order).
    """
    try:
        graph = STORE.active_graph()
        steps = graph.memory.timeline()
        if not steps:
            return "No steps recorded yet."
        lines = [f"# Investigation timeline: {graph.name}", ""]
        for s in steps:
            lines.append(
                f"{s.step}. [{s.timestamp}] `{s.transform}` on {s.trigger_entity_value} "
                f"→ {s.new_entities} new ({s.status})"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- Next Best Action engine -------------------------------------------------
@mcp.tool(
    name="maltego_next_best_actions",
    annotations={
        "title": "Next Best Actions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_next_best_actions(params: AnalysisLimitInput) -> str:
    """Recommend the most valuable next investigative moves (decision engine).

    A deterministic, explainable ranking that weighs entity importance, expected
    information gain, confidence, provider availability, and — crucially — the
    Investigation Memory (so it never re-suggests a transform already attempted
    on an entity). Supersedes maltego_suggest_next_steps with richer reasoning.

    Args:
        params (AnalysisLimitInput):
            - limit (int): Max recommendations (1-50, default 10).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Ranked recommendations, each with transform, target entity, a
        deterministic score, expected gain, and a plain-English reason.
    """
    try:
        graph = STORE.active_graph()
        recs = recommendation.next_best_actions(graph, limit=params.limit)
        bus.emit(RECOMMENDATION_UPDATED, {"count": len(recs)})
        if params.response_format == ResponseFormat.JSON:
            return to_json({"count": len(recs), "recommendations": recs})
        if not recs:
            return "No further actions: the graph is empty or every pivot has been tried."
        lines = ["# Next best actions", ""]
        for r in recs:
            flag = "" if r["available"] else " [needs API key]"
            lines.append(
                f"- **{r['score']}** `{r['transform']}` on {r['entity_value']}{flag}"
            )
            lines.append(f"    - {r['reason']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- Unified investigation entry point ---------------------------------------
@mcp.tool(
    name="maltego_investigate",
    annotations={
        "title": "Investigate (unified entry point)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def maltego_investigate(params: InvestigateQueryInput) -> str:
    """One-call investigation: detect, build, expand, analyze, and recommend.

    The primary high-level interface. Given any query (domain, email, IPv4/IPv6,
    or URL) it: auto-detects the entity type, ensures an active graph, selects an
    appropriate investigation machine, runs it (recording everything into
    Investigation Memory), applies a layout, scores entities, and returns a
    consolidated briefing with discoveries and ranked next actions.

    Args:
        params (InvestigateQueryInput):
            - query (str): What to investigate (type auto-detected).
            - allow_network (bool): Run network transforms (default True).
            - max_rounds (Optional[int]): Override machine depth.
            - layout (str): Layout to apply ('hierarchical'/'radial'/'force').

    Returns:
        str: A briefing: detected type, investigation stats, important
        discoveries, recommended next actions, and report availability.
    """
    try:
        det = detect(params.query)
        graph = _active_or_create(f"investigate-{det.value}")

        if det.machine:
            report = await machines.run_machine(
                graph,
                det.machine,
                det.value,
                allow_network=params.allow_network,
                max_rounds=params.max_rounds,
            )
            stats = report.to_dict()
        else:
            # Unclassified query: just seed it as a Phrase, no machine.
            graph.add_entity(det.type_id, det.value, dedupe=True)
            stats = {"transforms_run": 0, "entities_added": 0, "rounds": 0, "skipped_unavailable": []}

        layout.apply_layout(graph, params.layout)
        summary = analysis.summarize_graph(graph)
        recs = recommendation.next_best_actions(graph, limit=5)
        bus.emit(RECOMMENDATION_UPDATED, {"count": len(recs)})

        lines = [
            f"# Investigation: {params.query}",
            "",
            f"- Detected as **{det.type_id}** (value `{det.value}`)"
            + (f" — {det.note}" if det.note else ""),
            f"- Machine: {det.machine or 'none (unclassified)'}",
            f"- Graph: '{graph.name}' — {graph.entity_count()} entities, "
            f"{graph.link_count()} links",
            f"- This run: {stats['transforms_run']} transforms over "
            f"{stats.get('rounds', 0)} round(s), {stats.get('entities_added', 0)} new entities",
        ]
        if stats.get("skipped_unavailable"):
            lines.append(
                "- Skipped (no API key): " + ", ".join(stats["skipped_unavailable"])
            )
        lines.append("")
        lines.append("## Important discoveries")
        if summary["most_connected"]:
            for e in summary["most_connected"][:5]:
                lines.append(f"- {e['value']} ({e['type']}) — degree {e['degree']}")
        else:
            lines.append("- (no connected entities yet)")
        lines.append("")
        lines.append("## Recommended next actions")
        if recs:
            for r in recs:
                flag = "" if r["available"] else " [needs API key]"
                lines.append(f"- `{r['transform']}` on {r['entity_value']} (score {r['score']}){flag}")
        else:
            lines.append("- None — every applicable pivot has been tried.")
        lines.append("")
        lines.append(
            "## Reports & next tools\n"
            "- Full report: maltego_generate_report | Save graph: maltego_save_graph\n"
            "- Why an entity is here: maltego_explain_why | Timeline: "
            "maltego_get_investigation_timeline"
        )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- Real-time events --------------------------------------------------------
@mcp.tool(
    name="maltego_subscribe_events",
    annotations={
        "title": "Subscribe to Events (enable real-time mode)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_subscribe_events() -> str:
    """Enable real-time investigation mode and return a subscription id.

    Turns on the event bus's live mode and buffering. Because MCP stdio cannot
    push to the caller, retrieve emitted events by polling maltego_get_recent_events
    (events are also buffered before subscribing). Real-time mode is optional and
    does not change any .mtgx behaviour.

    Returns:
        str: The subscription id and current buffer/sequence state.
    """
    try:
        sub_id = bus.subscribe()
        return (
            f"Subscribed ({sub_id}). Real-time mode is ON. "
            f"{bus.subscriber_count()} subscriber(s); next event seq = {bus.next_seq}. "
            "Poll maltego_get_recent_events to read events."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_get_recent_events",
    annotations={
        "title": "Get Recent Events",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_get_recent_events(params: GetEventsInput) -> str:
    """Return recent investigation events (entity_discovered, transform_*, etc.).

    Reads from the event bus's bounded buffer. Use ``since_seq`` to fetch only
    events newer than a sequence number you've already seen (incremental polling).

    Args:
        params (GetEventsInput):
            - limit (int): Max events (default 50).
            - since_seq (Optional[int]): Only events with seq greater than this.
            - response_format (ResponseFormat): 'json' (default) or 'markdown'.

    Returns:
        str: JSON form is {count, next_seq, events:[{seq,type,timestamp,data}]};
        markdown is a readable list.
    """
    try:
        events = bus.recent(limit=params.limit, since_seq=params.since_seq)
        if params.response_format == ResponseFormat.MARKDOWN:
            if not events:
                return "No events recorded."
            lines = ["# Recent events", ""]
            for e in events:
                lines.append(f"- `{e.seq}` [{e.timestamp}] **{e.type}** {to_json(e.data)}")
            return "\n".join(lines)
        return to_json(
            {
                "count": len(events),
                "next_seq": bus.next_seq,
                "events": [e.to_dict() for e in events],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- Risk & confidence scoring -----------------------------------------------
@mcp.tool(
    name="maltego_score_entity",
    annotations={
        "title": "Score Entity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_score_entity(params: ScoreEntityInput) -> str:
    """Compute intelligence-quality scores for one entity (deterministic).

    Returns confidence, source_reliability, linkage_strength,
    investigation_priority, and novelty in [0, 1], derived from the graph
    structure and Investigation Memory (which providers found the entity).

    Args:
        params (ScoreEntityInput):
            - entity_id (str): Entity id (e.g. 'n0').

    Returns:
        str: JSON object {entity, scores:{confidence, source_reliability,
        linkage_strength, investigation_priority, novelty}}.
    """
    try:
        graph = STORE.active_graph()
        entity = graph.get_entity(params.entity_id)
        scores = scoring.score_entity(graph, params.entity_id)
        return to_json({"entity": entity.value, "id": entity.id, "type": entity.type_id, **scores})
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_rank_entities",
    annotations={
        "title": "Rank Entities by Priority",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_rank_entities(params: RankEntitiesInput) -> str:
    """Rank entities by investigation priority (deterministic).

    Scores every entity and returns them ordered by investigation_priority, so an
    analyst can focus on the most meaningful findings first.

    Args:
        params (RankEntitiesInput):
            - limit (int): Max entities (1-200, default 20).
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: Ranked entities with their scores.
    """
    try:
        graph = STORE.active_graph()
        ranked = scoring.rank_entities(graph, limit=params.limit)
        if params.response_format == ResponseFormat.JSON:
            return to_json({"count": len(ranked), "entities": ranked})
        if not ranked:
            return "No entities to rank."
        lines = ["# Entities by investigation priority", ""]
        for r in ranked:
            sc = r["scores"]
            lines.append(
                f"- **{r['value']}** ({r['type']}) [`{r['id']}`] — "
                f"priority {sc['investigation_priority']}, confidence {sc['confidence']}, "
                f"novelty {sc['novelty']}"
            )
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_explain_scores",
    annotations={
        "title": "Explain Entity Scores",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_explain_scores(params: ScoreEntityInput) -> str:
    """Explain how an entity's intelligence-quality scores were derived.

    Returns the scores plus a deterministic, human-readable rationale (sources,
    connectivity, type rarity, enriching properties).

    Args:
        params (ScoreEntityInput):
            - entity_id (str): Entity id (e.g. 'n0').

    Returns:
        str: Markdown explanation with the scores and contributing factors.
    """
    try:
        graph = STORE.active_graph()
        data = scoring.explain_scores(graph, params.entity_id)
        sc = data["scores"]
        lines = [
            f"# Scores for {data['value']} ({data['type']}) [`{data['id']}`]",
            "",
            f"- confidence: {sc['confidence']}",
            f"- source_reliability: {sc['source_reliability']}",
            f"- linkage_strength: {sc['linkage_strength']}",
            f"- investigation_priority: {sc['investigation_priority']}",
            f"- novelty: {sc['novelty']}",
            "",
            "## Why",
        ]
        for f in data["factors"]:
            lines.append(f"- {f}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- export tools ------------------------------------------------------------
@mcp.tool(
    name="maltego_export_csv",
    annotations={
        "title": "Export Graph to CSV",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_export_csv(params: ExportPathInput) -> str:
    """Export the active graph's entities to a CSV file (round-trips with import).

    Writes the same ``type,value,notes,link_to`` schema that maltego_import_csv
    reads, so an exported CSV can be re-imported to reconstruct entities and their
    relationships.

    Args:
        params (ExportPathInput):
            - path (str): Destination CSV file path.

    Returns:
        str: Confirmation including the path and entity count.
    """
    try:
        graph = STORE.active_graph()
        csv_import.export_csv_file(graph, params.path)
        return f"Exported {graph.entity_count()} entities from '{graph.name}' to {params.path}."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_export_json",
    annotations={
        "title": "Export Graph to JSON",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_export_json(params: ExportPathInput) -> str:
    """Export the full active graph (entities, links, memory, scores) as JSON.

    A complete, programmatic snapshot — a superset of the `.mtgx` sidecars —
    useful for downstream tooling that doesn't read Maltego files.

    Args:
        params (ExportPathInput):
            - path (str): Destination JSON file path.

    Returns:
        str: Confirmation including the path.
    """
    try:
        graph = STORE.active_graph()
        payload = {
            "name": graph.name,
            "created_at": graph.created_at,
            "entities": [e.to_dict() for e in graph.entities],
            "links": [l.to_dict() for l in graph.links],
            "memory": graph.memory.to_dict(),
        }
        with open(params.path, "w", encoding="utf-8") as fh:
            fh.write(to_json(payload))
        return (
            f"Exported '{graph.name}' ({graph.entity_count()} entities, "
            f"{graph.link_count()} links, {len(graph.memory.steps)} memory steps) "
            f"to {params.path}."
        )
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- link management ---------------------------------------------------------
@mcp.tool(
    name="maltego_list_links",
    annotations={
        "title": "List Links",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_list_links(params: ListLinksInput) -> str:
    """List links (edges) on the active graph, with pagination.

    Args:
        params (ListLinksInput):
            - limit/offset (int): Pagination.
            - response_format (ResponseFormat): 'markdown' (default) or 'json'.

    Returns:
        str: JSON form is {total, count, offset, links:[{id,source,target,label}]};
        markdown shows source → target with labels and entity values.
    """
    try:
        graph = STORE.active_graph()
        links = graph.links
        total = len(links)
        page = links[params.offset : params.offset + params.limit]
        if params.response_format == ResponseFormat.JSON:
            return to_json(
                {
                    "total": total,
                    "count": len(page),
                    "offset": params.offset,
                    "links": [l.to_dict() for l in page],
                }
            )
        if not page:
            return "No links on the active graph."
        lines = [f"# Links ({total})", ""]
        for l in page:
            s = graph.get_entity(l.source_id).value
            t = graph.get_entity(l.target_id).value
            label = f" ('{l.label}')" if l.label else ""
            lines.append(f"- `{l.id}` {s} → {t}{label}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_delete_link",
    annotations={
        "title": "Delete Link",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_delete_link(params: DeleteLinkInput) -> str:
    """Delete a single link (edge) from the active graph by id.

    Args:
        params (DeleteLinkInput):
            - link_id (str): Link id (e.g. 'e0'; see maltego_list_links).

    Returns:
        str: Confirmation, or a note if the link id was not found.
    """
    try:
        graph = STORE.active_graph()
        if graph.delete_link(params.link_id):
            return f"Deleted link {params.link_id}."
        return error(f"No link with id '{params.link_id}'. Use maltego_list_links.")
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- graph management --------------------------------------------------------
@mcp.tool(
    name="maltego_rename_graph",
    annotations={
        "title": "Rename Graph",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def maltego_rename_graph(params: RenameGraphInput) -> str:
    """Rename an open graph (defaults to the active graph).

    Args:
        params (RenameGraphInput):
            - new_name (str): New name.
            - graph_name (Optional[str]): Graph to rename; defaults to active.

    Returns:
        str: Confirmation, or an actionable error (unknown/duplicate name).
    """
    try:
        old = params.graph_name or (STORE.active_name or "")
        if not old:
            return error("No active graph to rename. Create or open one first.")
        graph = STORE.rename_graph(old, params.new_name)
        return f"Renamed graph '{old}' to '{graph.name}'."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_delete_graph",
    annotations={
        "title": "Delete Graph (from server)",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_delete_graph(params: DeleteGraphInput) -> str:
    """Remove an open graph from the server's memory.

    This does NOT delete any saved `.mtgx` file on disk — only the in-memory copy.
    If the active graph is removed, another open graph becomes active (if any).

    Args:
        params (DeleteGraphInput):
            - name (str): Name of the open graph to remove.

    Returns:
        str: Confirmation, or an actionable error if the name is unknown.
    """
    try:
        STORE.remove_graph(params.name)
        active = STORE.active_name or "none"
        return f"Removed graph '{params.name}' from the server. Active graph: {active}."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


# --- outcome-based learning --------------------------------------------------
@mcp.tool(
    name="maltego_learning_stats",
    annotations={
        "title": "Learning Stats",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_learning_stats() -> str:
    """Show cross-investigation learning stats used by the Next Best Action engine.

    Learning is opt-in: enable it by setting the env var MALTEGO_MCP_LEARNING=1
    (or MALTEGO_MCP_LEARNING_PATH=/path/to/file.json). When enabled, the engine
    records per-transform outcomes across investigations (runs, successes, average
    yield) and lets that history nudge recommendations.

    Returns:
        str: JSON of per-transform stats, or a note if learning is disabled.
    """
    try:
        if not learning.is_enabled():
            return (
                "Learning is disabled. Enable it with MALTEGO_MCP_LEARNING=1 "
                "(or set MALTEGO_MCP_LEARNING_PATH) and restart the server."
            )
        stats = learning.store.all_stats()
        if not stats:
            return "Learning is enabled but no outcomes recorded yet."
        return to_json({"store": learning.store_path(), "transforms": stats})
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


@mcp.tool(
    name="maltego_reset_learning",
    annotations={
        "title": "Reset Learning",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def maltego_reset_learning() -> str:
    """Clear the cross-investigation learning store (in-memory and on disk).

    No-op when learning is disabled.

    Returns:
        str: Confirmation.
    """
    try:
        if not learning.is_enabled():
            return "Learning is disabled; nothing to reset."
        learning.store.reset()
        return "Cleared the learning store."
    except Exception as exc:  # noqa: BLE001
        return handle_exception(exc)


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
