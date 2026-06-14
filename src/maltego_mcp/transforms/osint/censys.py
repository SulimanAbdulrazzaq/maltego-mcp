"""Censys provider (Search API v2).

Env: ``CENSYS_API_ID`` and ``CENSYS_API_SECRET`` (HTTP basic auth).
Docs: https://search.censys.io/api
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

PROVIDER = "censys"
API_ID_ENV = "CENSYS_API_ID"
API_SECRET_ENV = "CENSYS_API_SECRET"
BASE = "https://search.censys.io/api/v2"


# --- pure parser -------------------------------------------------------------
def parse_host_services(data: dict) -> List[ResultEntity]:
    """Parse /hosts/{ip} into Port and Service entities."""

    out: List[ResultEntity] = []
    result = (data.get("result") or {})
    for svc in result.get("services", []) or []:
        port = svc.get("port")
        name = svc.get("service_name")
        if port is not None:
            out.append(ResultEntity("maltego.Port", str(port), link_label="Censys port"))
        if name:
            out.append(ResultEntity("maltego.Service", str(name), link_label="Censys service"))
    return out


# --- transform runner --------------------------------------------------------
async def _ip_to_services(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_ID_ENV, API_SECRET_ENV)
    if not ok:
        return TransformResult(message=msg)
    auth = (os.environ.get(API_ID_ENV, ""), os.environ.get(API_SECRET_ENV, ""))
    data, err = await http_get_json(f"{BASE}/hosts/{value}", auth=auth)
    if err:
        return TransformResult(message=f"Censys: {err}")
    ents = parse_host_services(data or {})
    return TransformResult(entities=ents, message=f"Censys returned {len(ents)} result(s).")


_TRANSFORMS = [
    Transform(
        name="censys.ip_to_services",
        display_name="IP to Ports & Services [Censys]",
        description="Open ports and detected services for an IP address (Censys hosts).",
        input_types=["maltego.IPv4Address"],
        output_types=["maltego.Port", "maltego.Service"],
        provider=PROVIDER,
        run=_ip_to_services,
        requires_network=True,
        api_key_env=API_ID_ENV,
        extra_key_envs=[API_SECRET_ENV],  # both ID and secret required
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="Censys",
        description="Host port/service scanning intelligence via the Censys Search v2 API.",
        env_vars=[API_ID_ENV, API_SECRET_ENV],
        website="https://search.censys.io/account/api",
        tier="tier2",
        reliability=0.85,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
