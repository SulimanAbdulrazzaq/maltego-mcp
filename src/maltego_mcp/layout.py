"""Deterministic graph layout algorithms.

Computes (x, y) positions for entities to reduce visual clutter in large
investigations. Three layouts are provided:

* ``hierarchical`` -- BFS layering from the most-connected node; layers stack
  vertically, siblings spread horizontally.
* ``radial`` -- the most-connected node at the centre; other nodes on concentric
  rings by BFS distance.
* ``force`` -- a deterministic Fruchterman-Reingold force-directed layout
  (fixed seed positions, fixed iteration count -> reproducible output).

All algorithms are deterministic: given the same graph they always produce the
same coordinates (no RNG), so reports and round-trips are reproducible.
Positions are assigned back onto the graph via :meth:`Graph.set_positions` and
persisted in the ``.mtgx`` file by the writer.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from maltego_mcp.graph.graph_store import Graph

LAYOUTS = ("hierarchical", "radial", "force")

_SPACING_X = 160.0
_SPACING_Y = 120.0
_RING_STEP = 180.0


def _ordered_ids(graph: Graph) -> List[str]:
    """Entity ids in a stable order (by node-id numeric suffix when possible)."""

    def key(eid: str):
        if eid.startswith("n") and eid[1:].isdigit():
            return (0, int(eid[1:]))
        return (1, eid)

    return sorted((e.id for e in graph.entities), key=key)


def _adjacency(graph: Graph) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {eid: [] for eid in _ordered_ids(graph)}
    for link in graph.links:
        if link.source_id in adj and link.target_id in adj:
            adj[link.source_id].append(link.target_id)
            adj[link.target_id].append(link.source_id)
    # Deterministic neighbour order.
    for eid in adj:
        adj[eid] = sorted(set(adj[eid]))
    return adj


def _root(graph: Graph, ids: List[str]) -> Optional[str]:
    """Pick a deterministic root: highest degree, then lowest id order."""

    if not ids:
        return None
    return max(ids, key=lambda eid: (graph.degree(eid), -ids.index(eid)))


def _bfs_layers(adj: Dict[str, List[str]], ids: List[str]) -> List[List[str]]:
    """Group ids into BFS layers covering all components deterministically."""

    visited: set = set()
    layers: List[List[str]] = []
    # Order roots so the most-connected component anchors first.
    roots = sorted(ids, key=lambda eid: (-len(adj[eid]), ids.index(eid)))
    for start in roots:
        if start in visited:
            continue
        frontier = [start]
        visited.add(start)
        depth = 0
        while frontier:
            if depth >= len(layers):
                layers.append([])
            layers[depth].extend(frontier)
            nxt: List[str] = []
            for node in frontier:
                for nb in adj[node]:
                    if nb not in visited:
                        visited.add(nb)
                        nxt.append(nb)
            frontier = sorted(nxt, key=lambda eid: ids.index(eid))
            depth += 1
    return layers


def hierarchical(graph: Graph) -> Dict[str, Tuple[float, float]]:
    ids = _ordered_ids(graph)
    if not ids:
        return {}
    adj = _adjacency(graph)
    layers = _bfs_layers(adj, ids)
    positions: Dict[str, Tuple[float, float]] = {}
    for depth, layer in enumerate(layers):
        n = len(layer)
        for i, eid in enumerate(layer):
            x = (i - (n - 1) / 2.0) * _SPACING_X
            y = depth * _SPACING_Y
            positions[eid] = (x, y)
    return positions


def radial(graph: Graph) -> Dict[str, Tuple[float, float]]:
    ids = _ordered_ids(graph)
    if not ids:
        return {}
    adj = _adjacency(graph)
    root = _root(graph, ids)
    # BFS distance from root.
    dist: Dict[str, int] = {root: 0}
    q = deque([root])
    while q:
        node = q.popleft()
        for nb in adj[node]:
            if nb not in dist:
                dist[nb] = dist[node] + 1
                q.append(nb)
    # Disconnected nodes go on an outer ring.
    max_d = max(dist.values()) if dist else 0
    for eid in ids:
        dist.setdefault(eid, max_d + 1)

    rings: Dict[int, List[str]] = {}
    for eid in ids:
        rings.setdefault(dist[eid], []).append(eid)

    positions: Dict[str, Tuple[float, float]] = {}
    for ring, members in rings.items():
        if ring == 0:
            positions[members[0]] = (0.0, 0.0)
            members = members[1:]
            if not members:
                continue
        radius = _RING_STEP * max(ring, 1)
        n = len(members)
        for i, eid in enumerate(sorted(members, key=lambda e: ids.index(e))):
            angle = 2 * math.pi * i / n
            positions[eid] = (radius * math.cos(angle), radius * math.sin(angle))
    return positions


def force(graph: Graph, iterations: int = 60) -> Dict[str, Tuple[float, float]]:
    """Deterministic Fruchterman-Reingold force-directed layout."""

    ids = _ordered_ids(graph)
    n = len(ids)
    if n == 0:
        return {}
    if n == 1:
        return {ids[0]: (0.0, 0.0)}

    adj = _adjacency(graph)
    area = (n * _SPACING_X) ** 2
    k = math.sqrt(area / n)  # ideal edge length

    # Deterministic initial placement on a circle (no RNG).
    pos: Dict[str, List[float]] = {}
    for i, eid in enumerate(ids):
        angle = 2 * math.pi * i / n
        pos[eid] = [k * math.cos(angle), k * math.sin(angle)]

    temp = k
    cool = k / (iterations + 1)
    for _ in range(iterations):
        disp: Dict[str, List[float]] = {eid: [0.0, 0.0] for eid in ids}
        # Repulsive forces between all pairs.
        for i in range(n):
            for j in range(i + 1, n):
                a, b = ids[i], ids[j]
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                dist = math.hypot(dx, dy) or 0.01
                rep = (k * k) / dist
                ux, uy = dx / dist, dy / dist
                disp[a][0] += ux * rep
                disp[a][1] += uy * rep
                disp[b][0] -= ux * rep
                disp[b][1] -= uy * rep
        # Attractive forces along edges.
        seen_edges = set()
        for src, nbrs in adj.items():
            for tgt in nbrs:
                edge = tuple(sorted((src, tgt)))
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                dx = pos[src][0] - pos[tgt][0]
                dy = pos[src][1] - pos[tgt][1]
                dist = math.hypot(dx, dy) or 0.01
                att = (dist * dist) / k
                ux, uy = dx / dist, dy / dist
                disp[src][0] -= ux * att
                disp[src][1] -= uy * att
                disp[tgt][0] += ux * att
                disp[tgt][1] += uy * att
        # Apply displacement capped by temperature.
        for eid in ids:
            dx, dy = disp[eid]
            d = math.hypot(dx, dy) or 0.01
            pos[eid][0] += (dx / d) * min(d, temp)
            pos[eid][1] += (dy / d) * min(d, temp)
        temp = max(temp - cool, 0.0)

    return {eid: (round(p[0], 2), round(p[1], 2)) for eid, p in pos.items()}


_ALGORITHMS = {
    "hierarchical": hierarchical,
    "radial": radial,
    "force": force,
}


def apply_layout(graph: Graph, algorithm: str) -> int:
    """Compute ``algorithm`` layout and assign positions onto the graph.

    Returns the number of entities positioned. Raises ``ValueError`` for an
    unknown algorithm name.
    """

    algo = _ALGORITHMS.get(algorithm)
    if algo is None:
        raise ValueError(
            f"Unknown layout '{algorithm}'. Choose one of: {', '.join(LAYOUTS)}."
        )
    positions = algo(graph)
    return graph.set_positions(positions)
