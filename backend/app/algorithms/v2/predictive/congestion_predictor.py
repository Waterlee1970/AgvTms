"""
Congestion Hotspot Predictor: Spatio-temporal congestion forecasting.

Predicts where and when traffic congestion will occur using:
- Graph topology analysis (bottleneck detection)
- Historical occupancy patterns
- Current system load extrapolation
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CongestionHotspot:
    """A predicted congestion hotspot."""
    node_id: str
    severity: float           # 0-1, higher = worse
    start_time: float         # Unix timestamp when congestion starts
    peak_time: float          # When congestion peaks
    duration_seconds: float   # How long it lasts
    contributing_agvs: List[str] = field(default_factory=list)
    cause: str = ""           # "crossing", "narrow_path", "high_density", etc.


@dataclass
class CongestionForecast:
    """Complete congestion prediction result."""
    forecast_time: float      # When this forecast was made
    horizon_seconds: float    # Prediction horizon
    hotspots: List[CongestionHotspot] = field(default_factory=list)
    overall_congestion_score: float = 0.0  # System-wide 0-1
    bottleneck_nodes: List[Tuple[str, float]] = field(default_factory=list)  # (node, betweenness)


class CongestionPredictor:
    """
    Predicts future congestion on the map graph.

    Uses three signal sources:
    1. **Structural**: Betweenness centrality finds natural bottlenecks
    2. **Temporal**: Sliding window occupancy history
    3. **Extrapolation**: Current AGV trajectories projected forward
    """

    def __init__(
        self,
        window_seconds: float = 300.0,  # 5-min lookback
        horizon_seconds: float = 120.0,  # 2-min lookahead
        history_max_len: int = 2000,
    ):
        self.window = window_seconds
        self.horizon = horizon_seconds
        self.history_max = history_max_len

        # Node-level occupancy history: node_id -> [(timestamp, agv_count)]
        self._node_occupancy: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

        # Edge-level traversal history: edge_key -> [(timestamp, agv_id)]
        self._edge_traversals: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

        # Structural bottleneck scores (cached after graph build)
        self._betweenness: Dict[str, float] = {}
        self._graph_loaded: bool = False

        # Current AGV position snapshot
        self._agv_positions: Dict[str, str] = {}  # agv_id -> node_id
        self._agv_paths: Dict[str, List[str]] = {}  # agv_id -> path nodes

    def set_graph_topology(
        self,
        nodes: Set[str],
        edges: List[Tuple[str, str]],
    ) -> None:
        """Build graph and compute structural bottlenecks via approximate betweenness."""
        self._betweenness.clear()

        if not nodes or not edges:
            self._graph_loaded = True
            return

        adjacency: Dict[str, List[str]] = defaultdict(list)
        for u, v in edges:
            adjacency[u].append(v)
            adjacency[v].append(u)

        # Approximate betweenness via sampling
        sample_nodes = list(nodes)[:min(50, len(nodes))]  # Sample up to 50 sources
        for source in sample_nodes:
            # BFS from source
            predecessors: Dict[str, List[str]] = {source: []}
            distances: Dict[str, int] = {source: 0}
            queue = [source]
            visited_order: List[str] = []

            head = 0
            while head < len(queue):
                u = queue[head]; head += 1
                visited_order.append(u)
                for v in adjacency[u]:
                    if v not in distances:
                        distances[v] = distances[u] + 1
                        predecessors[v] = [u]
                        queue.append(v)
                    elif distances[v] == distances[u] + 1:
                        predecessors[v].append(u)

            # Count shortest paths through each node
            delta: Dict[str, float] = defaultdict(float)
            for w in reversed(visited_order):
                for pred in predecessors[w]:
                    n_pred = len(predecessors[pred])
                    if n_pred > 0:
                        ratio = (1.0 + delta.get(w, 0.0)) / n_pred
                        delta[pred] += ratio
                if w != source:
                    self._betweenness[w] = self._betweenness.get(w, 0.0) + delta.get(w, 0.0)

        self._graph_loaded = True
        logger.info(f"Congestion predictor: computed betweenness for {len(self._betweenness)} nodes")

    def record_state_snapshot(
        self,
        agv_positions: Dict[str, str],       # agv_id -> current_node
        agv_paths: Optional[Dict[str, List[str]]] = None,  # agv_id -> planned path
    ) -> None:
        """Record current AGV positions and paths."""
        now = time.time()
        self._agv_positions = dict(agv_positions)
        if agv_paths:
            self._agv_paths = dict(agv_paths)

        # Record per-node occupancy
        node_counts: Dict[str, int] = defaultdict(int)
        for agv_id, node in agv_positions.items():
            node_counts[node] += 1
            # Also record path nodes as "expected occupancy"
            if agv_paths and agv_id in agv_paths:
                for pnode in agv_paths[agv_id]:
                    node_counts[pnode] += 0.3  # Weight less than actual presence

        for node, count in node_counts.items():
            self._node_occupancy[node].append((now, count))

    def predict(self) -> CongestionForecast:
        """Generate congestion forecast."""
        now = time.time()
        hotspots: List[CongestionHotspot] = []
        bottleneck_list: List[Tuple[str, float]] = []

        # --- 1. Structural bottlenecks ---
        if self._betweenness:
            sorted_bn = sorted(self._betweenness.items(), key=lambda x: -x[1])
            max_bn = max(b for _, b in sorted_bn) if sorted_bn else 1.0
            for node, bn_val in sorted_bn[:15]:  # Top-15 bottlenecks
                norm = bn_val / max(max_bn, 1e-8)
                bottleneck_list.append((node, norm))

        # --- 2. Temporal occupancy patterns ---
        node_scores: Dict[str, float] = {}
        for node, hist in self._node_occupancy.items():
            # Filter to window
            recent = [(t, c) for t, c in hist if now - t <= self.window]
            if not recent:
                continue

            # Compute weighted average (more recent = more weight)
            weights = [(now - t) / self.window for t, _ in recent]
            inv_weights = [max(1e-6, 1 - w) for w in weights]
            total_w = sum(inv_weights)
            if total_w > 0:
                avg_occ = sum(c * iw for (_, c), iw in zip(recent, inv_weights)) / total_w
            else:
                avg_occ = 0.0

            # Trend: compare first half vs second half
            mid = len(recent) // 2
            if mid > 0:
                early_avg = sum(c for _, c in recent[:mid]) / mid
                late_avg = sum(c for _, c in recent[mid:]) / max(len(recent) - mid, 1)
                trend = (late_avg - early_avg) / max(early_avg, 1e-6)
            else:
                trend = 0.0

            # Combine occupancy + trend
            score = min(avg_occ / 3.0, 1.0) * 0.6 + min(max(trend, 0), 1.0) * 0.4
            node_scores[node] = score

        # --- 3. Path projection (where AGVs are heading) ---
        projected_counts: Dict[str, float] = defaultdict(float)
        for agv_id, path in self._agv_paths.items():
            if not path:
                continue
            for i, node in enumerate(path):
                # Closer nodes get more weight
                weight = 1.0 / (i + 1)
                projected_counts[node] += weight

        # Normalize
        proj_max = max(projected_counts.values(), default=1.0)
        if proj_max > 0:
            projected_counts = {k: v / proj_max for k, v in projected_counts.items()}

        # --- Fuse all signals ---
        all_nodes = set(node_scores.keys()) | set(projected_counts.keys()) | set(dict(bottleneck_list).keys())
        for node in all_nodes:
            sig_temporal = node_scores.get(node, 0.0)
            sig_projected = projected_counts.get(node, 0.0)
            sig_structural = next((b for n, b in bottleneck_list if n == node), 0.0) / max((b for _, b in bottleneck_list[:5]), default=1.0)

            fused = 0.40 * sig_temporal + 0.35 * sig_projected + 0.25 * sig_structural

            if fused > 0.25:  # Threshold for reporting
                cause_parts = []
                if sig_structural > 0.3:
                    cause_parts.append("bottleneck")
                if sig_projected > 0.4:
                    cause_parts.append("converging_paths")
                if sig_temporal > 0.4:
                    cause_parts.append("historical_hotspot")

                hotspots.append(CongestionHotspot(
                    node_id=node,
                    severity=min(fused, 1.0),
                    start_time=now + 10.0,  # Expect congestion in ~10s
                    peak_time=now + 45.0,    # Peak around ~45s
                    duration_seconds=60.0,
                    cause="+".join(cause_parts) if cause_parts else "unknown",
                ))

        # Sort by severity
        hotspots.sort(key=lambda h: -h.severity)

        overall_score = max((h.severity for h in hotspots), default=0.0)

        return CongestionForecast(
            forecast_time=now,
            horizon_seconds=self.horizon,
            hotspots=hotspots,
            overall_congestion_score=overall_score,
            bottleneck_nodes=bottleneck_list[:10],
        )

    @property
    def stats(self) -> dict:
        return {
            "tracked_nodes": len(self._node_occupancy),
            "graph_loaded": self._graph_loaded,
            "bottlenecks_known": len(self._betweenness),
            "active_agvs_tracked": len(self._agv_positions),
        }
