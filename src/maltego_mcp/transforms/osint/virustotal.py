"""VirusTotal provider (API v3).

Env: ``VIRUSTOTAL_API_KEY``. Docs: https://docs.virustotal.com/reference
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
from maltego_mcp.transforms.osint.base_http import (
    http_get_json,
    require_keys,
)

PROVIDER = "virustotal"
API_KEY_ENV = "VIRUSTOTAL_API_KEY"
BASE = "https://www.virustotal.com/api/v3"


def _headers() -> Dict[str, str]:
    return {"x-apikey": os.environ.get(API_KEY_ENV, "")}


# --- pure parsers (unit-testable, no network) --------------------------------
def parse_resolutions_to_ips(data: dict) -> List[ResultEntity]:
    """Parse /domains/{d}/resolutions into IPv4Address entities."""

    out: List[ResultEntity] = []
    for item in data.get("data", []):
        ip = (item.get("attributes") or {}).get("ip_address")
        if ip:
            out.append(
                ResultEntity("maltego.IPv4Address", ip, link_label="VT resolution")
            )
    return out


def parse_subdomains(data: dict) -> List[ResultEntity]:
    out: List[ResultEntity] = []
    for item in data.get("data", []):
        sub = item.get("id")
        if sub:
            out.append(ResultEntity("maltego.DNSName", sub, link_label="VT subdomain"))
    return out


def parse_ip_resolutions_to_domains(data: dict) -> List[ResultEntity]:
    out: List[ResultEntity] = []
    for item in data.get("data", []):
        host = (item.get("attributes") or {}).get("host_name")
        if host:
            out.append(ResultEntity("maltego.Domain", host, link_label="VT resolution"))
    return out


# --- transform runners -------------------------------------------------------
async def _domain_to_ip(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/domains/{value}/resolutions", headers=_headers(), params={"limit": 40}
    )
    if err:
        return TransformResult(message=f"VirusTotal: {err}")
    ents = parse_resolutions_to_ips(data or {})
    return TransformResult(entities=ents, message=f"VirusTotal returned {len(ents)} IP(s).")


async def _domain_to_subdomains(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/domains/{value}/subdomains", headers=_headers(), params={"limit": 40}
    )
    if err:
        return TransformResult(message=f"VirusTotal: {err}")
    ents = parse_subdomains(data or {})
    return TransformResult(entities=ents, message=f"VirusTotal returned {len(ents)} subdomain(s).")


async def _ip_to_domain(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/ip_addresses/{value}/resolutions", headers=_headers(), params={"limit": 40}
    )
    if err:
        return TransformResult(message=f"VirusTotal: {err}")
    ents = parse_ip_resolutions_to_domains(data or {})
    return TransformResult(entities=ents, message=f"VirusTotal returned {len(ents)} domain(s).")


_TRANSFORMS = [
    Transform(
        name="vt.domain_to_ip",
        display_name="Domain to IP [VirusTotal]",
        description="Passive DNS resolutions for a domain (VirusTotal).",
        input_types=["maltego.Domain", "maltego.DNSName"],
        output_types=["maltego.IPv4Address"],
        provider=PROVIDER,
        run=_domain_to_ip,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
    Transform(
        name="vt.domain_to_subdomains",
        display_name="Domain to Subdomains [VirusTotal]",
        description="Known subdomains of a domain (VirusTotal).",
        input_types=["maltego.Domain"],
        output_types=["maltego.DNSName"],
        provider=PROVIDER,
        run=_domain_to_subdomains,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
    Transform(
        name="vt.ip_to_domain",
        display_name="IP to Domains [VirusTotal]",
        description="Domains that have resolved to an IP (VirusTotal passive DNS).",
        input_types=["maltego.IPv4Address"],
        output_types=["maltego.Domain"],
        provider=PROVIDER,
        run=_ip_to_domain,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="VirusTotal",
        description="Passive DNS, subdomains and resolutions via the VirusTotal v3 API.",
        env_vars=[API_KEY_ENV],
        website="https://www.virustotal.com/gui/join-us",
        tier="tier1",
        reliability=0.9,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
