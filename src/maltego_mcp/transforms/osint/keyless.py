"""Keyless OSINT providers — high-value sources that need NO API key.

These dramatically increase what an investigation finds out of the box:

* **crt.sh** — Certificate Transparency search → subdomains of a domain.
* **RDAP** (rdap.org) — the modern, JSON, keyless successor to WHOIS →
  domain registration (registrar, dates, nameservers) and IP/ASN ownership.

All run over HTTPS with no credentials. Parsers are pure functions so they are
unit-tested without network access.
"""

from __future__ import annotations

from typing import Dict, List

from maltego_mcp.transforms.base import (
    ProviderInfo,
    ResultEntity,
    Transform,
    TransformResult,
    providers,
    registry,
)
from maltego_mcp.transforms.osint.base_http import http_get_json

CRTSH = "crtsh"
RDAP = "rdap"


# --- crt.sh ------------------------------------------------------------------
def parse_crtsh(data) -> List[ResultEntity]:
    """Parse crt.sh JSON (a list of cert records) into DNSName subdomains.

    Each record has a ``name_value`` field that may hold several newline-separated
    names (including wildcards). De-duplicate, drop wildcards/emails.
    """

    out: List[ResultEntity] = []
    seen = set()
    if not isinstance(data, list):
        return out
    for rec in data:
        if not isinstance(rec, dict):
            continue
        names = str(rec.get("name_value", "")).split("\n")
        names.append(str(rec.get("common_name", "")))
        for name in names:
            host = name.strip().lower().lstrip("*.").strip()
            if not host or "@" in host or " " in host or host in seen:
                continue
            seen.add(host)
            out.append(ResultEntity("maltego.DNSName", host, link_label="crt.sh cert"))
    return out


async def _crtsh_subdomains(value: str, props: Dict[str, str]) -> TransformResult:
    data, err = await http_get_json(
        "https://crt.sh/", params={"q": f"%.{value}", "output": "json"}, timeout=30.0
    )
    if err:
        return TransformResult(message=f"crt.sh: {err}")
    ents = parse_crtsh(data)
    return TransformResult(entities=ents, message=f"crt.sh returned {len(ents)} subdomain(s).")


# --- RDAP domain -------------------------------------------------------------
def _vcard_email(entity: dict) -> str:
    """Pull an email from an RDAP entity's jCard (vcardArray), if present."""

    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2:
        return ""
    for field in vcard[1]:
        if isinstance(field, list) and field and field[0] == "email":
            return str(field[-1])
    return ""


def parse_rdap_domain(data: dict) -> tuple:
    """Parse an RDAP domain response.

    Returns ``(entities, properties)`` — DNSName nameservers + EmailAddress
    contacts as entities, and registrar/date facts as properties to annotate the
    queried domain.
    """

    entities: List[ResultEntity] = []
    props: Dict[str, str] = {}
    if not isinstance(data, dict):
        return entities, props

    for ns in data.get("nameservers", []) or []:
        host = (ns.get("ldhName") or "").strip().lower()
        if host:
            entities.append(ResultEntity("maltego.DNSName", host, link_label="nameserver"))

    for ev in data.get("events", []) or []:
        action = ev.get("eventAction")
        date = ev.get("eventDate")
        if action and date:
            props[f"rdap.{action}"] = str(date)

    for ent in data.get("entities", []) or []:
        roles = ent.get("roles") or []
        email = _vcard_email(ent)
        if email:
            entities.append(
                ResultEntity("maltego.EmailAddress", email.lower(), link_label=",".join(roles) or "contact")
            )
        if "registrar" in roles:
            # registrar org name often in vcard 'fn'
            vcard = ent.get("vcardArray")
            if isinstance(vcard, list) and len(vcard) > 1:
                for field in vcard[1]:
                    if isinstance(field, list) and field and field[0] == "fn":
                        props["rdap.registrar"] = str(field[-1])
    return entities, props


async def _rdap_domain(value: str, props: Dict[str, str]) -> TransformResult:
    data, err = await http_get_json(f"https://rdap.org/domain/{value}")
    if err:
        return TransformResult(message=f"RDAP: {err}")
    ents, _facts = parse_rdap_domain(data or {})
    return TransformResult(entities=ents, message=f"RDAP returned {len(ents)} related entit(y/ies).")


# --- RDAP ip -----------------------------------------------------------------
def parse_rdap_ip(data: dict) -> List[ResultEntity]:
    """Parse an RDAP IP response into Netblock + Organization entities."""

    out: List[ResultEntity] = []
    if not isinstance(data, dict):
        return out
    start = data.get("startAddress")
    end = data.get("endAddress")
    cidr = data.get("handle")
    block = None
    if start and end:
        block = f"{start} - {end}"
    elif cidr:
        block = str(cidr)
    if block:
        out.append(ResultEntity("maltego.Netblock", block, link_label="netblock"))
    name = data.get("name")
    if name:
        out.append(ResultEntity("maltego.Organization", str(name), link_label="network owner"))
    for ent in data.get("entities", []) or []:
        vcard = ent.get("vcardArray")
        if isinstance(vcard, list) and len(vcard) > 1:
            for field in vcard[1]:
                if isinstance(field, list) and field and field[0] == "fn":
                    org = str(field[-1]).strip()
                    if org and org != name:
                        out.append(ResultEntity("maltego.Organization", org, link_label="registrant"))
    return out


async def _rdap_ip(value: str, props: Dict[str, str]) -> TransformResult:
    data, err = await http_get_json(f"https://rdap.org/ip/{value}")
    if err:
        return TransformResult(message=f"RDAP: {err}")
    ents = parse_rdap_ip(data or {})
    return TransformResult(entities=ents, message=f"RDAP returned {len(ents)} ownership entit(y/ies).")


# --- registration ------------------------------------------------------------
providers.register(
    ProviderInfo(
        name=CRTSH,
        display_name="crt.sh (Certificate Transparency)",
        description="Subdomain enumeration from Certificate Transparency logs. No API key required.",
        env_vars=[],
        website="https://crt.sh",
        tier="keyless",
        reliability=0.85,
    )
)
providers.register(
    ProviderInfo(
        name=RDAP,
        display_name="RDAP (rdap.org)",
        description="Keyless modern WHOIS: domain registration (registrar, dates, nameservers) and IP/ASN ownership.",
        env_vars=[],
        website="https://rdap.org",
        tier="keyless",
        reliability=0.88,
    )
)

_TRANSFORMS = [
    Transform(
        name="crtsh.domain_to_subdomains",
        display_name="Domain to Subdomains [crt.sh]",
        description="Enumerate subdomains from Certificate Transparency logs (crt.sh). No API key.",
        input_types=["maltego.Domain"],
        output_types=["maltego.DNSName"],
        provider=CRTSH,
        run=_crtsh_subdomains,
        requires_network=True,
    ),
    Transform(
        name="rdap.domain_info",
        display_name="Domain Registration [RDAP]",
        description="Registrar, registration/expiry dates, nameservers and contacts for a domain (RDAP). No API key.",
        input_types=["maltego.Domain"],
        output_types=["maltego.DNSName", "maltego.EmailAddress"],
        provider=RDAP,
        run=_rdap_domain,
        requires_network=True,
    ),
    Transform(
        name="rdap.ip_info",
        display_name="IP Ownership [RDAP]",
        description="Netblock and owning organization/ASN for an IP address (RDAP). No API key.",
        input_types=["maltego.IPv4Address", "maltego.IPv6Address"],
        output_types=["maltego.Netblock", "maltego.Organization"],
        provider=RDAP,
        run=_rdap_ip,
        requires_network=True,
    ),
]

for _t in _TRANSFORMS:
    registry.register(_t)
