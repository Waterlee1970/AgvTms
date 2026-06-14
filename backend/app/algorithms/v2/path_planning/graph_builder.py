"""
Graph representation and utilities for V2 path planning engine.

Provides a high-performance graph structure optimized for:
- Bidirectional A* search with heap priority queue
- Time-window constraint queries
- Distance caching for repeated lookups
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import math


@dataclass
class GraphNode:
    """Lightweight graph node for pathfinding."""
    id: str
    x: float
    y: float
    type: str = "path"  # pickup/dropoff/charge/cross/path/conveyor_in/conveyor_out
    capacity: int = 1    # max AGVs simultaneously at this node


@dataclass
class GraphEdge:
    """Lightweight graph edge for pathfinding."""
    from_node: str
    to_node: str
    distance: float       # meters
    speed_limit: float = 1.5     # m/s (default AGV speed)
    is_conveyor: bool = False
    congestion_factor: float = 1.0  # multiplier for effective travel time
    direction: str = "bidirectional"  # bidirectional/forward/backward

    @property
    def travel_time(self) -> float:
        """Effective travel time including congestion."""
        return self.distance / max(self.speed_limit, 0.01) * self.congestion_factor


class PathGraph:
    """
    High-performance graph for path planning queries.

    Uses adjacency list with precomputed edge weights.
    Supports both directed and undirected edge traversal.
    """

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.outgoing: Dict[str, List[Tuple[str, GraphEdge]]] = {}  # node -> [(neighbor, edge)]
        self.edge_map: Dict[Tuple[str, str], GraphEdge] = {}        # (from, to) -> edge
        self._distance_cache: Dict[Tuple[str, str], float] = {}     # heuristic cache

    def add_node(self, node: GraphNode) -> None:
        """Register a node in the graph."""
        self.nodes[node.id] = node
        if node.id not in self.outgoing:
            self.outgoing[node.id] = []

    def add_edge(self, edge: GraphEdge) -> None:
        """Register an edge with direction-aware traversal."""
        if edge.from_node not in self.outgoing:
            self.outgoing[edge.from_node] = []
        self.outgoing[edge.from_node].append((edge.to_node, edge))
        self.edge_map[(edge.from_node, edge.to_node)] = edge

        # Bidirectional or backward-only edges add reverse traversal
        if edge.direction in ("bidirectional", "backward"):
            if edge.to_node not in self.outgoing:
                self.outgoing[edge.to_node] = []
            self.outgoing[edge.to_node].append((edge.from_node, edge))
            self.edge_map[(edge.to_node, edge.from_node)] = edge

    def get_edge(self, frm: str, to: str) -> Optional[GraphEdge]:
        """Get edge between two nodes (or None)."""
        return self.edge_map.get((frm, to))

    def get_neighbors(self, node_id: str) -> List[Tuple[str, GraphEdge]]:
        """Get all outgoing neighbors with their edges."""
        return self.outgoing.get(node_id, [])

    def heuristic(self, a: str, b: str) -> float:
        """
        Euclidean distance heuristic (admissible for A*).
        Cached for repeated queries between same node pairs.
        """
        cache_key = (a, b)
        if cache_key in self._distance_cache:
            return self._distance_cache[cache_key]

        node_a = self.nodes.get(a)
        node_b = self.nodes.get(b)
        if node_a is None or node_b is None:
            return float('inf')

        h = math.sqrt((node_a.x - node_b.x) ** 2 + (node_a.y - node_b.y) ** 2)
        # Cache for efficiency
        self._distance_cache[cache_key] = h
        self._distance_cache[(b, a)] = h
        return h

    def invalidate_cache(self) -> None:
        """Clear distance caches (call after graph topology changes)."""
        self._distance_cache.clear()

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edge_map)

    def build_from_legacy(
        self,
        nodes: List,
        edges: List,
    ) -> "PathGraph":
        """
        Build PathGraph from legacy MapNode/MapEdge models.

        Args:
            nodes: List of MapNode from schemas
            edges: List of MapEdge from schemas

        Returns:
            Self for chaining
        """
        for n in nodes:
            self.add_node(GraphNode(
                id=n.id, x=n.x, y=n.y,
                type=n.type.value if hasattr(n.type, 'value') else str(n.type),
                capacity=getattr(n, 'capacity', 1),
            ))

        for e in edges:
            self.add_edge(GraphEdge(
                from_node=e.from_node,
                to_node=e.to_node,
                distance=e.distance,
                speed_limit=getattr(e, 'speed_limit', 1.5),
                is_conveyor=getattr(e, 'is_conveyor', False),
                congestion_factor=getattr(e, 'congestion_factor', 1.0),
                direction=e.direction.value if hasattr(e.direction, 'value') else str(e.direction),
            ))

        return self

    def find_nearest_node(self, x: float, y: float) -> Optional[str]:
        """Find the graph node closest to (x, y) coordinates."""
        min_dist = float('inf')
        nearest = None
        for node_id, node in self.nodes.items():
            d = math.sqrt((node.x - x) ** 2 + (node.y - y) ** 2)
            if d < min_dist:
                min_dist = d
                nearest = node_id
        return nearest

    def subgraph(
        self,
        node_ids: set,
        include_edges: bool = True,
    ) -> "PathGraph":
        """
        Extract a subgraph containing only specified nodes.
        Useful for zone-based path planning.
        """
        sub = PathGraph()
        for nid in node_ids:
            if nid in self.nodes:
                sub.add_node(self.nodes[nid])

        if include_edges:
            for nid in node_ids:
                for neighbor, edge in self.get_neighbors(nid):
                    if neighbor in node_ids:
                        sub.add_edge(edge)

        return sub

    def __repr__(self) -> str:
        return f"PathGraph(nodes={len(self.nodes)}, edges={len(self.edge_map)})"
