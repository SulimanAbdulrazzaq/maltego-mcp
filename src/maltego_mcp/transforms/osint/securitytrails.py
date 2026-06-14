"""SecurityTrails provider (API v1).

Env: ``SECURITYTRAILS_API_KEY``. Docs: https://docs.securitytrails.com/
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

PROVIDER = "securitytrails"
API_KEY_ENV = "SECURITYTRAILS_API_KEY"
BASE = "https://api.securitytrails.com/v1"


def _headers() -> Dict[str, str]:
    return {"APIKEY": os.environ.get(API_KEY_ENV, "")}


# --- pure parsers ------------------------------------------------------------
def parse_subdomains(data: dict, apex: str) -> List[ResultEntity]:
    out: List[ResultEntity] = []
    for sub in data.get("subdomains", []) or []:
        fqdn = f"{sub}.{apex}" if apex else sub
        out.append(ResultEntity("maltego.DNSName", fqdn, link_label="ST subdomain"))
    return out


def parse_current_dns_a(data: dict) -> List[ResultEntity]:
    """Parse /domain/{d} current_dns A records into IPv4Address entities."""

    out: List[ResultEntity] = []
    a_record = (data.get("current_dns") or {}).get("a") or {}
    for value in a_record.get("values", []) or []:
        ip = value.get("ip")
        if ip:
            out.append(ResultEntity("maltego.IPv4Address", ip, link_label="ST A record"))
    return out


# --- transform runners -------------------------------------------------------
async def _domain_to_subdomains(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/domain/{value}/subdomains", headers=_headers()
    )
    if err:
        return TransformResult(message=f"SecurityTrails: {err}")
    ents = parse_subdomains(data or {}, value)
    return TransformResult(entities=ents, message=f"SecurityTrails returned {len(ents)} subdomain(s).")


async def _domain_to_dns(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(f"{BASE}/domain/{value}", headers=_headers())
    if err:
        return TransformResult(message=f"SecurityTrails: {err}")
    ents = parse_current_dns_a(data or {})
    return TransformResult(entities=ents, message=f"SecurityTrails returned {len(ents)} A record(s).")


_TRANSFORMS = [
    Transform(
        name="securitytrails.domain_to_subdomains",
        display_name="Domain to Subdomains [SecurityTrails]",
        description="Subdomains of a domain (SecurityTrails).",
        input_types=["maltego.Domain"],
        output_types=["maltego.DNSName"],
        provider=PROVIDER,
        run=_domain_to_subdomains,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
    Transform(
        name="securitytrails.domain_to_dns",
        display_name="Domain to A Records [SecurityTrails]",
        description="Current DNS A records (IPv4) for a domain (SecurityTrails).",
        input_types=["maltego.Domain"],
        output_types=["maltego.IPv4Address"],
        provider=PROVIDER,
        run=_domain_to_dns,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="SecurityTrails",
        description="Historical and current DNS, subdomains and A records via SecurityTrails v1.",
        env_vars=[API_KEY_ENV],
        website="https://securitytrails.com/corp/api",
        tier="tier1",
        reliability=0.88,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
