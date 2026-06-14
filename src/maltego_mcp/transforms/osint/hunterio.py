"""Hunter.io provider (API v2).

Env: ``HUNTER_API_KEY``. Docs: https://hunter.io/api-documentation/v2
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

PROVIDER = "hunterio"
API_KEY_ENV = "HUNTER_API_KEY"
BASE = "https://api.hunter.io/v2"


def _key() -> str:
    return os.environ.get(API_KEY_ENV, "")


# --- pure parser -------------------------------------------------------------
def parse_domain_search(data: dict) -> List[ResultEntity]:
    """Parse /domain-search into EmailAddress entities (+ person name property)."""

    out: List[ResultEntity] = []
    emails = (data.get("data") or {}).get("emails", []) or []
    for item in emails:
        addr = item.get("value")
        if not addr:
            continue
        props: Dict[str, str] = {}
        first = item.get("first_name") or ""
        last = item.get("last_name") or ""
        full = (first + " " + last).strip()
        if full:
            props["person.name"] = full
        out.append(
            ResultEntity(
                "maltego.EmailAddress", addr, properties=props, link_label="Hunter email"
            )
        )
    return out


# --- transform runner --------------------------------------------------------
async def _domain_to_emails(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/domain-search", params={"domain": value, "api_key": _key()}
    )
    if err:
        return TransformResult(message=f"Hunter.io: {err}")
    ents = parse_domain_search(data or {})
    return TransformResult(entities=ents, message=f"Hunter.io returned {len(ents)} email(s).")


_TRANSFORMS = [
    Transform(
        name="hunter.domain_to_emails",
        display_name="Domain to Email Addresses [Hunter.io]",
        description="Email addresses associated with a domain (Hunter.io domain search).",
        input_types=["maltego.Domain"],
        output_types=["maltego.EmailAddress"],
        provider=PROVIDER,
        run=_domain_to_emails,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="Hunter.io",
        description="Email discovery for a domain via the Hunter.io v2 API.",
        env_vars=[API_KEY_ENV],
        website="https://hunter.io/api-keys",
        tier="tier2",
        reliability=0.75,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
