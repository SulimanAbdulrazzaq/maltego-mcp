"""Gated live OSINT provider tests.

These make REAL network calls and are skipped unless the relevant API key(s) are
present in the environment, so a normal `pytest` run stays green and offline.

Run a specific provider's live test by setting its key, e.g.::

    VIRUSTOTAL_API_KEY=... pytest tests/test_providers_live.py -k virustotal
"""

import asyncio
import os

import pytest

from maltego_mcp.transforms import registry


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.skipif(not os.environ.get("VIRUSTOTAL_API_KEY"), reason="VIRUSTOTAL_API_KEY not set")
def test_virustotal_domain_to_ip_live():
    t = registry.get("vt.domain_to_ip")
    assert t.is_available()
    result = _run(t.run("google.com", {}))
    # Either we got entities or a clear message; must not raise.
    assert result.message or result.entities


@pytest.mark.skipif(not os.environ.get("SHODAN_API_KEY"), reason="SHODAN_API_KEY not set")
def test_shodan_ip_to_info_live():
    t = registry.get("shodan.ip_to_info")
    assert t.is_available()
    result = _run(t.run("8.8.8.8", {}))
    assert result.message or result.entities


@pytest.mark.skipif(not os.environ.get("SECURITYTRAILS_API_KEY"), reason="SECURITYTRAILS_API_KEY not set")
def test_securitytrails_subdomains_live():
    t = registry.get("securitytrails.domain_to_subdomains")
    assert t.is_available()
    result = _run(t.run("example.com", {}))
    assert result.message or result.entities


@pytest.mark.skipif(
    not (os.environ.get("CENSYS_API_ID") and os.environ.get("CENSYS_API_SECRET")),
    reason="CENSYS_API_ID/CENSYS_API_SECRET not set",
)
def test_censys_ip_to_services_live():
    t = registry.get("censys.ip_to_services")
    assert t.is_available()  # now requires BOTH id and secret
    result = _run(t.run("8.8.8.8", {}))
    assert result.message or result.entities


@pytest.mark.skipif(not os.environ.get("HUNTER_API_KEY"), reason="HUNTER_API_KEY not set")
def test_hunter_domain_to_emails_live():
    t = registry.get("hunter.domain_to_emails")
    assert t.is_available()
    result = _run(t.run("example.com", {}))
    assert result.message or result.entities


@pytest.mark.skipif(not os.environ.get("HIBP_API_KEY"), reason="HIBP_API_KEY not set")
def test_hibp_email_to_breaches_live():
    t = registry.get("hibp.email_to_breaches")
    assert t.is_available()
    result = _run(t.run("test@example.com", {}))
    assert result.message or result.entities
