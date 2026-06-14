"""MCP tool-layer tests: drive tools through mcp.call_tool (the real client path).

These complement the module-level tests by exercising Pydantic input binding,
the tool wrappers, and error paths exactly as an MCP client would.
"""

import asyncio
import os
import tempfile

import pytest

from maltego_mcp.server import mcp, STORE


def call(name, args=None):
    """Invoke a tool by name and return its text output."""

    async def _run():
        res = await mcp.call_tool(name, args or {})
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text if isinstance(content, list) else str(content)

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _fresh_store():
    """Reset the global store between tests for isolation."""
    STORE._graphs.clear()
    STORE._active = None
    yield
    STORE._graphs.clear()
    STORE._active = None


def test_create_add_and_list_flow():
    assert "Created graph" in call("maltego_create_graph", {"params": {"name": "t"}})
    out = call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "example.com"}})
    assert "Added entity n0" in out
    listed = call("maltego_list_entities", {"params": {"response_format": "json"}})
    assert '"total": 1' in listed


def test_error_no_active_graph():
    out = call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "x.com"}})
    assert out.startswith("Error:") and "No active graph" in out


def test_error_unknown_transform():
    call("maltego_create_graph", {"params": {"name": "t"}})
    call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "x.com"}})
    out = call("maltego_run_transform", {"params": {"transform_name": "does.not.exist", "entity_id": "n0"}})
    assert out.startswith("Error:") and "Unknown transform" in out


def test_investigate_records_memory_and_explain_why():
    call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False}})
    steps = call("maltego_list_investigation_steps", {"params": {"response_format": "json"}})
    assert '"transform": "parse.email_to_domain"' in steps
    why = call("maltego_explain_why", {"params": {"entity_id": "n1"}})
    assert "parse.email_to_domain" in why


def test_next_best_actions_and_scoring_tools():
    call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False}})
    nba = call("maltego_next_best_actions", {"params": {"response_format": "json"}})
    assert '"recommendations"' in nba
    score = call("maltego_score_entity", {"params": {"entity_id": "n1"}})
    assert '"confidence"' in score and '"investigation_priority"' in score


def test_csv_import_export_roundtrip_tools():
    call("maltego_create_graph", {"params": {"name": "c"}})
    call("maltego_import_csv", {"params": {"content": "type,value,link_to\nDomain,a.com,\nIPv4Address,1.2.3.4,a.com\n"}})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.csv")
        out = call("maltego_export_csv", {"params": {"path": path}})
        assert "Exported 2 entities" in out
        assert os.path.isfile(path)
        # re-import into a fresh graph reconstructs entities + the link
        call("maltego_create_graph", {"params": {"name": "c2"}})
        rep = call("maltego_import_csv", {"params": {"path": path}})
        assert "entities added: 2" in rep
        links = call("maltego_list_links", {"params": {"response_format": "json"}})
        assert '"total": 1' in links


def test_link_and_graph_management_tools():
    call("maltego_create_graph", {"params": {"name": "g"}})
    call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "a.com"}})
    call("maltego_add_entity", {"params": {"type": "maltego.IPv4Address", "value": "1.1.1.1"}})
    call("maltego_add_link", {"params": {"source_id": "n0", "target_id": "n1", "label": "resolves"}})
    assert "Deleted link e0" in call("maltego_delete_link", {"params": {"link_id": "e0"}})
    assert "No link" in call("maltego_delete_link", {"params": {"link_id": "e9"}})
    # rename + delete graph
    assert "Renamed graph" in call("maltego_rename_graph", {"params": {"new_name": "renamed"}})
    assert "Removed graph 'renamed'" in call("maltego_delete_graph", {"params": {"name": "renamed"}})


def test_export_json_tool():
    call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False}})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "g.json")
        out = call("maltego_export_json", {"params": {"path": path}})
        assert "Exported" in out and os.path.isfile(path)
        import json
        data = json.load(open(path, encoding="utf-8"))
        assert "entities" in data and "memory" in data and data["memory"]["steps"]


def test_events_tools():
    call("maltego_subscribe_events", {})
    call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False}})
    events = call("maltego_get_recent_events", {"params": {"response_format": "json", "limit": 50}})
    assert "transform_completed" in events
