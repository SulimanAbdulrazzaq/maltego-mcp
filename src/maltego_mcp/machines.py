"""Investigation Machines -- reusable, configurable workflow templates.

A *Machine* is a named, reusable investigation recipe (analogous to Maltego
Machines): it seeds an entity of a given type and runs a curated set of
transforms over several rounds using the orchestration engine. Machines are the
high-level, shareable layer on top of :mod:`maltego_mcp.orchestration`.

Machines are extensible: third-party providers (or users) can register their own
via :func:`register_machine`, and because a Machine references transforms *by
name*, any transform a provider adds to the registry can participate. Curated
transform lists are advisory -- if a referenced transform is not registered or
its API key is missing, the engine simply skips it, so a Machine degrades
gracefully to whatever is currently available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from maltego_mcp.graph.graph_store import Graph
from maltego_mcp.orchestration import ExpansionReport, expand


@dataclass
class Machine:
    """A configurable investigation workflow template."""

    name: str
    display_name: str
    description: str
    seed_type: str
    transform_names: Optional[List[str]] = None
    allow_network: bool = True
    available_only: bool = True
    max_rounds: int = 2

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "seed_type": self.seed_type,
            "transform_names": list(self.transform_names) if self.transform_names else None,
            "allow_network": self.allow_network,
            "max_rounds": self.max_rounds,
        }


class MachineRegistry:
    """Registry of available investigation machines."""

    def __init__(self) -> None:
        self._machines: Dict[str, Machine] = {}

    def register(self, machine: Machine) -> None:
        self._machines[machine.name] = machine

    def get(self, name: str) -> Optional[Machine]:
        return self._machines.get(name)

    def all(self) -> List[Machine]:
        return sorted(self._machines.values(), key=lambda m: m.name)


registry = MachineRegistry()


def register_machine(machine: Machine) -> None:
    """Public hook for third parties to add a Machine."""

    registry.register(machine)


async def run_machine(
    graph: Graph,
    machine_name: str,
    seed_value: str,
    *,
    allow_network: Optional[bool] = None,
    max_rounds: Optional[int] = None,
) -> ExpansionReport:
    """Run a registered machine on ``graph`` against ``seed_value``.

    Ensures the seed entity exists (creating it with the machine's seed type if
    needed), then expands the graph per the machine's configuration. Optional
    ``allow_network`` / ``max_rounds`` override the machine defaults.

    Raises ``KeyError`` if the machine name is unknown.
    """

    machine = registry.get(machine_name)
    if machine is None:
        raise KeyError(machine_name)

    seed = graph.add_entity(type_id=machine.seed_type, value=seed_value, dedupe=True)
    return await expand(
        graph,
        seed.id,
        transform_names=machine.transform_names,
        allow_network=machine.allow_network if allow_network is None else allow_network,
        available_only=machine.available_only,
        max_rounds=machine.max_rounds if max_rounds is None else max_rounds,
    )


# --- built-in machines -------------------------------------------------------
_BUILTINS = [
    Machine(
        name="passive_domain",
        display_name="Passive Domain Investigation",
        description=(
            "Map a domain's footprint passively: resolve IPs, find the website, "
            "and enumerate subdomains via passive-DNS providers. Avoids active "
            "port scanning."
        ),
        seed_type="maltego.Domain",
        transform_names=[
            "dns.domain_to_ip",
            "dns.ip_to_host",
            "parse.domain_to_website",
            "crtsh.domain_to_subdomains",
            "rdap.domain_info",
            "vt.domain_to_ip",
            "vt.domain_to_subdomains",
            "securitytrails.domain_to_subdomains",
            "securitytrails.domain_to_dns",
            "shodan.domain_to_subdomains",
        ],
        allow_network=True,
        max_rounds=2,
    ),
    Machine(
        name="email_investigation",
        display_name="Email Investigation",
        description=(
            "Investigate an email address: derive its domain, check breach "
            "exposure, and expand the associated domain's basic footprint."
        ),
        seed_type="maltego.EmailAddress",
        transform_names=[
            "parse.email_to_domain",
            "hibp.email_to_breaches",
            "dns.domain_to_ip",
            "parse.domain_to_website",
        ],
        allow_network=True,
        max_rounds=2,
    ),
    Machine(
        name="infrastructure_mapping",
        display_name="Infrastructure Mapping",
        description=(
            "Map hosting infrastructure for a domain: resolve to IPs, reverse-DNS "
            "those IPs, and enumerate open ports/services via Shodan and Censys."
        ),
        seed_type="maltego.Domain",
        transform_names=[
            "dns.domain_to_ip",
            "securitytrails.domain_to_dns",
            "dns.ip_to_host",
            "rdap.ip_info",
            "shodan.ip_to_info",
            "censys.ip_to_services",
        ],
        allow_network=True,
        max_rounds=3,
    ),
]

for _m in _BUILTINS:
    registry.register(_m)
