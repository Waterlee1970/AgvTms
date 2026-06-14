"""
Map Management Service.

Handles CRUD operations for factory floor maps including nodes and edges.
Maintains in-memory storage with version tracking.
"""

from __future__ import annotations

import copy
import logging
from typing import Dict, List, Optional

from ..models.schemas import MapEdge, MapGraph, MapNode

logger = logging.getLogger(__name__)


class MapService:
    """Service for managing factory layout maps."""

    def __init__(self):
        self._graph = MapGraph(
            name="Default Layout",
            version=1,
        )
        self._init_demo_map()

    def _init_demo_map(self):
        """Initialize a demo factory layout with sample nodes and edges."""
        # Create a grid-based factory layout
        nodes = []
        edges = []

        # Row 0: Pickup stations (left) and dropoff stations (right)
        for i in range(5):
            nodes.append(MapNode(
                id=f"N_P{i:02d}",
                name=f"取货点{i+1}",
                x=float(i * 10),
                y=0.0,
                type="pickup",
            ))
            nodes.append(MapNode(
                id=f"N_D{i:02d}",
                name=f"卸货点{i+1}",
                x=float(i * 10),
                y=50.0,
                type="dropoff",
            ))

        # Row 1: Path nodes
        for i in range(5):
            nodes.append(MapNode(
                id=f"N_M1_{i:02d}",
                name=f"路径1-{i+1}",
                x=float(i * 10),
                y=12.5,
                type="path",
            ))

        # Row 2: Cross nodes (intersections with conveyor)
        for i in range(5):
            nodes.append(MapNode(
                id=f"N_C_{i:02d}",
                name=f"交叉口{i+1}",
                x=float(i * 10),
                y=25.0,
                type="cross",
            ))

        # Row 3: Path nodes
        for i in range(5):
            nodes.append(MapNode(
                id=f"N_M2_{i:02d}",
                name=f"路径2-{i+1}",
                x=float(i * 10),
                y=37.5,
                type="path",
            ))

        # Conveyor entry/exit nodes
        nodes.append(MapNode(id="N_CE", name="输送线入口", x=-5.0, y=25.0, type="conveyor_in"))
        nodes.append(MapNode(id="N_CX", name="输送线出口", x=45.0, y=25.0, type="conveyor_out"))

        # Charge stations
        nodes.append(MapNode(id="N_CH1", name="充电站1", x=5.0, y=-5.0, type="charge"))
        nodes.append(MapNode(id="N_CH2", name="充电站2", x=35.0, y=-5.0, type="charge"))

        # Build edges
        # Vertical edges (between rows)
        for i in range(5):
            edges.append(MapEdge(
                from_node=f"N_P{i:02d}", to_node=f"N_M1_{i:02d}",
                distance=12.5, direction="bidirectional",
            ))
            edges.append(MapEdge(
                from_node=f"N_M1_{i:02d}", to_node=f"N_C_{i:02d}",
                distance=12.5, direction="bidirectional",
            ))
            edges.append(MapEdge(
                from_node=f"N_C_{i:02d}", to_node=f"N_M2_{i:02d}",
                distance=12.5, direction="bidirectional",
            ))
            edges.append(MapEdge(
                from_node=f"N_M2_{i:02d}", to_node=f"N_D{i:02d}",
                distance=12.5, direction="bidirectional",
            ))

        # Horizontal edges (between columns)
        for i in range(4):
            for row_nodes in [
                (f"N_P{i:02d}", f"N_P{i+1:02d}"),
                (f"N_M1_{i:02d}", f"N_M1_{i+1:02d}"),
                (f"N_M2_{i:02d}", f"N_M2_{i+1:02d}"),
                (f"N_D{i:02d}", f"N_D{i+1:02d}"),
            ]:
                edges.append(MapEdge(
                    from_node=row_nodes[0], to_node=row_nodes[1],
                    distance=10.0, direction="bidirectional",
                ))

        # Horizontal edges at cross row (with conveyor)
        for i in range(4):
            edges.append(MapEdge(
                from_node=f"N_C_{i:02d}", to_node=f"N_C_{i+1:02d}",
                distance=10.0, direction="bidirectional",
            ))

        # Conveyor line (main horizontal conveyor)
        edges.append(MapEdge(
            from_node="N_CE", to_node="N_C_00",
            distance=5.0, direction="forward", is_conveyor=True, speed_limit=0.5,
        ))
        for i in range(4):
            edges.append(MapEdge(
                from_node=f"N_C_{i:02d}", to_node=f"N_C_{i+1:02d}",
                distance=10.0, direction="forward", is_conveyor=True, speed_limit=0.5,
            ))
        edges.append(MapEdge(
            from_node="N_C_04", to_node="N_CX",
            distance=5.0, direction="forward", is_conveyor=True, speed_limit=0.5,
        ))

        # Charge station connections
        edges.append(MapEdge(
            from_node="N_CH1", to_node="N_P00",
            distance=5.0, direction="bidirectional",
        ))
        edges.append(MapEdge(
            from_node="N_CH2", to_node="N_P03",
            distance=5.0, direction="bidirectional",
        ))

        self._graph.nodes = nodes
        self._graph.edges = edges

    # ---- CRUD Operations ----

    def get_graph(self) -> MapGraph:
        """Get the current map graph."""
        return copy.deepcopy(self._graph)

    def get_nodes(self) -> List[MapNode]:
        return copy.deepcopy(self._graph.nodes)

    def get_edges(self) -> List[MapEdge]:
        return copy.deepcopy(self._graph.edges)

    def add_node(self, node: MapNode) -> MapNode:
        if any(n.id == node.id for n in self._graph.nodes):
            raise ValueError(f"Node {node.id} already exists")
        self._graph.nodes.append(node)
        self._graph.version += 1
        logger.info(f"Added node: {node.id}")
        return node

    def update_node(self, node_id: str, updated: MapNode) -> Optional[MapNode]:
        for i, n in enumerate(self._graph.nodes):
            if n.id == node_id:
                self._graph.nodes[i] = updated
                self._graph.version += 1
                logger.info(f"Updated node: {node_id}")
                return updated
        return None

    def delete_node(self, node_id: str) -> bool:
        initial_len = len(self._graph.nodes)
        self._graph.nodes = [n for n in self._graph.nodes if n.id != node_id]
        # Also remove connected edges
        self._graph.edges = [
            e for e in self._graph.edges
            if e.from_node != node_id and e.to_node != node_id
        ]
        if len(self._graph.nodes) < initial_len:
            self._graph.version += 1
            logger.info(f"Deleted node: {node_id}")
            return True
        return False

    def add_edge(self, edge: MapEdge) -> MapEdge:
        self._graph.edges.append(edge)
        self._graph.version += 1
        return edge

    def delete_edge(self, edge_id: str) -> bool:
        initial_len = len(self._graph.edges)
        self._graph.edges = [e for e in self._graph.edges if e.id != edge_id]
        if len(self._graph.edges) < initial_len:
            self._graph.version += 1
            return True
        return False


# Singleton instance
map_service = MapService()
