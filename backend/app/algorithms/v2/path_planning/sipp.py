"""
SIPP (Safe Interval Path Planning) for large-scale AGV fleets.

SIPP extends A* to work in the time dimension without discretizing time
into fixed steps. Instead, it tracks "safe intervals" on each node — 
time ranges during which the node is unoccupied. This dramatically reduces
the state space compared to time-stepped approaches.

Key innovation:
- Standard Time-Window A* state: (node, discrete_time_step) → O(V × T) states
- SIPP state: (node, safe_interval_index) → O(V × I) states, where I ≪ T
- Each safe interval groups infinite continuous time points into one search state

Theoretical foundation:
- Phillips & Likhachev (2011): "SIPP: Safe Interval Path Planning for Dynamic Environments"
- Each node maintains intervals [t_start, t_end) when it's unoccupied
- State transitions check if arrival_time falls within a safe interval
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .graph_builder import PathGraph
from .time_window_table import TimeWindowTable

logger = logging.getLogger(__name__)


@dataclass
class SafeInterval:
    """A continuous safe time interval on a node."""
    start: float       # interval start time
    end: float         # interval end time (inclusive)
    index: int = 0     # interval index on this node

    def contains(self, time: float) -> bool:
        """Check if time falls within this interval."""
        return self.start <= time <= self.end

    def duration(self) -> float:
        """Length of the safe interval."""
        return max(0.0, self.end - self.start)


@dataclass(order=True)
class SippState:
    """Search state in SIPP: (node, safe_interval_index, earliest_arrival)."""
    f_cost: float
    node_id: str = field(compare=False)
    interval: SafeInterval = field(compare=False)
    arrival_time: float = field(compare=False)
    g_cost: float = field(compare=False)
    parent: Optional["SippState"] = field(compare=False, default=None)
    edge_to_here: Optional[Tuple[str, str, float]] = field(compare=False, default=None)


@dataclass
class SippPathResult:
    """SIPP path planning result."""
    path: List[str]                    # node IDs
    arrival_times: List[float]         # arrival time at each node
    depart_times: List[float]          # departure time from each node
    total_distance: float
    total_time: float
    wait_times: List[float]            # wait time at each node
    expanded_states: int
    search_time_ms: float
    found: bool


class SippPlanner:
    """
    Safe Interval Path Planner for collision-free multi-AGV routing.

    Advantages over standard TimeWindow A*:
    1. State space = number of nodes × safe intervals (not time steps)
    2. Handles dynamic wait decisions natively
    3. Scales to 100+ AGVs on 1000+ node graphs
    """

    def __init__(self, graph: PathGraph, time_windows: TimeWindowTable):
        self.graph = graph
        self.tw = time_windows
        self._safe_intervals_cache: Dict[str, List[SafeInterval]] = {}

    def plan(
        self,
        start: str,
        goal: str,
        start_time: float,
        agv_id: str = "",
        horizon: float = float('inf'),
    ) -> SippPathResult:
        """
        Plan a collision-free path using SIPP.

        Args:
            start: Start node ID
            goal: Goal node ID
            start_time: Earliest departure time
            agv_id: AGV identifier for reservation queries
            horizon: Planning horizon (inf for unlimited)

        Returns:
            SippPathResult with timed path information
        """
        import time
        t_start = time.perf_counter()

        if start == goal:
            return SippPathResult(
                path=[start], arrival_times=[start_time],
                depart_times=[start_time], total_distance=0.0,
                total_time=0.0, wait_times=[0.0],
                expanded_states=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=True,
            )

        if start not in self.graph.nodes or goal not in self.graph.nodes:
            return SippPathResult(
                path=[], arrival_times=[], depart_times=[], total_distance=0.0,
                total_time=0.0, wait_times=[],
                expanded_states=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=False,
            )

        # Compute safe intervals for all nodes (from occupancy table)
        self._compute_safe_intervals(horizon)

        # Track best arrival time per (node, interval_index)
        best_arrival: Dict[Tuple[str, int], float] = {}

        open_list: List[SippState] = []

        # Get safe interval containing start_time at start node
        start_intervals = self._safe_intervals_cache.get(start, [])
        start_interval = SafeInterval(start=start_time, end=horizon, index=0)
        for si in start_intervals:
            if si.contains(start_time):
                start_interval = si
                break

        h = self.graph.heuristic(start, goal)
        heapq.heappush(open_list, SippState(
            f_cost=h, node_id=start, interval=start_interval,
            arrival_time=start_time, g_cost=0.0,
        ))

        expanded = 0
        MAX_EXPANSIONS = 200000

        while open_list and expanded < MAX_EXPANSIONS:
            expanded += 1
            current = heapq.heappop(open_list)

            # Goal check (within safe interval)
            if current.node_id == goal:
                path, arrivals, departs, waits = self._reconstruct_sipp_path(current)
                return SippPathResult(
                    path=path,
                    arrival_times=arrivals,
                    depart_times=departs,
                    total_distance=self._path_distance(path),
                    total_time=current.arrival_time - start_time,
                    wait_times=waits,
                    expanded_states=expanded,
                    search_time_ms=(time.perf_counter() - t_start) * 1000,
                    found=True,
                )

            # State dominance check
            state_key = (current.node_id, current.interval.index)
            if state_key in best_arrival and best_arrival[state_key] <= current.arrival_time:
                continue
            best_arrival[state_key] = current.arrival_time

            # Expand to neighbors
            for neighbor, edge in self.graph.get_neighbors(current.node_id):
                travel_time = edge.travel_time
                min_arrival = current.arrival_time + travel_time

                # Skip if beyond horizon
                if min_arrival > horizon:
                    continue

                # Get safe intervals on neighbor
                neighbor_intervals = self._safe_intervals_cache.get(neighbor, [])

                for si in neighbor_intervals:
                    # Arrival must fall within a safe interval
                    if min_arrival > si.end:
                        continue

                    actual_arrival = max(min_arrival, si.start)
                    if actual_arrival <= si.end:
                        new_g = actual_arrival - start_time
                        new_h = self.graph.heuristic(neighbor, goal)
                        new_f = new_g + new_h

                        heapq.heappush(open_list, SippState(
                            f_cost=new_f,
                            node_id=neighbor,
                            interval=si,
                            arrival_time=actual_arrival,
                            g_cost=new_g,
                            parent=current,
                            edge_to_here=(current.node_id, neighbor, travel_time),
                        ))
                        break  # First valid interval is sufficient

        return SippPathResult(
            path=[], arrival_times=[], depart_times=[], total_distance=0.0,
            total_time=0.0, wait_times=[],
            expanded_states=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=False,
        )

    def _compute_safe_intervals(self, horizon: float) -> None:
        """
        Compute safe intervals for all nodes from the occupancy table.

        Strategy:
        - For each node, extract all occupancy intervals
        - Gaps between occupancy intervals = safe intervals
        - First interval: [0, first_occupancy_start)
        - Last interval: [last_occupancy_end, horizon)
        """
        self._safe_intervals_cache.clear()

        for node_id in self.graph.nodes:
            intervals: List[SafeInterval] = []
            last_end = 0.0
            idx = 0

            # Get occupancy intervals for this node
            if node_id in self.tw._occupancy:
                occupied = sorted(self.tw._occupancy[node_id], key=lambda x: x.start)

                for occ in occupied:
                    if occ.start > last_end:
                        # Gap = safe interval
                        intervals.append(SafeInterval(
                            start=last_end, end=occ.start, index=idx
                        ))
                        idx += 1
                    last_end = max(last_end, occ.end)

            # Final interval to horizon
            if last_end < horizon:
                intervals.append(SafeInterval(
                    start=last_end, end=horizon, index=idx
                ))

            self._safe_intervals_cache[node_id] = intervals

    def _reconstruct_sipp_path(
        self, goal_state: SippState
    ) -> Tuple[List[str], List[float], List[float], List[float]]:
        """Reconstruct the path with timing information."""
        nodes_rev = []
        arrivals_rev = []
        departs_rev = []
        waits_rev = []

        state = goal_state
        while state is not None:
            nodes_rev.append(state.node_id)
            arrivals_rev.append(state.arrival_time)

            # Departure time
            if state.parent is not None:
                # Wait time at this node before departing
                edge_travel = state.edge_to_here[2] if state.edge_to_here else 0.0
                depart = state.arrival_time  # Assume immediate departure after arrival
                waits_rev.append(max(0, state.arrival_time - (state.parent.arrival_time + edge_travel)))
                departs_rev.append(depart)
            else:
                departs_rev.append(state.arrival_time)
                waits_rev.append(0.0)

            state = state.parent

        # Reverse to get forward order
        path = nodes_rev[::-1]
        arrivals = arrivals_rev[::-1]
        departs = departs_rev[::-1]
        waits = waits_rev[::-1]

        return path, arrivals, departs, waits

    def _path_distance(self, path: List[str]) -> float:
        """Calculate total distance of a path."""
        total = 0.0
        for i in range(len(path) - 1):
            edge = self.graph.get_edge(path[i], path[i + 1])
            if edge:
                total += edge.distance
        return total

    def reserve_sipp_path(
        self,
        result: SippPathResult,
        agv_id: str,
        node_dwell_time: float = 0.5,
    ) -> bool:
        """
        Reserve the SIPP-planned path in the time window table.

        Returns:
            True if all reservations were successful
        """
        for i, node_id in enumerate(result.path):
            arrive = result.arrival_times[i]
            depart = result.depart_times[i]

            # Reserve node
            if not self.tw.reserve(node_id, arrive, depart, agv_id):
                self.tw.release_agv(agv_id)
                return False

            # Reserve edge
            if i < len(result.path) - 1:
                next_node = result.path[i + 1]
                edge_key = f"{node_id}->{next_node}"
                if not self.tw.reserve(edge_key, depart, result.arrival_times[i + 1], agv_id):
                    self.tw.release_agv(agv_id)
                    return False

        return True
