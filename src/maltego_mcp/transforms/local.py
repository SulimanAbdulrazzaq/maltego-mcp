"""Built-in, no-auth transform provider.

These transforms work without any Maltego license or third-party API key, using
only the Python standard library. They exist to (a) make the server immediately
useful for Maltego CE users and (b) demonstrate the provider pattern that a
future Maltego-API or OSINT provider would follow.

Network transforms (DNS lookups) run blocking socket calls inside a thread via
``asyncio.to_thread`` so the async event loop is never blocked. Offline
transforms (parsing a URL or email) perform no I/O.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Dict, List
from urllib.parse import urlparse

from maltego_mcp.transforms.base import (
    ProviderInfo,
    ResultEntity,
    Transform,
    TransformResult,
    providers,
    registry,
)

PROVIDER_NAME = "local"


# --- transform implementations -----------------------------------------------
async def _domain_to_ip(value: str, props: Dict[str, str]) -> TransformResult:
    """Resolve a domain/host to its IPv4 addresses via DNS."""

    def _resolve() -> List[str]:
        infos = socket.getaddrinfo(value, None, family=socket.AF_INET)
        # De-duplicate while preserving order.
        seen: List[str] = []
        for info in infos:
            ip = info[4][0]
            if ip not in seen:
                seen.append(ip)
        return seen

    try:
        ips = await asyncio.to_thread(_resolve)
    except socket.gaierror:
        return TransformResult(message=f"No DNS A record found for '{value}'.")

    return TransformResult(
        entities=[
            ResultEntity(
                type_id="maltego.IPv4Address",
                value=ip,
                link_label="resolves to",
            )
            for ip in ips
        ],
        message=f"Resolved {len(ips)} IPv4 address(es) for '{value}'.",
    )


async def _ip_to_host(value: str, props: Dict[str, str]) -> TransformResult:
    """Reverse-resolve an IPv4 address to a hostname via DNS PTR lookup."""

    def _reverse() -> str:
        host, _aliases, _addrs = socket.gethostbyaddr(value)
        return host

    try:
        host = await asyncio.to_thread(_reverse)
    except (socket.herror, socket.gaierror):
        return TransformResult(message=f"No PTR record found for '{value}'.")

    return TransformResult(
        entities=[
            ResultEntity(
                type_id="maltego.DNSName",
                value=host,
                link_label="reverse DNS",
            )
        ],
        message=f"Reverse DNS for '{value}': {host}",
    )


async def _url_to_domain(value: str, props: Dict[str, str]) -> TransformResult:
    """Extract the domain (host) from a URL. Offline / no network."""

    parsed = urlparse(value if "://" in value else f"http://{value}")
    host = parsed.hostname
    if not host:
        return TransformResult(message=f"Could not extract a domain from '{value}'.")
    return TransformResult(
        entities=[
            ResultEntity(
                type_id="maltego.Domain",
                value=host,
                link_label="domain of URL",
            )
        ],
        message=f"Domain of '{value}': {host}",
    )


async def _email_to_domain(value: str, props: Dict[str, str]) -> TransformResult:
    """Extract the domain part of an email address. Offline / no network."""

    if "@" not in value:
        return TransformResult(message=f"'{value}' is not a valid email address.")
    domain = value.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return TransformResult(message=f"'{value}' has no domain part.")
    return TransformResult(
        entities=[
            ResultEntity(
                type_id="maltego.Domain",
                value=domain,
                link_label="email domain",
            )
        ],
        message=f"Email domain of '{value}': {domain}",
    )


async def _domain_to_website(value: str, props: Dict[str, str]) -> TransformResult:
    """Create the canonical Website entity for a domain. Offline / no network."""

    host = value.strip().lower()
    return TransformResult(
        entities=[
            ResultEntity(
                type_id="maltego.Website",
                value=host,
                link_label="website",
            )
        ],
        message=f"Website entity created for '{host}'.",
    )


# --- registration ------------------------------------------------------------
_TRANSFORMS: List[Transform] = [
    Transform(
        name="dns.domain_to_ip",
        display_name="Domain to IPv4 Address [DNS]",
        description="Resolve a domain or DNS name to its IPv4 addresses (DNS A record).",
        input_types=["maltego.Domain", "maltego.DNSName", "maltego.Website"],
        output_types=["maltego.IPv4Address"],
        provider=PROVIDER_NAME,
        run=_domain_to_ip,
        requires_network=True,
    ),
    Transform(
        name="dns.ip_to_host",
        display_name="IPv4 Address to DNS Name [Reverse DNS]",
        description="Reverse-resolve an IPv4 address to a hostname (DNS PTR record).",
        input_types=["maltego.IPv4Address"],
        output_types=["maltego.DNSName"],
        provider=PROVIDER_NAME,
        run=_ip_to_host,
        requires_network=True,
    ),
    Transform(
        name="parse.url_to_domain",
        display_name="URL to Domain",
        description="Extract the domain (host) from a URL. Offline, no network calls.",
        input_types=["maltego.URL"],
        output_types=["maltego.Domain"],
        provider=PROVIDER_NAME,
        run=_url_to_domain,
        requires_network=False,
    ),
    Transform(
        name="parse.email_to_domain",
        display_name="Email Address to Domain",
        description="Extract the domain part of an email address. Offline, no network calls.",
        input_types=["maltego.EmailAddress"],
        output_types=["maltego.Domain"],
        provider=PROVIDER_NAME,
        run=_email_to_domain,
        requires_network=False,
    ),
    Transform(
        name="parse.domain_to_website",
        display_name="Domain to Website",
        description="Create the canonical Website entity for a domain. Offline, no network calls.",
        input_types=["maltego.Domain"],
        output_types=["maltego.Website"],
        provider=PROVIDER_NAME,
        run=_domain_to_website,
        requires_network=False,
    ),
]

providers.register(
    ProviderInfo(
        name=PROVIDER_NAME,
        display_name="Local (no API key)",
        description="Built-in transforms using only the Python standard library: DNS lookups and offline parsers. No credentials required.",
        env_vars=[],
        website="",
        tier="builtin",
        reliability=0.9,
    )
)

for _t in _TRANSFORMS:
    registry.register(_t)
