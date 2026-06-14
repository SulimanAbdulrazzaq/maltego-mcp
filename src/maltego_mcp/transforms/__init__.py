"""Pluggable transform-provider layer.

A *transform* takes an input entity and returns related entities (and links),
mirroring the core concept in Maltego. Providers are registered against the
shared :data:`registry`; the built-in :mod:`maltego_mcp.transforms.local`
provider ships no-auth transforms. Future providers (a real Maltego API, or
third-party OSINT services such as Shodan/VirusTotal) implement the same
:class:`~maltego_mcp.transforms.base.TransformProvider` interface and register
themselves the same way -- no changes to the core or the MCP tools required.
"""

from maltego_mcp.transforms.base import (
    ProviderInfo,
    ProviderRegistry,
    ResultEntity,
    Transform,
    TransformProvider,
    TransformRegistry,
    TransformResult,
    providers,
    registry,
)

# Importing the providers registers their transforms as a side effect.
from maltego_mcp.transforms import local  # noqa: E402,F401
from maltego_mcp.transforms import osint  # noqa: E402,F401

__all__ = [
    "ProviderInfo",
    "ProviderRegistry",
    "ResultEntity",
    "Transform",
    "TransformProvider",
    "TransformRegistry",
    "TransformResult",
    "providers",
    "registry",
]
