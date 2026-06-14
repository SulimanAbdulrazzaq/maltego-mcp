"""End-to-end smoke tests for the core graph + .mtgx round-trip + transforms."""

import asyncio
import os
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from maltego_mcp.graph.graph_store import GraphStore, EntityNotFoundError
from maltego_mcp.graph.mtgx_writer import write_mtgx, build_graphml, GRAPH_MEMBER
from maltego_mcp.graph.mtgx_reader import read_mtgx, parse_graphml
from maltego_mcp.transforms import registry


def _build_sample():
    store = GraphStore()
    g = store.create_graph("case1")
    dom = g.add_entity("maltego.Domain", "example.com", properties={"source": "test"}, notes="root")
    ip = g.add_entity("maltego.IPv4Address", "93.184.216.34")
    person = g.add_entity("maltego.Person", "Jane Doe")
    g.add_link(dom.id, ip.id, "resolves to")
    g.add_link(person.id, dom.id, "owns")
    return store, g, dom, ip, person


def test_dedupe():
    store, g, dom, *_ = _build_sample()
    again = g.add_entity("maltego.Domain", "example.com")
    assert again.id == dom.id, "dedupe should reuse the same entity id"
    assert g.entity_count() == 3


def test_graphml_is_valid_xml_with_expected_shape():
    _store, g, *_ = _build_sample()
    xml = build_graphml(g)
    root = ET.fromstring(xml)  # raises if malformed
    # local-name checks (namespace-agnostic)
    assert root.tag.endswith("graphml")
    nodes = [e for e in root.iter() if e.tag.endswith("MaltegoEntity")]
    links = [e for e in root.iter() if e.tag.endswith("MaltegoLink")]
    assert len(nodes) == 3
    assert len(links) == 2
    types = {n.get("type") for n in nodes}
    assert "maltego.Domain" in types and "maltego.IPv4Address" in types


def test_mtgx_archive_and_roundtrip():
    _store, g, dom, ip, person = _build_sample()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case1.mtgx")
        write_mtgx(g, path)

        # archive contains the expected member
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            assert GRAPH_MEMBER in names
            assert "version.properties" in names

        # read back
        g2 = read_mtgx(path)
        assert g2.entity_count() == 3
        assert g2.link_count() == 2
        domains = [e for e in g2.entities if e.type_id == "maltego.Domain"]
        assert len(domains) == 1
        assert domains[0].value == "example.com"
        assert domains[0].properties.get("source") == "test"
        assert domains[0].notes == "root"
        # links preserved with labels
        labels = sorted(l.label for l in g2.links)
        assert labels == ["owns", "resolves to"]
        assert g2.source_path and g2.source_path.endswith("case1.mtgx")


def test_delete_entity_removes_links():
    _store, g, dom, ip, person = _build_sample()
    removed = g.delete_entity(dom.id)
    assert len(removed) == 2  # both links touched the domain
    assert g.entity_count() == 2
    assert g.link_count() == 0
    try:
        g.get_entity(dom.id)
        assert False, "should have raised"
    except EntityNotFoundError:
        pass


def test_offline_transform_email_to_domain():
    t = registry.get("parse.email_to_domain")
    assert t is not None
    result = asyncio.run(t.run("alice@Example.COM", {}))
    assert len(result.entities) == 1
    assert result.entities[0].value == "example.com"
    assert result.entities[0].type_id == "maltego.Domain"


def test_transform_registry_filters_by_type():
    domain_transforms = {t.name for t in registry.for_type("maltego.Domain")}
    assert "dns.domain_to_ip" in domain_transforms
    assert "parse.domain_to_website" in domain_transforms
    assert "dns.ip_to_host" not in domain_transforms


if __name__ == "__main__":
    # Allow running without pytest.
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
