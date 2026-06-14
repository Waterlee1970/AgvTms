"""
Bidirectional A* Path Planning with Time Window Constraints.

Core pathfinding engine for the V2 algorithm suite.

Features:
- Bidirectional A*: simultaneously searches from start and goal, meets in the middle
- Time Window A*: states include (node, arrival_time), constrained by occupancy
- Heap-based priority queue for O((V+E)logV) complexity
- Admissible Euclidean heuristic (guarantees optimality for distance-based cost)
- Support for k-alternative path generation

Comparison to V1 (ACO):
- V1: O(V²) Dijkstra + stochastic ACO path construction
- V2: O((V+E)logV) A* with proven optimality for single-source shortest path
"""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .graph_builder import GraphNode, GraphEdge, PathGraph
from .time_window_table import TimeWindowTable

logger = logging.getLogger(__name__)


@dataclass(order=True)
class AStarState:
    """
    State node in A* search. Ordered by f_cost for heap priority queue.

    Attributes:
        node_id: Current graph node identifier
        time: Arrival time at this node (for time-window constrained search)
        g_cost: Actual cost from start to this node
        f_cost: g_cost + heuristic (estimated total cost)
        parent: Previous state for path reconstruction
        edge_from_parent: The edge traversed from parent to this node
    """
    f_cost: float
    node_id: str = field(compare=False)
    time: float = field(compare=False, default=0.0)
    g_cost: float = field(compare=False, default=0.0)
    parent: Optional["AStarState"] = field(compare=False, default=None)
    edge_from_parent: Optional[GraphEdge] = field(compare=False, default=None)


@dataclass
class PathResult:
    """Result of a path planning query."""
    path: List[str]                    # ordered node IDs from start to goal
    total_distance: float              # total travel distance in meters
    total_time: float                  # total travel time in seconds
    segments: List[Tuple[str, str, float]]  # (from, to, duration) for each segment
    expanded_nodes: int                # nodes explored during search
    search_time_ms: float              # wall-clock search time
    found: bool                        # whether a path was found

    def __bool__(self) -> bool:
        return self.found


class BidirectionalAStar:
    """
    Bidirectional A* path planner.

    Simultaneously expands frontiers from start and goal nodes.
    Terminates when the two frontiers meet, typically exploring
    ~50% fewer nodes than unidirectional A*.
    """

    def __init__(self, graph: PathGraph):
        self.graph = graph

    def find_path(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None,
    ) -> PathResult:
        """
        Find shortest path from start to goal.

        Args:
            start: Starting node ID
            goal: Goal node ID
            blocked_nodes: Set of temporarily blocked node IDs

        Returns:
            PathResult with path, distance, and timing information
        """
        import time
        t_start = time.perf_counter()

        if start == goal:
            return PathResult(
                path=[start], total_distance=0.0, total_time=0.0,
                segments=[], expanded_nodes=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=True,
            )

        if start not in self.graph.nodes or goal not in self.graph.nodes:
            return PathResult(
                path=[], total_distance=0.0, total_time=0.0,
                segments=[], expanded_nodes=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=False,
            )

        blocked = blocked_nodes or set()
        if start in blocked or goal in blocked:
            return PathResult(
                path=[], total_distance=0.0, total_time=0.0,
                segments=[], expanded_nodes=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=False,
            )

        # Forward frontier: from start
        forward_open: List[AStarState] = []
        forward_closed: Dict[str, AStarState] = {}

        h_start = self.graph.heuristic(start, goal)
        start_state = AStarState(
            f_cost=h_start, node_id=start, time=0.0, g_cost=0.0
        )
        heapq.heappush(forward_open, start_state)

        # Backward frontier: from goal
        backward_open: List[AStarState] = []
        backward_closed: Dict[str, AStarState] = {}

        h_goal = self.graph.heuristic(goal, start)
        goal_state = AStarState(
            f_cost=h_goal, node_id=goal, time=0.0, g_cost=0.0
        )
        heapq.heappush(backward_open, goal_state)

        best_meeting: Optional[Tuple[str, float, AStarState, AStarState]] = None
        best_cost = float('inf')
        expanded = 0

        while forward_open and backward_open:
            # Check termination condition
            if forward_open and backward_open:
                f_min = forward_open[0].f_cost if forward_open else float('inf')
                b_min = backward_open[0].f_cost if backward_open else float('inf')
                if best_cost <= f_min + b_min:
                    break

            # --- Expand forward frontier ---
            if forward_open:
                expanded += 1
                current = heapq.heappop(forward_open)

                # Skip if already processed with better cost
                if current.node_id in forward_closed:
                    prev = forward_closed[current.node_id]
                    if prev.g_cost <= current.g_cost:
                        # Check backward frontier for improved meeting
                        if current.node_id in backward_closed:
                            self._try_meeting(
                                current, backward_closed[current.node_id],
                                best_cost, best_meeting,
                                lambda f, b: (best_cost, None)  # placeholder
                            )
                        continue

                forward_closed[current.node_id] = current

                # Check if this node meets the backward frontier
                if current.node_id in backward_closed:
                    bc = backward_closed[current.node_id]
                    meeting_cost = current.g_cost + bc.g_cost
                    if meeting_cost < best_cost:
                        best_cost = meeting_cost
                        best_meeting = (current.node_id, meeting_cost, current, bc)

                # Expand neighbors
                for neighbor, edge in self.graph.get_neighbors(current.node_id):
                    if neighbor in blocked:
                        continue
                    new_g = current.g_cost + edge.travel_time
                    new_h = self.graph.heuristic(neighbor, goal)
                    new_f = new_g + new_h

                    # Pruning: don't expand if worse than best meeting
                    if new_f >= best_cost:
                        continue

                    if neighbor in forward_closed:
                        if forward_closed[neighbor].g_cost <= new_g:
                            continue

                    heapq.heappush(forward_open, AStarState(
                        f_cost=new_f, node_id=neighbor,
                        time=current.time + edge.travel_time,
                        g_cost=new_g, parent=current,
                        edge_from_parent=edge,
                    ))

            # --- Expand backward frontier ---
            if backward_open:
                expanded += 1
                current = heapq.heappop(backward_open)

                if current.node_id in backward_closed:
                    prev = backward_closed[current.node_id]
                    if prev.g_cost <= current.g_cost:
                        continue

                backward_closed[current.node_id] = current

                # Check if this node meets the forward frontier
                if current.node_id in forward_closed:
                    fc = forward_closed[current.node_id]
                    meeting_cost = fc.g_cost + current.g_cost
                    if meeting_cost < best_cost:
                        best_cost = meeting_cost
                        best_meeting = (current.node_id, meeting_cost, fc, current)

                # Expand neighbors
                for neighbor, edge in self.graph.get_neighbors(current.node_id):
                    if neighbor in blocked:
                        continue
                    new_g = current.g_cost + edge.travel_time
                    new_h = self.graph.heuristic(neighbor, start)
                    new_f = new_g + new_h

                    if new_f >= best_cost:
                        continue

                    if neighbor in backward_closed:
                        if backward_closed[neighbor].g_cost <= new_g:
                            continue

                    heapq.heappush(backward_open, AStarState(
                        f_cost=new_f, node_id=neighbor,
                        time=current.time + edge.travel_time,
                        g_cost=new_g, parent=current,
                        edge_from_parent=edge,
                    ))

        # --- Reconstruct path ---
        if best_meeting is None:
            # Fallback: unidirectional if bidirectional failed
            return self._unidirectional_fallback(start, goal, blocked, t_start, expanded)

        meeting_node, _, forward_state, backward_state = best_meeting

        # Reconstruct forward half
        forward_path = []
        forward_segments = []
        node = forward_state
        while node is not None:
            forward_path.append(node.node_id)
            if node.edge_from_parent:
                forward_segments.append((
                    node.parent.node_id,
                    node.node_id,
                    node.edge_from_parent.travel_time,
                ))
            node = node.parent
        forward_path.reverse()
        forward_segments.reverse()

        # Reconstruct backward half (from meeting to goal)
        backward_path = []
        backward_segments = []
        node = backward_state
        while node is not None:
            if node.parent is not None:
                backward_path.append(node.parent.node_id)
                if node.edge_from_parent:
                    backward_segments.append((
                        node.node_id,
                        node.parent.node_id,
                        node.edge_from_parent.travel_time,
                    ))
            node = node.parent

        # Merge: forward + backward (skip duplicate meeting node)
        full_path = forward_path + backward_path
        full_segments = forward_segments + backward_segments
        total_distance = forward_state.g_cost + backward_state.g_cost
        # Convert time-based cost back to distance
        total_dist = 0.0
        for seg in full_segments:
            # Estimate distance from travel time * avg speed
            edge = self.graph.get_edge(seg[0], seg[1])
            if edge:
                total_dist += edge.distance

        return PathResult(
            path=full_path,
            total_distance=total_dist,
            total_time=total_distance,
            segments=full_segments,
            expanded_nodes=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=True,
        )

    def _try_meeting(self, forward_state, backward_state, best_cost, best_meeting, callback):
        """Helper to check meeting condition."""
        meeting_cost = forward_state.g_cost + backward_state.g_cost
        if meeting_cost < best_cost:
            return meeting_cost, (forward_state.node_id, meeting_cost, forward_state, backward_state)
        return best_cost, best_meeting

    def _unidirectional_fallback(
        self, start: str, goal: str, blocked: set, t_start: float, expanded: int
    ) -> PathResult:
        """Fallback to unidirectional A* if bidirectional fails."""
        import time

        open_list: List[AStarState] = []
        closed: Dict[str, AStarState] = {}

        h = self.graph.heuristic(start, goal)
        heapq.heappush(open_list, AStarState(
            f_cost=h, node_id=start, time=0.0, g_cost=0.0
        ))

        while open_list:
            expanded += 1
            current = heapq.heappop(open_list)

            if current.node_id == goal:
                # Reconstruct path
                path = []
                segments = []
                node = current
                total_dist = 0.0
                while node is not None:
                    path.append(node.node_id)
                    if node.edge_from_parent:
                        total_dist += node.edge_from_parent.distance
                        segments.append((
                            node.parent.node_id,
                            node.node_id,
                            node.edge_from_parent.travel_time,
                        ))
                    node = node.parent
                path.reverse()
                segments.reverse()

                return PathResult(
                    path=path,
                    total_distance=total_dist,
                    total_time=current.g_cost,
                    segments=segments,
                    expanded_nodes=expanded,
                    search_time_ms=(time.perf_counter() - t_start) * 1000,
                    found=True,
                )

            if current.node_id in closed:
                if closed[current.node_id].g_cost <= current.g_cost:
                    continue
            closed[current.node_id] = current

            for neighbor, edge in self.graph.get_neighbors(current.node_id):
                if neighbor in blocked:
                    continue
                new_g = current.g_cost + edge.travel_time
                new_h = self.graph.heuristic(neighbor, goal)
                new_f = new_g + new_h

                if neighbor in closed and closed[neighbor].g_cost <= new_g:
                    continue

                heapq.heappush(open_list, AStarState(
                    f_cost=new_f, node_id=neighbor,
                    time=current.time + edge.travel_time,
                    g_cost=new_g, parent=current,
                    edge_from_parent=edge,
                ))

        return PathResult(
            path=[], total_distance=0.0, total_time=0.0,
            segments=[], expanded_nodes=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=False,
        )

    def find_k_paths(
        self,
        start: str,
        goal: str,
        k: int = 3,
        blocked_nodes: Optional[Set[str]] = None,
    ) -> List[PathResult]:
        """
        Find k alternative paths using Yen's algorithm concept.

        Generates diverse path options for the traffic controller
        to select the least congested route.
        """
        paths: List[PathResult] = []

        # First path: standard A*
        first = self.find_path(start, goal, blocked_nodes)
        if not first:
            return []
        paths.append(first)

        # Generate alternatives by blocking nodes from found paths
        blocked = set(blocked_nodes or set())
        for i in range(k - 1):
            if i >= len(paths):
                break
            # Block one node from the i-th path and rerun
            path_i = paths[i].path
            if len(path_i) <= 2:
                continue

            # Block a middle node
            mid_idx = len(path_i) // 2
            candidate_blocked = blocked | {path_i[mid_idx]}
            alt = self.find_path(start, goal, candidate_blocked)
            if alt and alt.path not in [p.path for p in paths]:
                paths.append(alt)
            elif len(path_i) > 3:
                # Try blocking another node
                candidate_blocked2 = blocked | {path_i[mid_idx - 1]}
                alt2 = self.find_path(start, goal, candidate_blocked2)
                if alt2 and alt2.path not in [p.path for p in paths]:
                    paths.append(alt2)

        return paths


class TimeWindowAStar(BidirectionalAStar):
    """
    Time-Window constrained A* path planner.

    Extends bidirectional A* with time-window occupancy constraints.
    Each state now represents (node_id, arrival_time), and edges
    are only traversed when the target node is free.

    This is the primary path planner for multi-AGV collision-free routing.
    """

    def __init__(self, graph: PathGraph, time_windows: TimeWindowTable):
        super().__init__(graph)
        self.tw = time_windows

    def find_path_with_time(
        self,
        start: str,
        goal: str,
        start_time: float,
        agv_id: str = "",
        blocked_nodes: Optional[Set[str]] = None,
    ) -> PathResult:
        """
        Find collision-free path with time window constraints.

        Each state: (node_id, arrival_time)
        Constraints: node/edge must be free during the traversal interval.

        Args:
            start: Start node ID
            goal: Goal node ID
            start_time: Earliest departure time from start
            agv_id: AGV identifier (for time window reservation tracking)
            blocked_nodes: Additional blocked nodes

        Returns:
            PathResult with timed path segments
        """
        import time
        t_start = time.perf_counter()

        if start == goal:
            return PathResult(
                path=[start], total_distance=0.0, total_time=0.0,
                segments=[], expanded_nodes=0,
                search_time_ms=(time.perf_counter() - t_start) * 1000,
                found=True,
            )

        blocked = blocked_nodes or set()

        # State key: (node_id, arrival_time_quantized) to avoid infinite states
        TIME_BUCKET = 0.5  # 500ms time quantization

        open_list: List[AStarState] = []
        # Track best g_cost for each (node, time_bucket)
        best_cost_at: Dict[Tuple[str, int], float] = {}

        h = self.graph.heuristic(start, goal)
        heapq.heappush(open_list, AStarState(
            f_cost=h, node_id=start, time=start_time, g_cost=0.0
        ))

        expanded = 0
        MAX_EXPANSIONS = 50000  # Safety limit

        while open_list and expanded < MAX_EXPANSIONS:
            expanded += 1
            current = heapq.heappop(open_list)

            if current.node_id == goal:
                # Reconstruct path
                path = []
                segments = []
                total_dist = 0.0
                node = current
                while node is not None:
                    path.append(node.node_id)
                    if node.edge_from_parent:
                        total_dist += node.edge_from_parent.distance
                        segments.append((
                            node.parent.node_id,
                            node.node_id,
                            node.edge_from_parent.travel_time,
                        ))
                    node = node.parent
                path.reverse()
                segments.reverse()

                return PathResult(
                    path=path,
                    total_distance=total_dist,
                    total_time=current.time - start_time,
                    segments=segments,
                    expanded_nodes=expanded,
                    search_time_ms=(time.perf_counter() - t_start) * 1000,
                    found=True,
                )

            # State dominance pruning
            time_bucket = int(current.time / TIME_BUCKET)
            state_key = (current.node_id, time_bucket)
            if state_key in best_cost_at and best_cost_at[state_key] <= current.g_cost:
                continue
            best_cost_at[state_key] = current.g_cost

            if current.node_id in blocked:
                continue

            for neighbor, edge in self.graph.get_neighbors(current.node_id):
                if neighbor in blocked:
                    continue

                # Calculate arrival and departure times
                travel_time = edge.travel_time
                arrive_time = current.time + travel_time

                # Check time window availability
                # 1. Current node must be free during departure
                if not self.tw.is_free(current.node_id, current.time, current.time, agv_id):
                    # Check next available window
                    next_free = self.tw.next_free_time(current.node_id, current.time, agv_id)
                    if next_free is None:
                        continue
                    # Wait for the node to become free
                    wait_time = next_free - current.time
                    arrive_time = current.time + wait_time + travel_time

                # 2. Edge must be free during traversal
                edge_key = f"{current.node_id}->{neighbor}"
                depart_time = arrive_time - travel_time
                if not self.tw.is_free(edge_key, depart_time, arrive_time, agv_id):
                    edge_next = self.tw.next_free_time(edge_key, depart_time, agv_id)
                    if edge_next is None:
                        continue
                    wait = edge_next - depart_time
                    depart_time = edge_next
                    arrive_time = depart_time + travel_time

                # 3. Target node must be free at arrival
                if not self.tw.is_free(neighbor, arrive_time, arrive_time + 1.0, agv_id):
                    node_next = self.tw.next_free_time(neighbor, arrive_time, agv_id)
                    if node_next is None:
                        continue
                    wait = node_next - arrive_time
                    arrive_time += wait

                new_g = arrive_time - start_time
                new_h = self.graph.heuristic(neighbor, goal)
                new_f = new_g + new_h

                heapq.heappush(open_list, AStarState(
                    f_cost=new_f, node_id=neighbor,
                    time=arrive_time, g_cost=new_g,
                    parent=current, edge_from_parent=edge,
                ))

        # Fallback to unidirectional with time windows
        return PathResult(
            path=[], total_distance=0.0, total_time=0.0,
            segments=[], expanded_nodes=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=False,
        )


def create_path_planner(
    graph: PathGraph,
    use_time_windows: bool = False,
    time_windows: Optional[TimeWindowTable] = None,
) -> BidirectionalAStar:
    """Factory function to create appropriate path planner."""
    if use_time_windows and time_windows is not None:
        return TimeWindowAStar(graph, time_windows)
    return BidirectionalAStar(graph)
