"""
World Model — Enhanced Traffic Control.

Implements a world-model-based traffic management system (对标RCS-2000世界模型):
- Intersection management with priority arbitration
- Dynamic lane assignment (动态路权)
- Corridor capacity management
- Deadlock prevention with resource allocation graph

Architecture:
    Road Network → Zone Classification
      ├── Intersection Zone (路口区域)
      ├── One-way Corridor (单行通道)
      └── Work Zone (工作区域)
    Control Layer → Resource Management
      ├── Mutual Exclusion Lock (互斥锁)
      ├── Sequential Lock (顺序锁)
      └── Priority Arbitration (优先级仲裁)
    Scheduling Layer → Time Coordination
      ├── Time Window Assignment
      ├── Waiting Queue Management
      └── Detour Decision
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ZoneType(str, Enum):
    INTERSECTION = "intersection"     # 路口
    CORRIDOR = "corridor"            # 通道
    WORK_ZONE = "work_zone"          # 工作区
    CHARGING_ZONE = "charging"       # 充电区
    BUFFER_ZONE = "buffer"           # 缓冲区


class LockType(str, Enum):
    MUTEX = "mutex"           # 互斥 (同一时刻仅1台AGV)
    SEQUENTIAL = "sequential"  # 顺序 (按申请顺序通过)
    SHARED = "shared"         # 共享 (多台可同时但不可交错)


@dataclass
class Zone:
    """A traffic zone in the world model."""
    zone_id: str
    zone_type: ZoneType
    node_ids: List[str]            # Nodes belonging to this zone
    max_capacity: int = 1          # Max concurrent AGVs
    lock_type: LockType = LockType.MUTEX
    current_occupants: Set[str] = field(default_factory=set)  # AGV IDs
    wait_queue: List[Tuple[str, int, float]] = field(default_factory=list)  # (agv_id, priority, timestamp)
    pass_through_time: float = 5.0  # Average time to pass through


@dataclass
class Corridor:
    """A corridor segment between two zones."""
    corridor_id: str
    from_zone: str
    to_zone: str
    direction: str = "bidirectional"  # bidirectional, forward, backward
    max_agvs: int = 2
    current_agvs: Set[str] = field(default_factory=set)
    narrow: bool = False  # Narrow corridor (width-limited)


class WorldModel:
    """
    World model for traffic management.

    Maintains a spatial model of the factory floor divided into zones
    and corridors, with real-time occupancy tracking and traffic rules.
    """

    def __init__(self):
        self._zones: Dict[str, Zone] = {}
        self._corridors: Dict[str, Corridor] = {}
        self._node_to_zone: Dict[str, str] = {}  # node_id → zone_id
        self._deadlock_graph: Dict[str, Set[str]] = {}  # agv_id → {waiting_for_agv_ids}

    # ---- Zone Management ----

    def add_zone(self, zone: Zone):
        """Register a traffic zone."""
        self._zones[zone.zone_id] = zone
        for node_id in zone.node_ids:
            self._node_to_zone[node_id] = zone.zone_id
        logger.info("Added zone %s: %s (%d nodes)", zone.zone_id, zone.zone_type, len(zone.node_ids))

    def add_corridor(self, corridor: Corridor):
        """Register a corridor between zones."""
        self._corridors[corridor.corridor_id] = corridor

    def get_zone_for_node(self, node_id: str) -> Optional[Zone]:
        """Get the zone containing a node."""
        zone_id = self._node_to_zone.get(node_id)
        return self._zones.get(zone_id) if zone_id else None

    def auto_discover_zones(self, nodes: List[Any], edges: List[Any]):
        """
        Auto-discover traffic zones from map topology.

        Identifies:
        - Intersections: nodes with >2 connections
        - Corridors: chains of degree-2 nodes
        - Work zones: nodes with type pickup/dropoff
        """
        # Build adjacency
        adjacency: Dict[str, List[str]] = {n.id: [] for n in nodes}
        for e in edges:
            if e.from_node in adjacency:
                adjacency[e.from_node].append(e.to_node)
            if e.direction == "bidirectional" and e.to_node in adjacency:
                adjacency[e.to_node].append(e.from_node)

        zone_counter = 0

        # Find intersections (degree > 2)
        for node in nodes:
            degree = len(adjacency.get(node.id, []))
            if degree > 2:
                zone_id = f"zone_intersection_{zone_counter}"
                zone_counter += 1
                self.add_zone(Zone(
                    zone_id=zone_id,
                    zone_type=ZoneType.INTERSECTION,
                    node_ids=[node.id],
                    max_capacity=1,
                    lock_type=LockType.MUTEX,
                    pass_through_time=3.0,
                ))

        # Find work zones (pickup/dropoff nodes)
        for node in nodes:
            if node.type in ("pickup", "dropoff"):
                zone_id = f"zone_work_{zone_counter}"
                zone_counter += 1
                self.add_zone(Zone(
                    zone_id=zone_id,
                    zone_type=ZoneType.WORK_ZONE,
                    node_ids=[node.id],
                    max_capacity=1,
                    lock_type=LockType.MUTEX,
                    pass_through_time=10.0,
                ))

        # Find charging zones
        for node in nodes:
            if node.type == "charge":
                zone_id = f"zone_charge_{zone_counter}"
                zone_counter += 1
                self.add_zone(Zone(
                    zone_id=zone_id,
                    zone_type=ZoneType.CHARGING_ZONE,
                    node_ids=[node.id],
                    max_capacity=1,
                    lock_type=LockType.MUTEX,
                    pass_through_time=120.0,  # Charging takes time
                ))

        logger.info("Auto-discovered %d zones", len(self._zones))

    # ---- Traffic Control ----

    def request_entry(self, agv_id: str, zone_id: str, priority: int = 0) -> bool:
        """
        Request entry to a zone for an AGV.

        Returns True if entry is granted immediately, False if queued.
        """
        zone = self._zones.get(zone_id)
        if not zone:
            return True  # No zone restriction

        # Already in zone?
        if agv_id in zone.current_occupants:
            return True

        # Check capacity
        if len(zone.current_occupants) < zone.max_capacity:
            zone.current_occupants.add(agv_id)
            return True

        # Queue the request
        zone.wait_queue.append((agv_id, priority, time.time()))
        zone.wait_queue.sort(key=lambda x: (-x[1], x[2]))  # Priority desc, time asc

        # Update deadlock graph
        for occupant in zone.current_occupants:
            self._deadlock_graph.setdefault(agv_id, set()).add(occupant)

        logger.debug("AGV %s queued for zone %s (priority=%d, queue=%d)",
                     agv_id, zone_id, priority, len(zone.wait_queue))
        return False

    def release_zone(self, agv_id: str, zone_id: str):
        """Release a zone after AGV exits."""
        zone = self._zones.get(zone_id)
        if not zone:
            return

        zone.current_occupants.discard(agv_id)

        # Remove from deadlock graph
        self._deadlock_graph.pop(agv_id, None)

        # Notify next in queue (in practice, would trigger an event)
        if zone.wait_queue and len(zone.current_occupants) < zone.max_capacity:
            next_agv, _, _ = zone.wait_queue.pop(0)
            zone.current_occupants.add(next_agv)
            logger.debug("AGV %s granted entry to zone %s from queue", next_agv, zone_id)

    def release_all(self, agv_id: str):
        """Release all zones held by an AGV."""
        for zone in self._zones.values():
            if agv_id in zone.current_occupants:
                self.release_zone(agv_id, zone.zone_id)
            # Remove from wait queues
            zone.wait_queue = [(a, p, t) for a, p, t in zone.wait_queue if a != agv_id]

    # ---- Deadlock Detection ----

    def detect_deadlock(self) -> Optional[List[str]]:
        """
        Detect deadlock using cycle detection in the wait-for graph.

        Returns a list of AGV IDs in the deadlock cycle, or None if no deadlock.
        """
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        path: List[str] = []

        def dfs(node: str) -> Optional[List[str]]:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self._deadlock_graph.get(node, set()):
                if neighbor not in visited:
                    result = dfs(neighbor)
                    if result:
                        return result
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    return path[cycle_start:] + [neighbor]

            path.pop()
            rec_stack.discard(node)
            return None

        for agv_id in self._deadlock_graph:
            if agv_id not in visited:
                cycle = dfs(agv_id)
                if cycle:
                    logger.warning("Deadlock detected: %s", " → ".join(cycle))
                    return cycle
        return None

    def resolve_deadlock(self, cycle: List[str]) -> str:
        """
        Resolve a deadlock by preempting the lowest-priority AGV.

        Returns the AGV ID that was preempted.
        """
        if not cycle:
            return ""

        # Preempt the last AGV in the cycle (arbitrary but deterministic)
        victim = cycle[-2] if len(cycle) > 1 else cycle[0]
        self.release_all(victim)
        logger.info("Resolved deadlock by preempting AGV %s", victim)
        return victim

    # ---- Dynamic Lane Assignment ----

    def assign_lane_direction(
        self, corridor_id: str, agv_id: str, direction: str
    ) -> bool:
        """
        Dynamically assign lane direction for a corridor.

        If corridor is bidirectional and at capacity, restrict to one direction.
        """
        corridor = self._corridors.get(corridor_id)
        if not corridor:
            return True

        if corridor.direction != "bidirectional":
            return corridor.direction == direction

        if len(corridor.current_agvs) < corridor.max_agvs:
            corridor.current_agvs.add(agv_id)
            return True

        return False

    def release_lane(self, corridor_id: str, agv_id: str):
        """Release a lane after AGV exits."""
        corridor = self._corridors.get(corridor_id)
        if corridor:
            corridor.current_agvs.discard(agv_id)

    # ---- Load Balancing ----

    def get_zone_utilization(self) -> Dict[str, float]:
        """Get utilization ratio for all zones."""
        return {
            zid: len(z.current_occupants) / max(z.max_capacity, 1)
            for zid, z in self._zones.items()
        }

    def get_congested_zones(self, threshold: float = 0.8) -> List[str]:
        """Get zones with utilization above threshold."""
        return [
            zid for zid, util in self.get_zone_utilization().items()
            if util >= threshold
        ]

    def suggest_detour(self, from_zone: str, to_zone: str) -> Optional[str]:
        """
        Suggest a detour zone if direct path is congested.

        Returns alternative zone_id or None.
        """
        congested = set(self.get_congested_zones())

        # Find corridors from from_zone
        for corridor in self._corridors.values():
            if corridor.from_zone == from_zone and corridor.to_zone not in congested:
                return corridor.to_zone
        return None

    # ---- Query ----

    def get_stats(self) -> Dict[str, Any]:
        """Get world model statistics."""
        return {
            "total_zones": len(self._zones),
            "total_corridors": len(self._corridors),
            "zones_by_type": {
                zt.value: sum(1 for z in self._zones.values() if z.zone_type == zt)
                for zt in ZoneType
            },
            "total_occupied": sum(len(z.current_occupants) for z in self._zones.values()),
            "total_queued": sum(len(z.wait_queue) for z in self._zones.values()),
            "congested_zones": len(self.get_congested_zones()),
            "avg_utilization": (
                sum(self.get_zone_utilization().values()) / max(len(self._zones), 1)
            ),
        }
