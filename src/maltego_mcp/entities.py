"""Catalog of common Maltego entity types.

Maltego identifies every entity by a fully-qualified type id (for example
``maltego.Domain``). Each type has one *main* property whose value is the
entity's primary value (the text shown on the graph). This module captures a
curated subset of the built-in Maltego entity types so the server can:

* validate / normalize the ``type`` argument supplied to tools,
* know which property name to use when storing the entity's main value,
* give agents a discoverable list of supported types.

The catalog is intentionally a plain data structure (no I/O) so it can be reused
by the graph store, the ``.mtgx`` writer, and transform providers alike. It is
not exhaustive -- :func:`is_known_type` is advisory, and the graph store accepts
unknown ``maltego.*`` types so investigations are never blocked by a missing
entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: Property type used for almost every Maltego string value.
STRING = "string"


@dataclass(frozen=True)
class EntityType:
    """Static description of a Maltego entity type.

    Attributes:
        type_id: Fully-qualified Maltego type id (e.g. ``maltego.Domain``).
        display_name: Human-friendly label (e.g. ``Domain``).
        main_property: Property name that holds the entity's primary value
            (e.g. ``maltego.domain.fqdn``).
        main_display_name: Display name for the main property (e.g. ``Domain``).
        category: Loose grouping used only for discovery/listing.
    """

    type_id: str
    display_name: str
    main_property: str
    main_display_name: str
    category: str = "general"


# --- Curated catalog of common built-in Maltego entity types -----------------
# Main property names match Maltego's built-in entities so that exported graphs
# populate the correct fields when opened in the Maltego client.
_ENTITY_TYPES: List[EntityType] = [
    # Infrastructure
    EntityType("maltego.Domain", "Domain", "fqdn", "Domain Name", "infrastructure"),
    EntityType("maltego.DNSName", "DNS Name", "fqdn", "DNS Name", "infrastructure"),
    EntityType("maltego.MXRecord", "MX Record", "fqdn", "MX Record", "infrastructure"),
    EntityType("maltego.NSRecord", "NS Record", "fqdn", "NS Record", "infrastructure"),
    EntityType("maltego.IPv4Address", "IPv4 Address", "ipv4-address", "IP Address", "infrastructure"),
    EntityType("maltego.IPv6Address", "IPv6 Address", "ipv6-address", "IPv6 Address", "infrastructure"),
    EntityType("maltego.Netblock", "Netblock", "ipv4-range", "IP Range", "infrastructure"),
    EntityType("maltego.AS", "AS", "as.number", "AS Number", "infrastructure"),
    # Maltego's Website entity uses the same 'fqdn' field as Domain/DNSName.
    EntityType("maltego.Website", "Website", "fqdn", "Website", "infrastructure"),
    EntityType("maltego.URL", "URL", "url", "URL", "infrastructure"),
    EntityType("maltego.Port", "Port", "port.number", "Port", "infrastructure"),
    EntityType("maltego.Service", "Service", "service.name", "Service", "infrastructure"),
    EntityType("maltego.Banner", "Banner", "banner.text", "Banner", "infrastructure"),
    # Identity / people
    EntityType("maltego.Person", "Person", "person.fullname", "Full Name", "personal"),
    EntityType("maltego.EmailAddress", "Email Address", "email", "Email Address", "personal"),
    EntityType("maltego.PhoneNumber", "Phone Number", "phonenumber", "Phone Number", "personal"),
    EntityType("maltego.Alias", "Alias", "alias", "Alias", "personal"),
    EntityType("maltego.Document", "Document", "document.title", "Title", "personal"),
    # Social
    EntityType("maltego.affiliation.Twitter", "Twitter Account", "twitter.screen-name", "Screen Name", "social"),
    EntityType("maltego.affiliation.Facebook", "Facebook Account", "affiliation.name", "Name", "social"),
    EntityType("maltego.affiliation.Linkedin", "LinkedIn Account", "affiliation.name", "Name", "social"),
    # Organization / location
    EntityType("maltego.Company", "Company", "title", "Name", "organization"),
    EntityType("maltego.Organization", "Organization", "title", "Name", "organization"),
    EntityType("maltego.Location", "Location", "location.name", "Name", "location"),
    EntityType("maltego.GPS", "GPS Coordinate", "gps", "GPS Coordinate", "location"),
    # Malware / threat-intel
    EntityType("maltego.Hash", "Hash", "properties.hash", "Hash", "malware"),
    EntityType("maltego.Hashtag", "Hashtag", "properties.hashtag", "Hashtag", "social"),
    # Generic
    EntityType("maltego.Phrase", "Phrase", "text", "Text", "general"),
    EntityType("maltego.Image", "Image", "properties.image", "Image", "general"),
]

#: Lookup of type id -> :class:`EntityType`.
ENTITY_TYPES: Dict[str, EntityType] = {et.type_id: et for et in _ENTITY_TYPES}

#: Default type used when a value's type cannot be determined.
DEFAULT_TYPE = "maltego.Phrase"


def get_entity_type(type_id: str) -> Optional[EntityType]:
    """Return the :class:`EntityType` for ``type_id`` or ``None`` if unknown."""

    return ENTITY_TYPES.get(type_id)


def is_known_type(type_id: str) -> bool:
    """Return ``True`` if ``type_id`` is in the curated catalog."""

    return type_id in ENTITY_TYPES


def main_property_for(type_id: str) -> str:
    """Return the main property name for ``type_id``.

    Falls back to a generic ``properties.value`` for unknown types so that even
    custom/unsupported Maltego entity types round-trip through the graph.
    """

    et = ENTITY_TYPES.get(type_id)
    return et.main_property if et else "properties.value"


def main_display_name_for(type_id: str) -> str:
    """Return the main-property display name for ``type_id``."""

    et = ENTITY_TYPES.get(type_id)
    return et.main_display_name if et else "Value"


def list_categories() -> Dict[str, List[EntityType]]:
    """Group the catalog by category for discovery/listing."""

    grouped: Dict[str, List[EntityType]] = {}
    for et in _ENTITY_TYPES:
        grouped.setdefault(et.category, []).append(et)
    return grouped
