"""Have I Been Pwned provider (API v3).

Env: ``HIBP_API_KEY``. Docs: https://haveibeenpwned.com/API/v3
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

PROVIDER = "hibp"
API_KEY_ENV = "HIBP_API_KEY"
BASE = "https://haveibeenpwned.com/api/v3"


def _headers() -> Dict[str, str]:
    return {
        "hibp-api-key": os.environ.get(API_KEY_ENV, ""),
        "user-agent": "maltego-mcp",
    }


# --- pure parser -------------------------------------------------------------
def parse_breaches(data) -> List[ResultEntity]:
    """Parse /breachedaccount response (a list of breach objects) into Phrases."""

    out: List[ResultEntity] = []
    if not isinstance(data, list):
        return out
    for breach in data:
        name = breach.get("Name") if isinstance(breach, dict) else None
        if name:
            out.append(ResultEntity("maltego.Phrase", str(name), link_label="breached in"))
    return out


# --- transform runner --------------------------------------------------------
async def _email_to_breaches(value: str, props: Dict[str, str]) -> TransformResult:
    ok, msg = require_keys(API_KEY_ENV)
    if not ok:
        return TransformResult(message=msg)
    data, err = await http_get_json(
        f"{BASE}/breachedaccount/{value}",
        headers=_headers(),
        params={"truncateResponse": "true"},
    )
    if err:
        # HIBP returns 404 when an account has no breaches; treat as "clean".
        if "404" in err:
            return TransformResult(message=f"No known breaches for '{value}'.")
        return TransformResult(message=f"HaveIBeenPwned: {err}")
    ents = parse_breaches(data)
    return TransformResult(entities=ents, message=f"Found {len(ents)} breach(es) for '{value}'.")


_TRANSFORMS = [
    Transform(
        name="hibp.email_to_breaches",
        display_name="Email to Breaches [HaveIBeenPwned]",
        description="Data breaches an email address appeared in (HaveIBeenPwned).",
        input_types=["maltego.EmailAddress"],
        output_types=["maltego.Phrase"],
        provider=PROVIDER,
        run=_email_to_breaches,
        requires_network=True,
        api_key_env=API_KEY_ENV,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER,
        display_name="Have I Been Pwned",
        description="Breach exposure for an email address via the HaveIBeenPwned v3 API.",
        env_vars=[API_KEY_ENV],
        website="https://haveibeenpwned.com/API/Key",
        tier="tier2",
        reliability=0.95,
    )
)
for _t in _TRANSFORMS:
    registry.register(_t)
