"""OSINT transform providers.

Each module in this package implements one external OSINT provider (VirusTotal,
Shodan, ...). A provider:

* registers a :class:`~maltego_mcp.transforms.base.ProviderInfo` describing
  itself and the environment variables it reads, and
* registers one or more :class:`~maltego_mcp.transforms.base.Transform` objects
  (with ``api_key_env`` set) into the shared transform ``registry``.

Importing this package imports every provider module, which performs the
registration as an import side effect. Providers register regardless of whether
their API key is configured -- availability is computed at call time -- so the
catalog of *possible* transforms is always discoverable, and missing keys are
handled gracefully with actionable messages.
"""

from __future__ import annotations

# Importing each module registers its provider + transforms as a side effect.
from maltego_mcp.transforms.osint import virustotal  # noqa: F401
from maltego_mcp.transforms.osint import shodan  # noqa: F401
from maltego_mcp.transforms.osint import securitytrails  # noqa: F401
from maltego_mcp.transforms.osint import censys  # noqa: F401
from maltego_mcp.transforms.osint import hunterio  # noqa: F401
from maltego_mcp.transforms.osint import hibp  # noqa: F401
