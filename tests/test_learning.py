"""Tests for the opt-in outcome-based learning store and its NBA integration."""

import asyncio
import os
import tempfile

import pytest

from maltego_mcp import learning, recommendation
from maltego_mcp.graph.graph_store import GraphStore
from maltego_mcp.machines import run_machine


@pytest.fixture
def temp_learning(monkeypatch):
    """Enable learning against a throwaway store for the duration of a test."""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "learning.json")
    monkeypatch.setenv("MALTEGO_MCP_LEARNING_PATH", path)
    learning.store.reload()
    learning.store.reset()
    yield path
    learning.store.reset()
    learning.store.reload()


def test_learning_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MALTEGO_MCP_LEARNING", raising=False)
    monkeypatch.delenv("MALTEGO_MCP_LEARNING_PATH", raising=False)
    assert learning.is_enabled() is False
    # record + prior are no-ops/zero when disabled
    learning.store.record("vt.domain_to_ip", "success", 5)
    assert learning.store.prior("vt.domain_to_ip") == 0.0
    assert learning.store.stats("vt.domain_to_ip")["runs"] == 0.0


def test_learning_records_and_prior(temp_learning):
    assert learning.is_enabled() is True
    learning.store.record("vt.domain_to_ip", "success", 4)
    learning.store.record("vt.domain_to_ip", "success", 6)
    learning.store.record("vt.domain_to_ip", "empty", 0)
    s = learning.store.stats("vt.domain_to_ip")
    assert s["runs"] == 3 and s["successes"] == 2
    assert s["avg_yield"] == 5.0  # (4+6)/2
    assert round(s["success_rate"], 3) == round(2 / 3, 3)
    assert 0.0 < learning.store.prior("vt.domain_to_ip") <= 1.0


def test_learning_persists_to_disk(temp_learning):
    learning.store.record("dns.domain_to_ip", "success", 2)
    learning.store.flush()
    assert os.path.isfile(temp_learning)
    # reload from disk
    learning.store.reload()
    assert learning.store.stats("dns.domain_to_ip")["runs"] == 1


def test_run_and_record_feeds_learning(temp_learning):
    g = GraphStore().create_graph("l")
    asyncio.run(run_machine(g, "email_investigation", "bob@example.com", allow_network=False))
    # the offline parse transforms ran and were recorded
    s = learning.store.stats("parse.email_to_domain")
    assert s["runs"] >= 1 and s["successes"] >= 1


def test_learning_nudges_expected_gain(temp_learning):
    g = GraphStore().create_graph("g")
    d = g.add_entity("maltego.Domain", "example.com")
    from maltego_mcp.transforms import registry
    t = registry.get("dns.domain_to_ip")
    base = recommendation._expected_gain(g, t)
    # record strong historical success for this transform
    for _ in range(5):
        learning.store.record("dns.domain_to_ip", "success", 5)
    boosted = recommendation._expected_gain(g, t)
    assert boosted >= base  # history should not lower the estimate
    assert boosted > base   # and here it should raise it


def test_disabled_reproduces_baseline(monkeypatch):
    monkeypatch.delenv("MALTEGO_MCP_LEARNING", raising=False)
    monkeypatch.delenv("MALTEGO_MCP_LEARNING_PATH", raising=False)
    learning.store.reload()
    g = GraphStore().create_graph("g")
    g.add_entity("maltego.Domain", "example.com")
    from maltego_mcp.transforms import registry
    t = registry.get("dns.domain_to_ip")
    # With learning disabled, expected gain ignores any history entirely.
    gain1 = recommendation._expected_gain(g, t)
    gain2 = recommendation._expected_gain(g, t)
    assert gain1 == gain2
