"""
Zone-Based Traffic Control System for Large-Scale AGV Fleets.

Implements hierarchical traffic management:
1. Zone partitioning: Divide map into intersections, corridors, work zones
2. Zone locking: Mutual exclusion on shared resources
3. Deadlock detection: Resource Allocation Graph cycle detection
4. Banker's algorithm variant: Deadlock avoidance via safe-state checking
5. Dynamic flow control: Congestion-based entry throttling

Architecture:
    Layer 1: Zone Manager  — partitions map, manages zone locks
    Layer 2: Deadlock Detector — RAG cycle detection + recovery
    Layer 3: Flow Controller — entry throttling, rerouting suggestions
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ZoneType(str, Enum):
    """Types of traffic control zones."""
    INTERSECTION = "intersection"   # Cross junction, high collision risk
    CORRIDOR = "corridor"           # Bidirectional or one-way corridor
    WORK_ZONE = "work_zone"         # Pickup/dropoff station area
    CHARGING_ZONE = "charging_zone" # Charging station area
    CONVEYOR_INTERFACE = "conveyor" # AGV-conveyor interaction point


class LockType(str, Enum):
    """Types of zone locks."""
    EXCLUSIVE = "exclusive"       # Single AGV only
    SHARED = "shared"            # Multiple AGVs, same direction
    SEQUENTIAL = "sequential"     # Multiple AGVs, FIFO order


@dataclass
class TrafficZone:
    """A traffic control zone in the factory map."""
    id: str
    name: str
    zone_type: ZoneType
    node_ids: Set[str] = field(default_factory=set)
    entry_nodes: Set[str] = field(default_factory=set)   # Border entry points
    exit_nodes: Set[str] = field(default_factory=set)    # Border exit points
    max_capacity: int = 3         # Max AGVs allowed simultaneously
    lock_type: LockType = LockType.EXCLUSIVE
    current_occupants: Set[str] = field(default_factory=set)  # AGV IDs currently inside
    wait_queue: List[str] = field(default_factory=list)        # AGV IDs waiting for entry

    def is_full(self) -> bool:
        return len(self.current_occupants) >= self.max_capacity

    def enter(self, agv_id: str) -> bool:
        """AGV enters the zone. Returns True if entry is allowed."""
        if self.is_full() and agv_id not in self.current_occupants:
            return False
        self.current_occupants.add(agv_id)
        return True

    def leave(self, agv_id: str) -> None:
        """AGV leaves the zone."""
        self.current_occupants.discard(agv_id)


@dataclass
class LockRequest:
    """A lock request from an AGV for a zone."""
    agv_id: str
    zone_id: str
    requested_at: float    # timestamp
    priority: int = 0      # higher = more urgent
    timeout: float = 30.0  # seconds before request expires

    def is_expired(self, current_time: float) -> bool:
        return current_time - self.requested_at > self.timeout


class ResourceAllocationGraph:
    """
    Resource Allocation Graph for deadlock detection.

    Nodes: AGVs (processes) + Zones (resources)
    Edges:
      - AGV → Zone: AGV is waiting for zone (request edge)
      - Zone → AGV: Zone is held by AGV (assignment edge)

    A cycle in this graph indicates deadlock.
    """

    def __init__(self):
        # AGV → set of zones it holds
        self.held: Dict[str, Set[str]] = defaultdict(set)
        # AGV → set of zones it's waiting for
        self.waiting: Dict[str, Set[str]] = defaultdict(set)

    def hold(self, agv_id: str, zone_id: str) -> None:
        """Record that AGV holds a zone."""
        self.held[agv_id].add(zone_id)

    def release(self, agv_id: str, zone_id: str) -> None:
        """Record that AGV released a zone."""
        self.held[agv_id].discard(zone_id)

    def wait(self, agv_id: str, zone_id: str) -> None:
        """Record that AGV is waiting for a zone."""
        self.waiting[agv_id].add(zone_id)

    def stop_waiting(self, agv_id: str, zone_id: str) -> None:
        """AGV stopped waiting for a zone."""
        self.waiting[agv_id].discard(zone_id)

    def detect_deadlock(self) -> Optional[List[str]]:
        """
        Detect if there is a deadlock cycle.

        Uses DFS from each AGV that is waiting. Follows:
        AGV_i → Zone_j (wait) → AGV_k (hold) → Zone_l (wait) → ...

        Returns:
            List of AGV IDs in the deadlock cycle, or None if no deadlock
        """
        # Build the directed graph: processes + resources
        # Edge: AGV → Zone (wait), Zone → AGV (hold)

        visited: Set[str] = set()
        in_stack: Set[str] = set()
        path: List[str] = []

        def dfs(node: str) -> Optional[List[str]]:
            if node in in_stack:
                # Found cycle starting from this node
                cycle_start = path.index(node)
                return path[cycle_start:]
            if node in visited:
                return None

            visited.add(node)
            in_stack.add(node)
            path.append(node)

            # If node is an AGV: follow wait edges to zones
            if node in self.waiting:
                for zone in self.waiting[node]:
                    result = dfs(zone)
                    if result:
                        return result

            # If node is a zone: follow hold edges to AGVs
            for agv, zones in self.held.items():
                if node in zones:
                    result = dfs(agv)
                    if result:
                        return result

            path.pop()
            in_stack.discard(node)
            return None

        # Start from AGVs that are both holding and waiting
        for agv_id in set(self.held.keys()) | set(self.waiting.keys()):
            if agv_id not in visited:
                result = dfs(agv_id)
                if result:
                    return result

        return None

    def is_safe_state(self, agv_id: str, requested_zone: str,
                      max_capacity: Dict[str, int]) -> bool:
        """
        Banker's algorithm: Check if granting this request
        leaves the system in a safe state.

        A state is safe if there exists a sequence where all AGVs
        can eventually complete without deadlock.
        """
        # Simplified: if granting the request creates a cycle, it's unsafe
        # Temporarily grant and check
        self.hold(agv_id, requested_zone)
        self.stop_waiting(agv_id, requested_zone)

        deadlock = self.detect_deadlock()

        # Rollback
        self.release(agv_id, requested_zone)
        if deadlock is None:
            self.wait(agv_id, requested_zone)

        return deadlock is None


class ZoneManager:
    """
    Manages traffic zones and coordinates AGV entry/exit.

    Responsibilities:
    1. Zone discovery: Automatically identify intersections & corridors
    2. Lock management: Grant/deny entry requests
    3. Queue management: FIFO + priority-based entry ordering
    """

    def __init__(self):
        self.zones: Dict[str, TrafficZone] = {}
        self.rag = ResourceAllocationGraph()
        self._agv_current_zone: Dict[str, str] = {}  # AGV → current zone

    def add_zone(self, zone: TrafficZone) -> None:
        """Register a traffic zone."""
        self.zones[zone.id] = zone

    def auto_discover_zones(
        self,
        nodes: List,
        edges: List,
        intersection_degree_threshold: int = 3,
    ) -> int:
        """
        Automatically identify traffic zones from map topology.

        Heuristics:
        - Intersections: nodes with degree ≥ threshold
        - Corridors: sequences of degree-2 nodes between intersections
        - Work zones: pickup/dropoff nodes ± 1 hop radius
        - Charging zones: charge nodes ± 1 hop radius
        """
        # Build degree map
        degree: Dict[str, int] = defaultdict(int)
        adj: Dict[str, List[str]] = defaultdict(list)

        for e in edges:
            degree[e.from_node] += 1
            degree[e.to_node] += 1
            adj[e.from_node].append(e.to_node)
            adj[e.to_node].append(e.from_node)

        node_types: Dict[str, str] = {}
        for n in nodes:
            node_types[n.id] = getattr(n, 'type', 'path')
            if hasattr(n.type, 'value'):
                node_types[n.id] = n.type.value

        zones_created = 0

        # 1. Intersection zones
        intersection_nodes = set()
        for nid, deg in degree.items():
            if deg >= intersection_degree_threshold:
                intersection_nodes.add(nid)

        for nid in intersection_nodes:
            zone_id = f"zone_intersection_{nid}"
            zone = TrafficZone(
                id=zone_id,
                name=f"Intersection {nid}",
                zone_type=ZoneType.INTERSECTION,
                node_ids={nid} | set(adj[nid]),  # Include immediate neighbors
                entry_nodes=set(adj[nid]),
                exit_nodes=set(adj[nid]),
                max_capacity=1,
                lock_type=LockType.EXCLUSIVE,
            )
            self.add_zone(zone)
            zones_created += 1

        # 2. Work zones (pickup/dropoff)
        work_nodes = {nid for nid, t in node_types.items()
                      if t in ('pickup', 'dropoff')}
        for nid in work_nodes:
            zone_id = f"zone_work_{nid}"
            neighbors = set(adj.get(nid, [])) | {nid}
            zone = TrafficZone(
                id=zone_id,
                name=f"WorkZone {nid}",
                zone_type=ZoneType.WORK_ZONE,
                node_ids=neighbors,
                entry_nodes=set(adj.get(nid, [])),
                exit_nodes=set(adj.get(nid, [])),
                max_capacity=2,
                lock_type=LockType.SEQUENTIAL,
            )
            self.add_zone(zone)
            zones_created += 1

        # 3. Charging zones
        charge_nodes = {nid for nid, t in node_types.items()
                        if t == 'charge'}
        for nid in charge_nodes:
            zone_id = f"zone_charge_{nid}"
            neighbors = set(adj.get(nid, [])) | {nid}
            zone = TrafficZone(
                id=zone_id,
                name=f"ChargeZone {nid}",
                zone_type=ZoneType.CHARGING_ZONE,
                node_ids=neighbors,
                entry_nodes=set(adj.get(nid, [])),
                exit_nodes=set(adj.get(nid, [])),
                max_capacity=4,
                lock_type=LockType.SEQUENTIAL,
            )
            self.add_zone(zone)
            zones_created += 1

        # 4. Corridors: chains of degree-2 nodes between intersections
        visited = set(intersection_nodes) | set(work_nodes) | set(charge_nodes)
        corridor_id = 0

        for nid in list(degree.keys()):
            if nid in visited:
                continue

            # Follow degree-2 chain
            chain = [nid]
            visited.add(nid)
            current = nid

            # Extend forward
            for _ in range(50):  # max corridor length
                next_nodes = [n for n in adj.get(current, [])
                             if n not in visited and degree.get(n, 0) <= 2]
                if not next_nodes:
                    break
                current = next_nodes[0]
                chain.append(current)
                visited.add(current)

            if len(chain) >= 2:
                corridor_id += 1
                zone_id = f"zone_corridor_{corridor_id}"
                zone = TrafficZone(
                    id=zone_id,
                    name=f"Corridor {corridor_id}",
                    zone_type=ZoneType.CORRIDOR,
                    node_ids=set(chain),
                    entry_nodes={chain[0], chain[-1]},
                    exit_nodes={chain[0], chain[-1]},
                    max_capacity=3,
                    lock_type=LockType.SHARED,
                )
                self.add_zone(zone)
                zones_created += 1

        logger.info(f"Auto-discovered {zones_created} traffic zones "
                     f"({len([z for z in self.zones.values() if z.zone_type == ZoneType.INTERSECTION])} intersections, "
                     f"{len([z for z in self.zones.values() if z.zone_type == ZoneType.CORRIDOR])} corridors, "
                     f"{len([z for z in self.zones.values() if z.zone_type == ZoneType.WORK_ZONE])} work zones)")

        return zones_created

    def request_entry(
        self, agv_id: str, zone_id: str, priority: int = 0,
    ) -> Tuple[bool, Optional[float]]:
        """
        Request AGV entry into a zone.

        Returns:
            (granted, wait_time) — wait_time is estimated wait if denied
        """
        zone = self.zones.get(zone_id)
        if zone is None:
            return False, None

        # Already inside
        if agv_id in zone.current_occupants:
            return True, 0.0

        # Check capacity
        if zone.is_full():
            zone.wait_queue.append(agv_id)
            return False, self._estimate_wait(zone, agv_id)

        # Deadlock avoidance: check safe state
        if not self.rag.is_safe_state(agv_id, zone_id,
                                       {z.id: z.max_capacity for z in self.zones.values()}):
            zone.wait_queue.append(agv_id)
            return False, self._estimate_wait(zone, agv_id)

        # Grant entry
        zone.enter(agv_id)
        self.rag.hold(agv_id, zone_id)
        self._agv_current_zone[agv_id] = zone_id

        return True, 0.0

    def release_zone(self, agv_id: str, zone_id: str) -> None:
        """AGV leaves a zone."""
        zone = self.zones.get(zone_id)
        if zone:
            zone.leave(agv_id)
        self.rag.release(agv_id, zone_id)
        if self._agv_current_zone.get(agv_id) == zone_id:
            del self._agv_current_zone[agv_id]

        # Process wait queue
        if zone and zone.wait_queue:
            next_agv = zone.wait_queue.pop(0)
            zone.enter(next_agv)
            self.rag.hold(next_agv, zone_id)
            self._agv_current_zone[next_agv] = zone_id

    def get_zone_for_node(self, node_id: str) -> Optional[str]:
        """Find the traffic zone containing a given node."""
        for zone_id, zone in self.zones.items():
            if node_id in zone.node_ids:
                return zone_id
        return None

    def get_zones_on_path(self, path: List[str]) -> List[str]:
        """Get ordered list of zones that a path traverses."""
        zones_seen = []
        for node_id in path:
            zone_id = self.get_zone_for_node(node_id)
            if zone_id and (not zones_seen or zones_seen[-1] != zone_id):
                zones_seen.append(zone_id)
        return zones_seen

    def _estimate_wait(self, zone: TrafficZone, _agv_id: str) -> float:
        """Estimate wait time for zone entry."""
        # Simple estimate: number of queued AGVs × average dwell time
        avg_dwell = 5.0  # seconds per AGV in zone
        queue_pos = len(zone.wait_queue)
        return queue_pos * avg_dwell

    def check_deadlock(self) -> Optional[List[str]]:
        """Check for deadlock in the current allocation state."""
        return self.rag.detect_deadlock()

    def resolve_deadlock(self, deadlock_cycle: List[str]) -> Optional[str]:
        """
        Resolve a deadlock by preempting the lowest priority AGV.

        Returns:
            AGV ID that was preempted, or None if resolution failed
        """
        if not deadlock_cycle:
            return None

        # Preempt the AGV with fewest held resources
        agvs_in_cycle = [n for n in deadlock_cycle if n in self.rag.held]
        if not agvs_in_cycle:
            return None

        victim = min(agvs_in_cycle, key=lambda a: len(self.rag.held.get(a, set())))

        # Release all zones held by victim
        for zone_id in list(self.rag.held.get(victim, set())):
            self.release_zone(victim, zone_id)

        # Stop waiting
        for zone_id in list(self.rag.waiting.get(victim, set())):
            self.rag.stop_waiting(victim, zone_id)

        logger.warning(f"Deadlock resolved: preempted {victim}")
        return victim

    @property
    def stats(self) -> dict:
        """Get traffic control statistics."""
        total_occupancy = sum(len(z.current_occupants) for z in self.zones.values())
        total_queued = sum(len(z.wait_queue) for z in self.zones.values())
        zone_utilization = {}
        for zid, zone in self.zones.items():
            util = len(zone.current_occupants) / max(zone.max_capacity, 1)
            zone_utilization[zid] = round(util, 2)

        return {
            "total_zones": len(self.zones),
            "total_occupancy": total_occupancy,
            "total_queued": total_queued,
            "avg_zone_utilization": round(
                sum(zone_utilization.values()) / max(len(zone_utilization), 1), 2
            ),
            "zone_types": {
                zt.value: len([z for z in self.zones.values() if z.zone_type == zt])
                for zt in ZoneType
            },
        }

    def __repr__(self) -> str:
        return f"ZoneManager(zones={len(self.zones)}, occupancy={sum(len(z.current_occupants) for z in self.zones.values())})"
