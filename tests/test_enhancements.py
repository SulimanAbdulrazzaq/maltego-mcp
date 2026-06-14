"""Tests for the AI-assisted investigation-platform enhancements.

These cover orchestration, machines, analysis, layout, CSV import, OSINT provider
registration/parsing, reporting, graph merge, and mtgx position round-trip.
All tests are deterministic and run offline (no real network / API keys).
"""

import asyncio
import os
import tempfile

from maltego_mcp.graph.graph_store import GraphStore
from maltego_mcp.graph.mtgx_writer import write_mtgx
from maltego_mcp.graph.mtgx_reader import read_mtgx
from maltego_mcp import analysis, csv_import, layout, machines, reporting
from maltego_mcp.orchestration import expand
from maltego_mcp.transforms import registry, providers


# --- orchestration -----------------------------------------------------------
def test_expand_offline_dedupes_and_links():
    g = GraphStore().create_graph("inv")
    seed = g.add_entity("maltego.EmailAddress", "bob@example.com")
    report = asyncio.run(
        expand(g, seed.id, allow_network=False, available_only=True, max_rounds=2)
    )
    # email -> domain (round 1); domain -> website (round 2)
    values = {e.value for e in g.entities}
    assert "example.com" in values  # domain derived
    assert any(e.type_id == "maltego.Website" for e in g.entities)
    assert report.entities_added >= 2
    assert report.links_added >= 2
    # No duplicate domain entity.
    domains = [e for e in g.entities if e.type_id == "maltego.Domain"]
    assert len(domains) == 1


def test_expand_skips_network_when_disabled():
    g = GraphStore().create_graph("inv")
    seed = g.add_entity("maltego.Domain", "example.com")
    report = asyncio.run(expand(g, seed.id, allow_network=False, max_rounds=1))
    # dns.domain_to_ip is network -> must not have run; only offline website added.
    assert all(e.type_id != "maltego.IPv4Address" for e in g.entities)
    assert any(e.type_id == "maltego.Website" for e in g.entities)
    del report


# --- machines ----------------------------------------------------------------
def test_machine_registry_has_builtins():
    names = {m.name for m in machines.registry.all()}
    assert {"passive_domain", "email_investigation", "infrastructure_mapping"} <= names


def test_run_email_machine_offline():
    g = GraphStore().create_graph("m")
    report = asyncio.run(
        machines.run_machine(g, "email_investigation", "alice@example.com", allow_network=False)
    )
    assert report.entities_added >= 1
    assert any(e.value == "example.com" for e in g.entities)


def test_run_unknown_machine_raises():
    g = GraphStore().create_graph("m")
    try:
        asyncio.run(machines.run_machine(g, "nope", "x"))
        assert False
    except KeyError:
        pass


# --- analysis ----------------------------------------------------------------
def _diamond_graph():
    g = GraphStore().create_graph("a")
    d = g.add_entity("maltego.Domain", "example.com")
    ip = g.add_entity("maltego.IPv4Address", "1.2.3.4")
    w = g.add_entity("maltego.Website", "example.com")
    e = g.add_entity("maltego.EmailAddress", "x@example.com")
    g.add_link(d.id, ip.id, "resolves")
    g.add_link(d.id, w.id, "website")
    g.add_link(e.id, d.id, "email domain")
    return g, d


def test_summarize_graph():
    g, _ = _diamond_graph()
    s = analysis.summarize_graph(g)
    assert s["entity_count"] == 4
    assert s["link_count"] == 3
    assert s["type_breakdown"]["maltego.Domain"] == 1
    # domain is the most connected (degree 3)
    assert s["most_connected"][0]["type"] == "maltego.Domain"


def test_identify_pivots_ranks_domain_first():
    g, d = _diamond_graph()
    pivots = analysis.identify_interesting_pivots(g)
    assert pivots and pivots[0]["id"] == d.id
    assert pivots[0]["degree"] == 3


def test_suggest_next_steps_nonempty():
    g, _ = _diamond_graph()
    steps = analysis.suggest_next_steps(g)
    assert steps
    assert all("transform" in s and "on_entity" in s for s in steps)


def test_explain_entity():
    g, d = _diamond_graph()
    data = analysis.explain_entity(g, d.id)
    assert data["degree"] == 3
    assert len(data["outgoing"]) == 2
    assert len(data["incoming"]) == 1


# --- layout ------------------------------------------------------------------
def test_layouts_are_deterministic_and_assign_positions():
    for algo in layout.LAYOUTS:
        g, _ = _diamond_graph()
        n1 = layout.apply_layout(g, algo)
        pos1 = {e.id: e.position for e in g.entities}
        # recompute on a fresh identical graph -> identical positions
        g2, _ = _diamond_graph()
        layout.apply_layout(g2, algo)
        pos2 = {e.id: e.position for e in g2.entities}
        assert n1 == 4
        assert pos1 == pos2, f"{algo} not deterministic"
        assert all(p is not None for p in pos1.values())


def test_unknown_layout_raises():
    g, _ = _diamond_graph()
    try:
        layout.apply_layout(g, "spiral")
        assert False
    except ValueError:
        pass


# --- layout round-trips through mtgx -----------------------------------------
def test_position_roundtrip_in_mtgx():
    g, _ = _diamond_graph()
    layout.apply_layout(g, "hierarchical")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "p.mtgx")
        write_mtgx(g, path)
        g2 = read_mtgx(path)
        # every entity should have recovered a position
        assert all(e.position is not None for e in g2.entities)


# --- CSV import --------------------------------------------------------------
def test_csv_import_basic_and_skips_unknown():
    g = GraphStore().create_graph("c")
    text = "type,value\nDomain,example.com\nEmail,a@example.com\nIPv4Address,1.2.3.4\nBogus,zzz\n"
    rep = csv_import.import_csv_text(g, text)
    assert rep["entities_added"] == 3
    assert rep["rows_skipped"] == 1
    assert g.entity_count() == 3


def test_csv_import_links_and_dedupe():
    g = GraphStore().create_graph("c")
    text = "type,value,link_to\nDomain,example.com,\nIPv4Address,1.2.3.4,example.com\nDomain,example.com,\n"
    rep = csv_import.import_csv_text(g, text)
    assert rep["links_added"] == 1
    # third row dedupes against the first domain
    assert rep["entities_reused"] == 1
    assert len([e for e in g.entities if e.type_id == "maltego.Domain"]) == 1


def test_csv_resolve_type_aliases():
    assert csv_import.resolve_type("Email") == "maltego.EmailAddress"
    assert csv_import.resolve_type("maltego.Domain") == "maltego.Domain"
    assert csv_import.resolve_type("nonsense") is None


# --- OSINT providers ---------------------------------------------------------
def test_providers_registered():
    names = {p.name for p in providers.all()}
    assert {"virustotal", "shodan", "securitytrails", "censys", "hunterio", "hibp"} <= names


def test_provider_transforms_unavailable_without_keys(monkeypatch):
    # Ensure keys are absent.
    for var in ["VIRUSTOTAL_API_KEY", "SHODAN_API_KEY", "HIBP_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    assert registry.get("vt.domain_to_ip").is_available() is False
    assert registry.get("shodan.ip_to_info").is_available() is False


def test_provider_transform_available_with_key(monkeypatch):
    monkeypatch.setenv("VIRUSTOTAL_API_KEY", "dummy")
    assert registry.get("vt.domain_to_ip").is_available() is True


def test_provider_run_graceful_without_key(monkeypatch):
    monkeypatch.delenv("HIBP_API_KEY", raising=False)
    t = registry.get("hibp.email_to_breaches")
    result = asyncio.run(t.run("a@b.com", {}))
    assert result.entities == []
    assert "Missing API credential" in result.message


def test_provider_pure_parsers():
    from maltego_mcp.transforms.osint import virustotal, shodan, hunterio, hibp

    vt = virustotal.parse_resolutions_to_ips(
        {"data": [{"attributes": {"ip_address": "9.9.9.9"}}]}
    )
    assert vt[0].value == "9.9.9.9" and vt[0].type_id == "maltego.IPv4Address"

    sh = shodan.parse_host({"ports": [80, 443], "hostnames": ["a.example.com"]})
    assert {e.value for e in sh} == {"80", "443", "a.example.com"}

    hu = hunterio.parse_domain_search(
        {"data": {"emails": [{"value": "j@x.com", "first_name": "J", "last_name": "D"}]}}
    )
    assert hu[0].value == "j@x.com" and hu[0].properties["person.name"] == "J D"

    br = hibp.parse_breaches([{"Name": "Adobe"}, {"Name": "LinkedIn"}])
    assert {e.value for e in br} == {"Adobe", "LinkedIn"}


# --- merge / continuation ----------------------------------------------------
def test_merge_from_dedupes_and_remaps():
    store = GraphStore()
    a = store.create_graph("a")
    a.add_entity("maltego.Domain", "example.com")
    b = store.create_graph("b", make_active=False)
    d = b.add_entity("maltego.Domain", "example.com")  # duplicate by value
    ip = b.add_entity("maltego.IPv4Address", "1.2.3.4")  # new
    b.add_link(d.id, ip.id, "resolves")
    stats = a.merge_from(b, dedupe=True)
    assert stats["entities_added"] == 1   # only the IP is new
    assert stats["entities_reused"] == 1  # the domain matched
    assert stats["links_added"] == 1
    assert a.entity_count() == 2


# --- reporting ---------------------------------------------------------------
def test_reports_render_and_are_reproducible():
    g, _ = _diamond_graph()
    md1 = reporting.build_markdown_report(g)
    g2, _ = _diamond_graph()
    md2 = reporting.build_markdown_report(g2)
    assert md1 == md2  # deterministic
    assert "# Investigation Report" in md1
    assert "Executive Summary" in md1
    html = reporting.build_html_report(g)
    assert html.startswith("<!DOCTYPE html>")
    assert "Suggested Next Steps" in html


if __name__ == "__main__":
    import sys

    class _MP:
        def setenv(self, k, v):
            os.environ[k] = v

        def delenv(self, k, raising=True):
            os.environ.pop(k, None)

    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            fn(_MP()) if fn.__code__.co_argcount else fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
