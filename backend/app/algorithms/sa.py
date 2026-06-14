"""
Simulated Annealing for AGV Task Assignment Optimization.

模拟退火算法 - 用于任务到AGV的最优分配

Implements the Metropolis-Hastings algorithm for combinatorial optimization:
1. Generate initial greedy solution
2. Perturb via neighbor operations (swap, reassign)
3. Accept/reject based on Metropolis criterion
4. Exponential cooling schedule

Mathematical foundation:
- Acceptance probability: P(accept) = exp(-ΔE / T)
- Cooling: T_k = T_0 * α^k
- Objective: minimize makespan + priority-weighted tardiness
"""

from __future__ import annotations

import copy
import logging
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..models.schemas import (
    AgvStatus,
    AgvTask,
    AgvTaskStatus,
    MapEdge,
    MapNode,
    SaConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class SaResult:
    """Result of a Simulated Annealing optimization run."""
    assignments: Dict[str, List[AgvTask]]  # agv_id -> list of assigned tasks
    total_cost: float
    makespan: float
    energy_history: List[float]
    acceptance_rate: float
    runtime_ms: float


class SimulatedAnnealing:
    """
    Simulated Annealing for optimal task-to-AGV assignment.

    Explores the solution space by:
    - Swapping tasks between AGVs
    - Reassigning individual tasks
    - Accepting worse solutions with decreasing probability as temperature drops
    """

    def __init__(self, config: SaConfig):
        self.config = config
        self.nodes: Dict[str, MapNode] = {}
        self.edges: Dict[Tuple[str, str], MapEdge] = {}
        self.adjacency: Dict[str, List[str]] = defaultdict(list)
        self._shortest_path_cache: Dict[Tuple[str, str], float] = {}

    def _build_graph(self, nodes: List[MapNode], edges: List[MapEdge]):
        """Build graph for distance calculations."""
        self.nodes = {n.id: n for n in nodes}
        self.edges = {}
        self.adjacency = defaultdict(list)
        self._shortest_path_cache = {}

        for edge in edges:
            key = (edge.from_node, edge.to_node)
            self.edges[key] = edge
            self.adjacency[edge.from_node].append(edge.to_node)
            if edge.direction.value in ("bidirectional", "backward"):
                self.adjacency[edge.to_node].append(edge.from_node)

    def _dijkstra_distance(self, start: str, end: str) -> float:
        """Cached Dijkstra distance between two nodes."""
        cache_key = (start, end)
        if cache_key in self._shortest_path_cache:
            return self._shortest_path_cache[cache_key]

        if start == end:
            self._shortest_path_cache[cache_key] = 0.0
            return 0.0

        dist: Dict[str, float] = {n: float('inf') for n in self.nodes}
        dist[start] = 0.0
        unvisited = set(self.nodes.keys())

        while unvisited:
            current = min(unvisited, key=lambda n: dist[n])
            if dist[current] == float('inf'):
                break
            if current == end:
                break
            unvisited.remove(current)

            for neighbor in self.adjacency.get(current, []):
                if neighbor not in unvisited:
                    continue
                edge = self.edges.get((current, neighbor))
                if edge is None:
                    continue
                alt = dist[current] + edge.distance
                if alt < dist[neighbor]:
                    dist[neighbor] = alt

        result = dist.get(end, float('inf'))
        self._shortest_path_cache[cache_key] = result
        return result

    def _generate_initial_solution(
        self, tasks: List[AgvTask], agvs: List[AgvStatus]
    ) -> Dict[str, List[AgvTask]]:
        """
        Generate initial solution using greedy assignment.
        
        Tasks sorted by priority (descending), assigned to nearest available AGV.
        """
        assignments: Dict[str, List[AgvTask]] = {agv.id: [] for agv in agvs}
        sorted_tasks = sorted(tasks, key=lambda t: (-t.priority, t.create_time))

        for task in sorted_tasks:
            best_agv = None
            best_cost = float('inf')

            for agv in agvs:
                agv_node = agv.current_node or self._find_nearest_node(agv.x, agv.y)
                if agv_node not in self.nodes:
                    continue

                # Cost = distance to pickup + pickup-to-dropoff distance
                dist_to_pickup = self._dijkstra_distance(agv_node, task.pickup_node)
                task_dist = self._dijkstra_distance(task.pickup_node, task.dropoff_node)
                total = dist_to_pickup + task_dist

                # Factor in existing workload
                existing_tasks = len(assignments[agv.id])
                total *= (1 + 0.1 * existing_tasks)

                if total < best_cost:
                    best_cost = total
                    best_agv = agv.id

            if best_agv:
                assignments[best_agv].append(task)

        return assignments

    def _calculate_cost(self, assignments: Dict[str, List[AgvTask]]) -> Tuple[float, float]:
        """
        Calculate total cost and makespan of an assignment.
        
        Cost = weighted sum of travel distances + priority-weighted tardiness penalty
        """
        total_cost = 0.0
        agv_completion_times: Dict[str, float] = {}

        for agv_id, tasks in assignments.items():
            agv_time = 0.0
            current_node = None

            # Find AGV start position
            for agv in self._current_agvs:
                if agv.id == agv_id:
                    current_node = agv.current_node or self._find_nearest_node(agv.x, agv.y)
                    break

            for task in tasks:
                if current_node:
                    dist_to_pickup = self._dijkstra_distance(current_node, task.pickup_node)
                    agv_time += dist_to_pickup / 1.5  # avg speed 1.5 m/s

                task_dist = self._dijkstra_distance(task.pickup_node, task.dropoff_node)
                agv_time += task_dist / 1.5

                # Priority-weighted tardiness
                if task.deadline:
                    deadline_ts = task.deadline.timestamp()
                    now_ts = datetime.now().timestamp()
                    deadline_offset = max(0, deadline_ts - now_ts)
                    tardiness = max(0, agv_time - deadline_offset)
                    total_cost += tardiness * task.priority * 10.0

                current_node = task.dropoff_node

            agv_completion_times[agv_id] = agv_time
            total_cost += agv_time * 0.5  # Travel time cost

        makespan = max(agv_completion_times.values()) if agv_completion_times else 0.0
        total_cost += makespan * 2.0  # Makespan penalty

        return total_cost, makespan

    def _generate_neighbor(self, assignments: Dict[str, List[AgvTask]]) -> Dict[str, List[AgvTask]]:
        """
        Generate a neighboring solution by one of these operations:
        1. Swap two tasks between different AGVs
        2. Move a task from one AGV to another
        3. Swap task order within an AGV's queue
        4. Reassign a single task to the best AGV
        """
        neighbor = copy.deepcopy(assignments)
        agv_ids = list(assignments.keys())
        op = random.random()

        if op < 0.35 and len(agv_ids) >= 2:
            # Swap tasks between two AGVs
            a1, a2 = random.sample(agv_ids, 2)
            if neighbor[a1] and neighbor[a2]:
                i1 = random.randint(0, len(neighbor[a1]) - 1)
                i2 = random.randint(0, len(neighbor[a2]) - 1)
                neighbor[a1][i1], neighbor[a2][i2] = neighbor[a2][i2], neighbor[a1][i1]

        elif op < 0.65 and len(agv_ids) >= 2:
            # Move a task from one AGV to another
            source = random.choice(agv_ids)
            if neighbor[source]:
                task_idx = random.randint(0, len(neighbor[source]) - 1)
                task = neighbor[source].pop(task_idx)
                target = random.choice([a for a in agv_ids if a != source])
                insert_pos = random.randint(0, len(neighbor[target]))
                neighbor[target].insert(insert_pos, task)

        elif op < 0.85:
            # Swap task order within an AGV
            agv_id = random.choice(agv_ids)
            if len(neighbor[agv_id]) >= 2:
                i, j = random.sample(range(len(neighbor[agv_id])), 2)
                neighbor[agv_id][i], neighbor[agv_id][j] = neighbor[agv_id][j], neighbor[agv_id][i]

        else:
            # Reassign a task to best AGV (greedy perturbation)
            all_tasks = []
            for agv_id, tasks in assignments.items():
                for t in tasks:
                    all_tasks.append((agv_id, t))

            if all_tasks:
                agv_id, task = random.choice(all_tasks)
                # Remove from current
                neighbor[agv_id].remove(task)
                # Assign to AGV with least load
                best_agv = min(neighbor.keys(), key=lambda a: len(neighbor[a]))
                neighbor[best_agv].append(task)

        return neighbor

    def _find_nearest_node(self, x: float, y: float) -> str:
        """Find nearest map node to given coordinates."""
        min_dist = float('inf')
        nearest = None
        for node_id, node in self.nodes.items():
            dist = math.sqrt((node.x - x) ** 2 + (node.y - y) ** 2)
            if dist < min_dist:
                min_dist = dist
                nearest = node_id
        return nearest or list(self.nodes.keys())[0]

    def optimize(
        self,
        nodes: List[MapNode],
        edges: List[MapEdge],
        tasks: List[AgvTask],
        agvs: List[AgvStatus],
    ) -> SaResult:
        """
        Run Simulated Annealing optimization.

        Args:
            nodes: Map nodes
            edges: Map edges
            tasks: Pending tasks to assign
            agvs: Available AGVs

        Returns:
            SaResult with optimized assignments
        """
        start_time = time.perf_counter()
        self._build_graph(nodes, edges)
        self._current_agvs = agvs

        # Generate initial solution
        current = self._generate_initial_solution(tasks, agvs)
        current_cost, current_makespan = self._calculate_cost(current)

        best = copy.deepcopy(current)
        best_cost = current_cost
        best_makespan = current_makespan

        energy_history = [current_cost]
        accepted = 0
        total_moves = 0

        T = self.config.initial_temp

        for iteration in range(self.config.iterations):
            # Generate neighbor
            neighbor = self._generate_neighbor(current)
            neighbor_cost, neighbor_makespan = self._calculate_cost(neighbor)

            delta = neighbor_cost - current_cost
            total_moves += 1

            # Metropolis acceptance criterion
            if delta < 0:
                # Better solution: always accept
                current = neighbor
                current_cost = neighbor_cost
                current_makespan = neighbor_makespan
                accepted += 1

                if current_cost < best_cost:
                    best = copy.deepcopy(current)
                    best_cost = current_cost
                    best_makespan = current_makespan
            else:
                # Worse solution: accept with probability exp(-ΔE/T)
                prob = math.exp(-delta / T) if T > 0 else 0
                if random.random() < prob:
                    current = neighbor
                    current_cost = neighbor_cost
                    current_makespan = neighbor_makespan
                    accepted += 1

            # Cool down
            T *= self.config.cooling_rate

            # Early termination if frozen
            if T < self.config.min_temp:
                logger.debug(f"SA frozen at iteration {iteration}")
                break

            energy_history.append(current_cost)

            if iteration % 100 == 0:
                logger.debug(f"SA Iter {iteration}: T={T:.2f}, best_cost={best_cost:.2f}, current_cost={current_cost:.2f}")

        runtime_ms = (time.perf_counter() - start_time) * 1000
        acceptance_rate = accepted / max(total_moves, 1)

        logger.info(
            f"SA complete: best_cost={best_cost:.2f}, makespan={best_makespan:.2f}, "
            f"acceptance_rate={acceptance_rate:.2%}, runtime={runtime_ms:.0f}ms"
        )

        return SaResult(
            assignments=best,
            total_cost=best_cost,
            makespan=best_makespan,
            energy_history=energy_history,
            acceptance_rate=acceptance_rate,
            runtime_ms=runtime_ms,
        )


def run_sa(
    nodes: List[MapNode],
    edges: List[MapEdge],
    tasks: List[AgvTask],
    agvs: List[AgvStatus],
    config: Optional[SaConfig] = None,
) -> SaResult:
    """
    Convenience function to run Simulated Annealing optimization.
    """
    cfg = config or SaConfig()
    optimizer = SimulatedAnnealing(cfg)
    return optimizer.optimize(nodes, edges, tasks, agvs)
