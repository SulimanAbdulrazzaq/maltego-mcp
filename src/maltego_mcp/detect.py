"""Lightweight, deterministic detection of what a free-text query refers to.

Used by the unified ``maltego_investigate`` entry point to turn a raw string
(``example.com``, ``test@example.com``, ``1.2.3.4``, ``https://example.com``)
into a Maltego entity type, a normalized value, and the machine best suited to
investigate it. Pure stdlib, no network.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


@dataclass
class Detection:
    """Result of classifying a query string."""

    type_id: str
    value: str
    machine: str
    note: str = ""


def detect(query: str) -> Detection:
    """Classify ``query`` into an entity type + recommended machine.

    Resolution order: URL -> IP (v4/v6) -> email -> domain -> fallback Phrase.
    URLs are reduced to their host domain (the original URL is kept in ``note``).
    """

    q = (query or "").strip()

    # URL -> reduce to its host domain.
    if "://" in q:
        parsed = urlparse(q)
        host = parsed.hostname
        if host:
            return Detection(
                type_id="maltego.Domain",
                value=host,
                machine="passive_domain",
                note=f"derived from URL {q}",
            )

    # IP address (v4 or v6).
    try:
        ip = ipaddress.ip_address(q)
        type_id = "maltego.IPv6Address" if ip.version == 6 else "maltego.IPv4Address"
        return Detection(type_id=type_id, value=q, machine="infrastructure_mapping")
    except ValueError:
        pass

    # Email.
    if _EMAIL_RE.match(q):
        return Detection(
            type_id="maltego.EmailAddress", value=q.lower(), machine="email_investigation"
        )

    # Domain.
    if _DOMAIN_RE.match(q):
        return Detection(
            type_id="maltego.Domain", value=q.lower(), machine="passive_domain"
        )

    # Fallback: treat as a generic phrase (no machine).
    return Detection(type_id="maltego.Phrase", value=q, machine="", note="unclassified query")
