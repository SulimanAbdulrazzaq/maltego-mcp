"""Transform-provider abstraction and registry.

This is the primary extension point of the server. A *transform* takes one
input entity (type + value) and returns related entities and links -- exactly
the Maltego concept. Concrete providers implement :class:`TransformProvider`
and register their transforms with the shared :data:`registry`.

The built-in :mod:`maltego_mcp.transforms.local` provider ships no-auth
transforms that work without any Maltego license or third-party key. To add a
real Maltego API or an OSINT service (Shodan, VirusTotal, Hunter.io, ...), write
a new provider that subclasses :class:`TransformProvider`, build its
:class:`Transform` objects, and call ``registry.register(...)`` -- no changes to
the graph core or the MCP tools are required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Protocol


@dataclass
class ResultEntity:
    """An entity produced by a transform (not yet placed on a graph)."""

    type_id: str
    value: str
    properties: Dict[str, str] = field(default_factory=dict)
    #: Label for the link drawn from the input entity to this result.
    link_label: str = ""


@dataclass
class TransformResult:
    """The output of running a transform against one input entity."""

    entities: List[ResultEntity] = field(default_factory=list)
    #: Optional human-readable message (e.g. "no records found").
    message: str = ""


# A transform function receives the input entity's value plus its full property
# map and returns a TransformResult. It is async so providers may do network I/O.
TransformFn = Callable[[str, Dict[str, str]], Awaitable[TransformResult]]


@dataclass
class Transform:
    """Metadata + callable for a single transform.

    Attributes:
        name: Unique transform name (e.g. ``dns.domain_to_ip``).
        display_name: Human-friendly label.
        description: What the transform does.
        input_types: Maltego entity types this transform accepts.
        output_types: Maltego entity types it may produce.
        provider: Name of the owning provider.
        requires_network: True if the transform performs network I/O.
        api_key_env: Name of the environment variable holding the API key this
            transform needs, or ``None`` if no key is required. Used to compute
            availability and to give actionable "missing key" errors.
        extra_key_envs: Additional environment variables that must ALSO be set
            for the transform to run (e.g. an API id + secret pair). All of them,
            plus ``api_key_env``, are required for availability.
        run: The async callable implementing the transform.
    """

    name: str
    display_name: str
    description: str
    input_types: List[str]
    output_types: List[str]
    provider: str
    run: TransformFn
    requires_network: bool = False
    api_key_env: Optional[str] = None
    extra_key_envs: List[str] = field(default_factory=list)

    def accepts(self, type_id: str) -> bool:
        return type_id in self.input_types

    def required_key_envs(self) -> List[str]:
        """All environment variables required for this transform to run."""

        envs = [self.api_key_env] if self.api_key_env else []
        return envs + list(self.extra_key_envs)

    def is_available(self) -> bool:
        """True if the transform can run now (no key needed, or all keys present)."""

        return all(bool(os.environ.get(v)) for v in self.required_key_envs())

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "input_types": list(self.input_types),
            "output_types": list(self.output_types),
            "provider": self.provider,
            "requires_network": self.requires_network,
            "api_key_env": self.api_key_env,
            "extra_key_envs": list(self.extra_key_envs),
            "available": self.is_available(),
        }


class TransformProvider(Protocol):
    """Interface every transform provider implements."""

    name: str

    def transforms(self) -> List[Transform]:
        """Return the transforms this provider exposes."""
        ...


class TransformRegistry:
    """Central registry of all available transforms, keyed by name."""

    def __init__(self) -> None:
        self._transforms: Dict[str, Transform] = {}

    def register(self, transform: Transform) -> None:
        if transform.name in self._transforms:
            raise ValueError(f"Transform '{transform.name}' already registered.")
        self._transforms[transform.name] = transform

    def register_provider(self, provider: TransformProvider) -> None:
        for transform in provider.transforms():
            self.register(transform)

    def get(self, name: str) -> Optional[Transform]:
        return self._transforms.get(name)

    def all(self) -> List[Transform]:
        return list(self._transforms.values())

    def for_type(self, type_id: str, available_only: bool = False) -> List[Transform]:
        """Return transforms that accept ``type_id`` as input.

        When ``available_only`` is true, transforms whose required API key is
        missing are excluded.
        """

        return [
            t
            for t in self._transforms.values()
            if t.accepts(type_id) and (not available_only or t.is_available())
        ]

    def by_provider(self, provider: str) -> List[Transform]:
        return [t for t in self._transforms.values() if t.provider == provider]


@dataclass
class ProviderInfo:
    """Describes an OSINT provider (a source of transforms).

    Providers are kept conceptually separate from individual transforms: a
    provider bundles related transforms, declares the environment variables it
    needs, and reports whether it is currently configured.

    Attributes:
        name: Stable provider id (e.g. ``virustotal``); matches
            :attr:`Transform.provider`.
        display_name: Human-friendly name.
        description: What the provider offers.
        env_vars: Environment variable names this provider reads for credentials.
        website: Where to obtain an API key.
        tier: Loose grouping ("builtin", "tier1", "tier2").
        reliability: Provider trustworthiness in [0, 1], used by the scoring
            engine as a source-reliability signal. Defaults to 0.7 so unknown
            third-party providers get a sensible middling score.
    """

    name: str
    display_name: str
    description: str
    env_vars: List[str] = field(default_factory=list)
    website: str = ""
    tier: str = "tier1"
    reliability: float = 0.7

    def is_configured(self) -> bool:
        """True if all required env vars are set (or none are required)."""

        return all(bool(os.environ.get(v)) for v in self.env_vars)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "env_vars": list(self.env_vars),
            "website": self.website,
            "tier": self.tier,
            "reliability": self.reliability,
            "configured": self.is_configured(),
        }


class ProviderRegistry:
    """Registry of provider metadata, kept separate from the transforms."""

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderInfo] = {}

    def register(self, info: ProviderInfo) -> None:
        # Idempotent: re-registering the same provider id is a no-op overwrite,
        # which keeps module re-imports during testing harmless.
        self._providers[info.name] = info

    def get(self, name: str) -> Optional[ProviderInfo]:
        return self._providers.get(name)

    def all(self) -> List[ProviderInfo]:
        return sorted(self._providers.values(), key=lambda p: (p.tier, p.name))


#: Process-wide registry populated at import time by the providers.
registry = TransformRegistry()

#: Process-wide registry of provider metadata.
providers = ProviderRegistry()
