"""
Time Window Occupancy Table for multi-AGV collision avoidance.

Manages the temporal occupancy of nodes and edges in the factory map.
Each AGV reserves time intervals on path segments to prevent collisions.

Key concepts:
- Node occupancy: AGV occupies a node from arrival to departure
- Edge occupancy: AGV occupies an edge during traversal
- Time quantization: intervals stored in sorted lists for efficient query
- Reservation lifecycle: reserve → occupied → released (upon completion)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import bisect


@dataclass
class TimeInterval:
    """A reserved time interval on a resource."""
    start: float
    end: float
    agv_id: str = ""

    def overlaps(self, other: "TimeInterval") -> bool:
        """Check if two intervals overlap."""
        return self.start < other.end and other.start < self.end

    def __lt__(self, other: "TimeInterval") -> bool:
        return self.start < other.start


class TimeWindowTable:
    """
    Occupancy table for nodes and edges in time domain.

    Each resource (node or edge) maintains a sorted list of reserved
    time intervals. AGVs reserve intervals before entering and release
    them upon leaving. Queries check if a resource is free during
    a specified time window.

    Usage:
        tw = TimeWindowTable()
        tw.reserve("N001", 0.0, 5.0, "AGV1")      # Reserve node N001 for AGV1
        tw.reserve("N001->N002", 5.0, 12.0, "AGV1") # Reserve edge for AGV1 path
        tw.is_free("N001", 3.0, 4.0, "AGV2")       # False (overlaps with AGV1)
        tw.is_free("N001", 8.0, 9.0, "AGV2")       # True (after AGV1 leaves)
        tw.next_free_time("N001", 2.0)              # Returns 5.0 (after AGV1)
    """

    def __init__(self):
        # Resource key -> sorted list of time intervals
        self._occupancy: Dict[str, List[TimeInterval]] = {}
        # AGV -> list of reserved resource keys (for cleanup)
        self._agv_reservations: Dict[str, List[str]] = {}

    def reserve(
        self,
        resource_key: str,
        start_time: float,
        end_time: float,
        agv_id: str,
        force: bool = False,
    ) -> bool:
        """
        Reserve a time interval on a resource.

        Args:
            resource_key: Node ID or "from->to" for edges
            start_time: Interval start time
            end_time: Interval end time
            agv_id: AGV making the reservation
            force: If True, override existing reservations (use cautiously)

        Returns:
            True if reservation was successful
        """
        if end_time <= start_time:
            return False

        interval = TimeInterval(start=start_time, end=end_time, agv_id=agv_id)

        if resource_key not in self._occupancy:
            self._occupancy[resource_key] = []

        intervals = self._occupancy[resource_key]

        if not force:
            # Check for conflicts
            for existing in intervals:
                if existing.agv_id != agv_id and interval.overlaps(existing):
                    return False

        # Insert in sorted order
        bisect.insort(intervals, interval)

        # Track AGV reservations
        if agv_id not in self._agv_reservations:
            self._agv_reservations[agv_id] = []
        self._agv_reservations[agv_id].append(resource_key)

        return True

    def reserve_path(
        self,
        path: List[str],
        start_time: float,
        agv_id: str,
        edge_travel_times: Optional[Dict[Tuple[str, str], float]] = None,
        node_dwell_time: float = 0.5,
    ) -> Tuple[bool, float, List[Tuple[str, float, float]]]:
        """
        Reserve an entire path for an AGV.

        Args:
            path: Ordered list of node IDs
            start_time: Departure time from first node
            agv_id: AGV identifier
            edge_travel_times: Optional dict of (from, to) -> travel_time
            node_dwell_time: Time AGV spends at each node

        Returns:
            (success, end_time, [(resource_key, start, end), ...])
        """
        reservations: List[Tuple[str, float, float]] = []
        current_time = start_time

        for i, node_id in enumerate(path):
            # Reserve node occupancy
            node_arrive = current_time
            node_leave = current_time + node_dwell_time
            if not self.reserve(node_id, node_arrive, node_leave, agv_id):
                # Rollback
                self.release_agv(agv_id)
                return False, 0.0, []
            reservations.append((node_id, node_arrive, node_leave))
            current_time = node_leave

            # Reserve edge to next node
            if i < len(path) - 1:
                next_node = path[i + 1]
                edge_key = f"{node_id}->{next_node}"
                if edge_travel_times and (node_id, next_node) in edge_travel_times:
                    travel_time = edge_travel_times[(node_id, next_node)]
                else:
                    travel_time = 1.0  # Default fallback

                edge_start = current_time
                edge_end = current_time + travel_time
                if not self.reserve(edge_key, edge_start, edge_end, agv_id):
                    self.release_agv(agv_id)
                    return False, 0.0, []
                reservations.append((edge_key, edge_start, edge_end))
                current_time = edge_end

        end_time = current_time
        return True, end_time, reservations

    def is_free(
        self,
        resource_key: str,
        start_time: float,
        end_time: float,
        exclude_agv: str = "",
    ) -> bool:
        """
        Check if a resource is free during the given interval.

        Args:
            resource_key: Node ID or edge key
            start_time: Check start
            end_time: Check end
            exclude_agv: Ignore reservations from this AGV (self-check)

        Returns:
            True if resource is free
        """
        if resource_key not in self._occupancy:
            return True

        query = TimeInterval(start=start_time, end=end_time, agv_id="")

        for existing in self._occupancy[resource_key]:
            if exclude_agv and existing.agv_id == exclude_agv:
                continue
            if query.overlaps(existing):
                return False

        return True

    def next_free_time(
        self,
        resource_key: str,
        after_time: float,
        exclude_agv: str = "",
    ) -> Optional[float]:
        """
        Find the earliest time after `after_time` when the resource is free.

        Args:
            resource_key: Node ID or edge key
            after_time: Earliest possible start
            exclude_agv: Ignore this AGV's reservations

        Returns:
            Earliest free time, or None if resource is permanently free
        """
        if resource_key not in self._occupancy:
            return after_time

        candidate = after_time
        max_iterations = 100
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            found_conflict = False

            for existing in self._occupancy[resource_key]:
                if exclude_agv and existing.agv_id == exclude_agv:
                    continue
                if existing.start <= candidate < existing.end:
                    candidate = existing.end
                    found_conflict = True
                    break

            if not found_conflict:
                return candidate

        return candidate

    def release_agv(self, agv_id: str) -> None:
        """
        Release all reservations for an AGV.
        Called when AGV completes its task or encounters an error.
        """
        if agv_id not in self._agv_reservations:
            return

        resource_keys = self._agv_reservations[agv_id]
        for key in resource_keys:
            if key in self._occupancy:
                self._occupancy[key] = [
                    interval for interval in self._occupancy[key]
                    if interval.agv_id != agv_id
                ]
                if not self._occupancy[key]:
                    del self._occupancy[key]

        del self._agv_reservations[agv_id]

    def release_path_segment(
        self,
        resource_key: str,
        agv_id: str,
    ) -> None:
        """
        Release a specific path segment reservation.
        Used for incremental release as AGV progresses through path.
        """
        if resource_key in self._occupancy:
            self._occupancy[resource_key] = [
                interval for interval in self._occupancy[resource_key]
                if interval.agv_id != agv_id
            ]
            if not self._occupancy[resource_key]:
                del self._occupancy[resource_key]

    def get_agv_schedule(self, agv_id: str) -> List[Tuple[str, float, float]]:
        """
        Get all reserved intervals for an AGV.

        Returns:
            List of (resource_key, start_time, end_time)
        """
        if agv_id not in self._agv_reservations:
            return []

        result = []
        for key in self._agv_reservations[agv_id]:
            if key in self._occupancy:
                for interval in self._occupancy[key]:
                    if interval.agv_id == agv_id:
                        result.append((key, interval.start, interval.end))
        result.sort(key=lambda x: x[1])  # Sort by start time
        return result

    def occupancy_at_time(
        self,
        resource_key: str,
        at_time: float,
    ) -> Optional[str]:
        """
        Get the AGV ID occupying a resource at a specific time.

        Returns:
            AGV ID or None if resource is free
        """
        if resource_key not in self._occupancy:
            return None

        for interval in self._occupancy[resource_key]:
            if interval.start <= at_time < interval.end:
                return interval.agv_id
        return None

    def occupancy_count(self, resource_key: str) -> int:
        """Number of active reservations on a resource."""
        return len(self._occupancy.get(resource_key, []))

    def clear(self) -> None:
        """Reset the entire occupancy table."""
        self._occupancy.clear()
        self._agv_reservations.clear()

    @property
    def total_reservations(self) -> int:
        """Total number of active reservations."""
        return sum(len(intervals) for intervals in self._occupancy.values())

    @property
    def active_agvs(self) -> int:
        """Number of AGVs with active reservations."""
        return len(self._agv_reservations)

    def __repr__(self) -> str:
        return (
            f"TimeWindowTable(resources={len(self._occupancy)}, "
            f"reservations={self.total_reservations}, "
            f"agvs={self.active_agvs})"
        )
