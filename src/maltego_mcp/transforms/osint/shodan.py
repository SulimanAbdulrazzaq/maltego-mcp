"""Shodan provider.

Env: ``SHODAN_API_KEY``. Docs: https://developer.shodan.io/api
"""

from __future__ import annotations

import os
from typing import Dict, List

from maltego_mcp.transforms.base import (
    ProviderInfo,
    ResultEntity,
    Transform,
    TransformResult,
    providers,
    registry,
)
from maltego_mcp.transforms.osint.base_http import http_get_json, require_keys

PROVIDER = "shodan"
API_KEY_ENV = "SHODAN_API_KEY"
BASE = "https://api.shodan.io"


def _key() -> str:
    return os.environ.get(API_KEY_ENV, "")


# --- pure parsers ------------------------------------------------------------
def parse_host(data: dict) -> List[ResultEntity]:
    """Parse /shodan/host/{ip} into Port + DNSName entities."""

    out: List[ResultEntity] = []
    for port in data.get("ports", []) or []:
        out.append(ResultEntity("maltego.Port", str(port), link_label="open port"))
    for host in data.get("hostnames", []) or []:
        out.append(ResultEntity("maltego.DNSName", host, link_label="Shodan hostname"))
    return out


def parse_dns_domain(data: dict) -> List[ResultEntity]:
    """Parse /dns/domain/{domain} into DNSName subdomain entities."""

    out: List[ResultEntity] = []
    domain = data.get("domain", "")
    for sub in data.get("subdomains", []) or []:
        fqdn = f"{sub}.{domain}" if domain and sub else sub
        if fqdn:
            out.append(ResultEntity("maltego.DNSName", fqdn, link_label="Shodan subdomain"))
    return out


# --- transform runners -------------------------------------------------------
async def _ip_to_info(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(f"{BASE}/shodan/host/{value}", params={"key": _key()})
    if err:
        return TransformResult(message=f"Shodan: {err}")
    ents = parse_host(data or {})
    return TransformResult(entities=ents, message=f"Shodan returned {len(ents)} result(s).")


async def _domain_to_subdomains(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(f"{BASE}/dns/domain/{value}", params={"key": _key()})
    if err:
        return TransformResult(message=f"Shodan: {err}")
    ents = parse_dns_domain(data or {})
    return TransformResult(entities=ents, message=f"Shodan returned {len(ents)} subdomain(s).")


_TRANSFORMS = [
    Transform(
        name="shodan.ip_to_info",
        display_name="IP to Ports & Hostnames [Shodan]",
        description="Open ports and hostnames for an IP address (Shodan host lookup).",
        input_types=["maltego.IPv4Address"],
        output_types=["maltego.Port", "maltego.DNSName"],
        provider=PROVIDER,
        run=_ip_to_info,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
    Transform(
        name="shodan.domain_to_subdomains",
        display_name="Domain to Subdomains [Shodan]",
        description="Subdomains of a domain (Shodan DNS).",
        input_types=["maltego.Domain"],
        output_types=["maltego.DNSName"],
        provider=PROVIDER,
        run=_domain_to_subdomains,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="Shodan",
        description="Host port/service intelligence and DNS subdomains via the Shodan API.",
        env_vars=[API_KEY_ENV],
        website="https://account.shodan.io/",
        tier="tier1",
        reliability=0.85,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
