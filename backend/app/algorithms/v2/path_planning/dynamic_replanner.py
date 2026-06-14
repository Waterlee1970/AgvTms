"""
Dynamic Replanning Engine for real-time path adaptation.

When an AGV encounters unexpected obstacles, congestion, or a blocked path,
this engine performs a local incremental replan without recomputing the entire
path from scratch.

Uses a D* Lite variant (simplified incremental A*):
- Detects changes in the graph (blocked edges/nodes, new congestion)
- Repairs only the affected portion of the path
- Returns the updated path with minimal latency (<50ms target)

Replanning triggers:
1. Edge blocked: Another AGV/person/obstacle occupies a path segment
2. Node blocked: Dropoff/pickup station occupied beyond expected time
3. Priority override: Higher-priority AGV preempts the current path
4. Battery low: AGV must reroute through a charging station
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple

from .graph_builder import PathGraph
from .astar import BidirectionalAStar, PathResult
from .time_window_table import TimeWindowTable

logger = logging.getLogger(__name__)


class ReplanTrigger(str, Enum):
    """Triggers that initiate dynamic replanning."""
    EDGE_BLOCKED = "edge_blocked"
    NODE_BLOCKED = "node_blocked"
    CONGESTION = "congestion"
    PRIORITY_OVERRIDE = "priority_override"
    BATTERY_LOW = "battery_low"
    TASK_REROUTE = "task_reroute"


@dataclass
class ReplanEvent:
    """Event that triggers a replan."""
    trigger: ReplanTrigger
    agv_id: str
    affected_node: Optional[str] = None
    affected_edge: Optional[Tuple[str, str]] = None
    timestamp: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class AgvPathState:
    """Current path execution state for an AGV."""
    agv_id: str
    planned_path: List[str]
    current_segment_index: int        # Index of the node AGV is currently at/approaching
    current_node: str
    next_node: Optional[str] = None
    progress_along_current_edge: float = 0.0  # 0.0 to 1.0
    path_cost: float = 0.0
    remaining_nodes: int = 0
    is_replanning: bool = False


class DynamicReplanner:
    """
    Dynamic incremental replanning engine.

    Uses a simplified D* Lite approach:
    1. Detect affected path segments from events
    2. Preserve path before affected segment
    3. Replan only from affected point to goal
    4. Merge preserved + replanned segments
    5. Update time window reservations
    """

    def __init__(
        self,
        graph: PathGraph,
        time_windows: TimeWindowTable,
        verbose: bool = False,
    ):
        self.graph = graph
        self.tw = time_windows
        self.astar = BidirectionalAStar(graph)
        self.verbose = verbose

        # Track blocked resources for replanning
        self._blocked_nodes: Set[str] = set()
        self._blocked_edges: Set[Tuple[str, str]] = set()

        # Active AGV path states
        self._agv_states: Dict[str, AgvPathState] = {}

        # Replan history for analysis
        self._replan_count: Dict[str, int] = {}
        self._replan_history: List[ReplanEvent] = []

    def register_agv_path(
        self,
        agv_id: str,
        path: List[str],
        start_node: str,
    ) -> AgvPathState:
        """Register an AGV's planned path for monitoring."""
        state = AgvPathState(
            agv_id=agv_id,
            planned_path=path,
            current_segment_index=0,
            current_node=start_node,
            next_node=path[1] if len(path) > 1 else None,
            remaining_nodes=len(path) - 1,
        )
        self._agv_states[agv_id] = state
        if agv_id not in self._replan_count:
            self._replan_count[agv_id] = 0
        return state

    def update_agv_position(
        self,
        agv_id: str,
        current_node: str,
        next_node: Optional[str] = None,
        progress: float = 0.0,
    ) -> None:
        """Update AGV's current position along its path."""
        if agv_id not in self._agv_states:
            logger.warning(f"Unknown AGV {agv_id} in dynamic replanner")
            return

        state = self._agv_states[agv_id]
        state.current_node = current_node
        state.next_node = next_node
        state.progress_along_current_edge = progress

        # Release completed segments from time window table
        if state.planned_path and current_node in state.planned_path:
            idx = state.planned_path.index(current_node)
            # Release edges before current position
            for i in range(idx):
                node = state.planned_path[i]
                self.tw.release_path_segment(node, agv_id)
                if i > 0:
                    edge_key = f"{state.planned_path[i-1]}->{node}"
                    self.tw.release_path_segment(edge_key, agv_id)

            state.current_segment_index = idx
            state.remaining_nodes = len(state.planned_path) - idx - 1

    def handle_event(self, event: ReplanEvent) -> Optional[PathResult]:
        """
        Handle a replan trigger event.

        Returns:
            Updated PathResult if replan was successful, None if no replan needed
        """
        agv_id = event.agv_id
        if agv_id not in self._agv_states:
            return None

        state = self._agv_states[agv_id]
        original_path = state.planned_path

        # Mark blocked resources
        if event.trigger == ReplanTrigger.EDGE_BLOCKED and event.affected_edge:
            self._blocked_edges.add(event.affected_edge)

        if event.trigger == ReplanTrigger.NODE_BLOCKED and event.affected_node:
            self._blocked_nodes.add(event.affected_node)

        # Check if AGV's remaining path is affected
        affected = False
        remaining = original_path[state.current_segment_index:]
        goal = original_path[-1]

        for node in remaining:
            if node in self._blocked_nodes:
                affected = True
                break

        for i in range(len(remaining) - 1):
            edge = (remaining[i], remaining[i + 1])
            rev_edge = (remaining[i + 1], remaining[i])
            if edge in self._blocked_edges or rev_edge in self._blocked_edges:
                affected = True
                break

        if not affected:
            return None

        if self.verbose:
            logger.info(f"Replanning for {agv_id}: {event.trigger.value}")

        state.is_replanning = True
        self._replan_count[agv_id] = self._replan_count.get(agv_id, 0) + 1
        self._replan_history.append(event)

        # Preserve completed prefix of path
        completed_prefix = original_path[:state.current_segment_index]
        if not completed_prefix or completed_prefix[-1] != state.current_node:
            completed_prefix = original_path[:state.current_segment_index + 1]

        # Replan from current node to goal
        start_node = state.current_node

        # Combine blocked sets
        blocked = self._blocked_nodes.copy()
        # Avoid immediate re-entry to just-departed node
        if len(completed_prefix) >= 2:
            pass  # Don't block the goal

        replan_result = self.astar.find_path(start_node, goal, blocked)

        if not replan_result or not replan_result.path:
            logger.warning(f"Replan failed for {agv_id}: no alternative path found")
            state.is_replanning = False
            return None

        # Merge: completed_prefix + replanned (skip duplicate start node)
        if completed_prefix[-1] == replan_result.path[0]:
            new_path = completed_prefix[:-1] + replan_result.path
        else:
            new_path = completed_prefix + replan_result.path

        # Update AGV state
        state.planned_path = new_path
        state.current_segment_index = len(completed_prefix) - 1
        state.next_node = new_path[state.current_segment_index + 1] if len(new_path) > state.current_segment_index + 1 else None
        state.remaining_nodes = len(new_path) - state.current_segment_index - 1
        state.is_replanning = False

        if self.verbose:
            logger.info(
                f"Replan complete for {agv_id}: "
                f"{len(original_path)} → {len(new_path)} nodes "
                f"(+{len(new_path) - len(original_path)})"
            )

        return PathResult(
            path=new_path,
            total_distance=replan_result.total_distance,
            total_time=replan_result.total_time,
            segments=replan_result.segments,
            expanded_nodes=replan_result.expanded_nodes,
            search_time_ms=replan_result.search_time_ms,
            found=True,
        )

    def clear_blocked_resources(self) -> None:
        """Clear all temporary blocked resources."""
        self._blocked_nodes.clear()
        self._blocked_edges.clear()

    def unblock_node(self, node_id: str) -> None:
        """Remove a node from the blocked set."""
        self._blocked_nodes.discard(node_id)

    def unblock_edge(self, edge: Tuple[str, str]) -> None:
        """Remove an edge from the blocked set."""
        self._blocked_edges.discard(edge)
        rev_edge = (edge[1], edge[0])
        self._blocked_edges.discard(rev_edge)

    def get_replan_stats(self, agv_id: str) -> dict:
        """Get replanning statistics for an AGV."""
        return {
            "total_replans": self._replan_count.get(agv_id, 0),
            "current_path_length": len(self._agv_states.get(agv_id, AgvPathState(
                agv_id=agv_id, planned_path=[], current_segment_index=0,
                current_node="", path_cost=0, remaining_nodes=0,
            )).planned_path),
        }

    def get_all_replan_stats(self) -> dict:
        """Get replanning statistics for all AGVs."""
        total_replans = sum(self._replan_count.values())
        trigger_counts = {}
        for event in self._replan_history:
            trigger_counts[event.trigger.value] = trigger_counts.get(event.trigger.value, 0) + 1

        return {
            "total_replans": total_replans,
            "agv_replan_counts": dict(self._replan_count),
            "trigger_distribution": trigger_counts,
            "active_blocked_nodes": len(self._blocked_nodes),
            "active_blocked_edges": len(self._blocked_edges),
        }

    def __repr__(self) -> str:
        return (
            f"DynamicReplanner(agvs={len(self._agv_states)}, "
            f"blocked_nodes={len(self._blocked_nodes)}, "
            f"blocked_edges={len(self._blocked_edges)}, "
            f"total_replans={sum(self._replan_count.values())})"
        )
