"""Tests for v0.4.0: keyless transforms, deep mode, expand_entity, find_path."""

import asyncio

import pytest

from maltego_mcp.transforms import registry, providers
from maltego_mcp.transforms.osint import keyless
from maltego_mcp.server import mcp, STORE


def call(name, args=None):
    async def _run():
        res = await mcp.call_tool(name, args or {})
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text if isinstance(content, list) else str(content)

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _fresh_store():
    STORE._graphs.clear()
    STORE._active = None
    yield
    STORE._graphs.clear()
    STORE._active = None


# --- keyless transforms ------------------------------------------------------
def test_keyless_transforms_registered_and_available():
    for name in ("crtsh.domain_to_subdomains", "rdap.domain_info", "rdap.ip_info"):
        t = registry.get(name)
        assert t is not None, name
        assert t.is_available() is True  # no key required
    pnames = {p.name for p in providers.all()}
    assert {"crtsh", "rdap"} <= pnames


def test_crtsh_parser_dedupes_and_strips_wildcards():
    ents = keyless.parse_crtsh(
        [
            {"name_value": "a.x.com\n*.x.com", "common_name": "b.x.com"},
            {"name_value": "a.x.com"},
            {"name_value": "user@x.com"},  # email -> skipped
        ]
    )
    vals = sorted(e.value for e in ents)
    assert vals == ["a.x.com", "b.x.com", "x.com"]
    assert all(e.type_id == "maltego.DNSName" for e in ents)


def test_rdap_domain_parser():
    ents, _ = keyless.parse_rdap_domain(
        {
            "nameservers": [{"ldhName": "NS1.X.COM"}],
            "events": [{"eventAction": "registration", "eventDate": "2001-01-01"}],
            "entities": [
                {
                    "roles": ["registrar"],
                    "vcardArray": ["vcard", [["fn", {}, "text", "Acme"], ["email", {}, "text", "ab@acme.com"]]],
                }
            ],
        }
    )
    kinds = {(e.type_id, e.value) for e in ents}
    assert ("maltego.DNSName", "ns1.x.com") in kinds
    assert ("maltego.EmailAddress", "ab@acme.com") in kinds


def test_rdap_ip_parser():
    ents = keyless.parse_rdap_ip(
        {"handle": "8.8.8.0/24", "name": "GOGL", "startAddress": "8.8.8.0", "endAddress": "8.8.8.255"}
    )
    kinds = {(e.type_id, e.value) for e in ents}
    assert ("maltego.Netblock", "8.8.8.0 - 8.8.8.255") in kinds
    assert ("maltego.Organization", "GOGL") in kinds


def test_keyless_transforms_input_types():
    # Offline structural checks (no network): correct input/output wiring.
    assert registry.get("crtsh.domain_to_subdomains").accepts("maltego.Domain")
    assert registry.get("rdap.ip_info").accepts("maltego.IPv4Address")
    assert "maltego.DNSName" in registry.get("rdap.domain_info").output_types


# --- deep mode ---------------------------------------------------------------
def test_investigate_depth_modes_offline():
    out_q = call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False, "depth": "quick", "include_report": False}})
    assert "depth: quick" in out_q
    STORE._graphs.clear(); STORE._active = None
    out_d = call("maltego_investigate", {"params": {"query": "bob@example.com", "allow_network": False, "depth": "deep", "include_report": False}})
    assert "deep (all applicable transforms)" in out_d


def test_investigate_rejects_bad_depth():
    # Invalid input is rejected by schema validation (raises), not a text error.
    with pytest.raises(Exception):
        call("maltego_investigate", {"params": {"query": "example.com", "depth": "ludicrous"}})


# --- expand_entity + find_path ----------------------------------------------
def test_expand_entity_offline():
    call("maltego_create_graph", {"params": {"name": "e"}})
    call("maltego_add_entity", {"params": {"type": "maltego.EmailAddress", "value": "a@x.com"}})
    out = call("maltego_expand_entity", {"params": {"entity_id": "n0", "allow_network": False, "max_rounds": 2}})
    assert "Expanded n0" in out
    # email -> domain was discovered offline
    entities = call("maltego_list_entities", {"params": {"response_format": "json"}})
    assert "x.com" in entities


def test_find_path():
    call("maltego_create_graph", {"params": {"name": "p"}})
    call("maltego_add_entity", {"params": {"type": "maltego.EmailAddress", "value": "a@x.com"}})  # n0
    call("maltego_expand_entity", {"params": {"entity_id": "n0", "allow_network": False}})  # -> x.com (n1)
    out = call("maltego_find_path", {"params": {"source_id": "n0", "target_id": "n1"}})
    assert "Path:" in out and "x.com" in out


def test_find_path_none():
    call("maltego_create_graph", {"params": {"name": "p2"}})
    call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "a.com"}})  # n0
    call("maltego_add_entity", {"params": {"type": "maltego.Domain", "value": "b.com"}})  # n1 (unlinked)
    out = call("maltego_find_path", {"params": {"source_id": "n0", "target_id": "n1"}})
    assert "No path" in out
