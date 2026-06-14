"""Tests for the investigation-intelligence subsystems.

Covers: Investigation Memory (recording, queries, persistence, merge), the
scoring engine, the Next Best Action engine, the query detector, the event bus,
and the unified investigate flow. All deterministic and offline.
"""

import asyncio
import os
import tempfile

from maltego_mcp.graph.graph_store import GraphStore
from maltego_mcp.graph.mtgx_writer import write_mtgx, MEMORY_MEMBER
from maltego_mcp.graph.mtgx_reader import read_mtgx
from maltego_mcp import scoring, recommendation, machines
from maltego_mcp.detect import detect
from maltego_mcp.events import EventBus, bus, TRANSFORM_COMPLETED, ENTITY_DISCOVERED
from maltego_mcp.memory import InvestigationMemory, STATUS_SUCCESS, STATUS_ERROR
from maltego_mcp.orchestration import expand, run_and_record
from maltego_mcp.transforms import registry


def _email_investigation():
    """Build a small graph offline and return (graph, report)."""
    g = GraphStore().create_graph("intel")
    seed = g.add_entity("maltego.EmailAddress", "bob@example.com")
    report = asyncio.run(expand(g, seed.id, allow_network=False, max_rounds=2))
    return g, report


# --- Investigation Memory ----------------------------------------------------
def test_memory_records_steps_during_expansion():
    g, _ = _email_investigation()
    steps = g.memory.timeline()
    assert len(steps) == 2  # email->domain, domain->website
    assert steps[0].transform == "parse.email_to_domain"
    assert steps[0].status == STATUS_SUCCESS
    assert steps[0].new_entities == 1
    # execution ids are deterministic sequential
    assert [s.execution_id for s in steps] == ["x0", "x1"]
    # reasons capture intent
    assert "applicable to" in steps[0].reason


def test_memory_queries():
    g, _ = _email_investigation()
    domain = [e for e in g.entities if e.type_id == "maltego.Domain"][0]
    email = [e for e in g.entities if e.type_id == "maltego.EmailAddress"][0]
    # the domain was discovered by the email->domain transform
    disc = g.memory.discovering_steps(domain.id)
    assert disc and disc[0].transform == "parse.email_to_domain"
    # the email triggered a transform
    assert "parse.email_to_domain" in g.memory.transforms_run_on(email.id)
    # provider source attribution
    assert g.memory.sources_for_entity(domain.id) == ["local"]


def test_memory_records_error_step():
    g = GraphStore().create_graph("err")
    e = g.add_entity("maltego.Domain", "x.com")

    class _Boom:
        name = "boom.transform"
        provider = "local"
        output_types = ["maltego.IPv4Address"]

        async def run(self, value, props):
            raise RuntimeError("kaboom")

    added, step = asyncio.run(run_and_record(g, _Boom(), e, "test", emit_events=False))
    assert added == []
    assert step.status == STATUS_ERROR
    assert step.reconsider is True
    assert "kaboom" in step.message


def test_memory_survives_mtgx_roundtrip():
    g, _ = _email_investigation()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.mtgx")
        write_mtgx(g, path)
        import zipfile
        assert MEMORY_MEMBER in zipfile.ZipFile(path).namelist()
        g2 = read_mtgx(path)
        assert len(g2.memory.steps) == 2
        assert g2.memory.steps[0].transform == "parse.email_to_domain"


def test_memory_not_written_when_empty():
    g = GraphStore().create_graph("plain")
    g.add_entity("maltego.Domain", "x.com")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "p.mtgx")
        write_mtgx(g, path)
        import zipfile
        # No memory sidecar for a graph with no recorded steps (CE-clean).
        assert MEMORY_MEMBER not in zipfile.ZipFile(path).namelist()


def test_memory_merges_with_graph():
    store = GraphStore()
    a, _ = _email_investigation()
    b, _ = _email_investigation()
    before = len(a.memory.steps)
    stats = a.merge_from(b, dedupe=True)
    assert stats["memory_steps_merged"] == 2
    assert len(a.memory.steps) == before + 2


# --- scoring -----------------------------------------------------------------
def test_scoring_is_deterministic_and_bounded():
    g1, _ = _email_investigation()
    g2, _ = _email_investigation()
    d1 = [e for e in g1.entities if e.type_id == "maltego.Domain"][0]
    d2 = [e for e in g2.entities if e.type_id == "maltego.Domain"][0]
    s1 = scoring.score_entity(g1, d1.id)
    s2 = scoring.score_entity(g2, d2.id)
    assert s1 == s2  # deterministic
    for v in s1.values():
        assert 0.0 <= v <= 1.0


def test_scoring_keys_present_and_cached():
    g, _ = _email_investigation()
    e = g.entities[0]
    s = scoring.score_entity(g, e.id)
    assert set(s) == {
        "confidence",
        "source_reliability",
        "linkage_strength",
        "investigation_priority",
        "novelty",
    }
    assert e.scores == s  # cached onto the entity


def test_seed_entity_has_user_reliability():
    g = GraphStore().create_graph("s")
    e = g.add_entity("maltego.Domain", "seed.com")
    s = scoring.score_entity(g, e.id)
    # No discovering step -> sourced from the analyst -> full reliability.
    assert s["source_reliability"] == 1.0


def test_rank_entities_orders_by_priority():
    g, _ = _email_investigation()
    ranked = scoring.rank_entities(g)
    priorities = [r["scores"]["investigation_priority"] for r in ranked]
    assert priorities == sorted(priorities, reverse=True)
    # the connected domain should outrank a leaf
    assert ranked[0]["type"] == "maltego.Domain"


def test_explain_scores_has_factors():
    g, _ = _email_investigation()
    d = [e for e in g.entities if e.type_id == "maltego.Domain"][0]
    data = scoring.explain_scores(g, d.id)
    assert data["factors"]
    assert "scores" in data and data["sources"] == ["local"]


# --- Next Best Action engine -------------------------------------------------
def test_nba_excludes_attempted_pairs():
    g, _ = _email_investigation()
    recs = recommendation.next_best_actions(g, limit=20)
    # parse.email_to_domain already ran on the email -> must not be recommended on it
    email = [e for e in g.entities if e.type_id == "maltego.EmailAddress"][0]
    assert not any(
        r["transform"] == "parse.email_to_domain" and r["entity_id"] == email.id
        for r in recs
    )


def test_nba_is_ranked_and_explainable():
    g, _ = _email_investigation()
    recs = recommendation.next_best_actions(g, limit=10)
    assert recs
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)
    assert all(r["reason"] for r in recs)
    assert all(0.0 <= r["score"] <= 1.0 for r in recs)


def test_nba_deterministic():
    g1, _ = _email_investigation()
    g2, _ = _email_investigation()
    r1 = recommendation.next_best_actions(g1, limit=5)
    r2 = recommendation.next_best_actions(g2, limit=5)
    assert [(r["transform"], r["entity_id"], r["score"]) for r in r1] == [
        (r["transform"], r["entity_id"], r["score"]) for r in r2
    ]


# --- query detector ----------------------------------------------------------
def test_detect_types():
    assert detect("example.com").type_id == "maltego.Domain"
    assert detect("bob@example.com").type_id == "maltego.EmailAddress"
    assert detect("1.2.3.4").type_id == "maltego.IPv4Address"
    assert detect("2001:db8::1").type_id == "maltego.IPv6Address"
    d = detect("https://sub.example.com/path?x=1")
    assert d.type_id == "maltego.Domain" and d.value == "sub.example.com"
    assert detect("just some text").type_id == "maltego.Phrase"


def test_detect_picks_machine():
    assert detect("example.com").machine == "passive_domain"
    assert detect("a@b.com").machine == "email_investigation"
    assert detect("8.8.8.8").machine == "infrastructure_mapping"


# --- event bus ---------------------------------------------------------------
def test_event_bus_records_and_filters():
    eb = EventBus(maxlen=10)
    eb.subscribe()
    eb.emit("a", {"x": 1})
    eb.emit("b", {"y": 2})
    recent = eb.recent()
    assert [e.type for e in recent] == ["a", "b"]
    assert [e.seq for e in recent] == [0, 1]
    # since_seq filtering
    after = eb.recent(since_seq=0)
    assert [e.type for e in after] == ["b"]


def test_expansion_emits_events():
    bus.clear()
    bus.subscribe()
    g = GraphStore().create_graph("ev")
    seed = g.add_entity("maltego.EmailAddress", "bob@example.com")
    asyncio.run(expand(g, seed.id, allow_network=False, max_rounds=2))
    types = {e.type for e in bus.recent(50)}
    assert TRANSFORM_COMPLETED in types
    assert ENTITY_DISCOVERED in types


# --- unified investigate (via machine) ---------------------------------------
def test_unified_investigate_offline():
    g = GraphStore().create_graph("u")
    det = detect("bob@example.com")
    report = asyncio.run(
        machines.run_machine(g, det.machine, det.value, allow_network=False)
    )
    assert report.entities_added >= 1
    assert g.memory.timeline()  # memory recorded
    # scoring + NBA work on the result
    assert scoring.rank_entities(g)
    assert recommendation.next_best_actions(g)


if __name__ == "__main__":
    import sys

    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
