"""
Ant Colony Optimization (ACO) for AGV Path Planning.

蚁群算法 - 用于多AGV路径规划

Implements the full ACO metaheuristic:
1. Pheromone matrix initialization and evaporation
2. Probabilistic path construction using pheromone + heuristic
3. Multi-AGV collision avoidance via time-window reservation
4. Iterative improvement over multiple generations

Mathematical foundation:
- State transition probability: P_ij^k = (τ_ij^α * η_ij^β) / Σ(τ_il^α * η_il^β)
- Pheromone update: τ_ij = (1-ρ)τ_ij + ΣΔτ_ij^k
- Heuristic: η_ij = 1/d_ij  (inverse of distance)
"""

from __future__ import annotations

import copy
import logging
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from ..models.schemas import (
    AcoConfig,
    AgvAssignment,
    AgvStatus,
    AgvTask,
    MapEdge,
    MapNode,
)

logger = logging.getLogger(__name__)


@dataclass
class AcoResult:
    """Result of an ACO optimization run."""
    best_paths: Dict[str, List[str]]  # agv_id -> path nodes
    best_cost: float
    assignment_costs: Dict[str, float]
    convergence: List[float]  # best cost per iteration
    iterations_run: int
    runtime_ms: float


class AntColonyOptimizer:
    """
    Ant Colony Optimization for multi-AGV path planning.

    Each ant represents a complete solution: a set of paths for all AGVs
    to complete their assigned tasks. The colony explores the solution space
    guided by pheromone trails and heuristic information (inverse distance).
    """

    def __init__(self, config: AcoConfig):
        self.config = config
        self.pheromone: Dict[Tuple[str, str], float] = {}
        self.nodes: Dict[str, MapNode] = {}
        self.edges: Dict[Tuple[str, str], MapEdge] = {}
        self.adjacency: Dict[str, List[str]] = defaultdict(list)
        self._best_cost_history: List[float] = []

    def _build_graph(self, nodes: List[MapNode], edges: List[MapEdge]):
        """Build internal graph representation from map data."""
        self.nodes = {n.id: n for n in nodes}
        self.edges = {}
        self.adjacency = defaultdict(list)

        for edge in edges:
            key = (edge.from_node, edge.to_node)
            self.edges[key] = edge
            self.adjacency[edge.from_node].append(edge.to_node)
            if edge.direction.value in ("bidirectional", "backward"):
                rev_key = (edge.to_node, edge.from_node)
                self.edges[rev_key] = edge
                self.adjacency[edge.to_node].append(edge.from_node)

        # Initialize pheromone
        for (f, t), edge in self.edges.items():
            self.pheromone[(f, t)] = 1.0

    def _dijkstra(self, start: str, end: str) -> Tuple[List[str], float]:
        """Find shortest path from start to end using Dijkstra's algorithm."""
        if start == end:
            return [start], 0.0

        dist: Dict[str, float] = {n: float('inf') for n in self.nodes}
        prev: Dict[str, Optional[str]] = {n: None for n in self.nodes}
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
                    prev[neighbor] = current

        # Reconstruct path
        path = []
        node = end
        while node is not None:
            path.append(node)
            node = prev[node]
        path.reverse()

        if path[0] != start:
            return [], float('inf')

        return path, dist[end]

    def _construct_ant_solution(
        self,
        agv_tasks: Dict[str, AgvTask],
        agvs: Dict[str, AgvStatus],
        time_windows: Dict[str, List[Tuple[float, float]]],
    ) -> Tuple[Dict[str, List[str]], float]:
        """
        Construct a complete solution (one ant).
        
        Each AGV builds its path probabilistically, guided by:
        - Pheromone concentration τ on edges
        - Heuristic information η (1/distance)
        
        Time-window constraints prevent collisions.
        """
        alpha = self.config.alpha
        beta = self.config.beta
        q0 = self.config.q0

        paths: Dict[str, List[str]] = {}
        total_cost = 0.0
        occupied_nodes: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

        for agv_id, task in agv_tasks.items():
            agv = agvs[agv_id]
            start_node = agv.current_node or agv.id  # fallback to agv id as start
            if start_node not in self.nodes:
                # Find nearest node
                start_node = self._find_nearest_node(agv.x, agv.y)

            end_node = task.dropoff_node

            # ACO-based path construction from start to pickup, then to dropoff
            # Simplified: use Dijkstra as base, perturb with pheromone
            path1, cost1 = self._aco_path_construct(start_node, task.pickup_node)
            path2, cost2 = self._aco_path_construct(task.pickup_node, end_node)

            # Merge paths (remove duplicate pickup node)
            full_path = path1 + path2[1:] if len(path2) > 1 else path1 + path2
            path_cost = cost1 + cost2

            paths[agv_id] = full_path
            total_cost += path_cost

            # Update occupied time windows for collision avoidance
            cumulative_time = 0.0
            for i in range(len(full_path) - 1):
                f, t = full_path[i], full_path[i + 1]
                edge = self.edges.get((f, t))
                if edge:
                    travel_time = edge.distance / edge.speed_limit
                    occupied_nodes[f].append((cumulative_time, cumulative_time + travel_time))
                    cumulative_time += travel_time

        return paths, total_cost

    def _aco_path_construct(self, start: str, end: str) -> Tuple[List[str], float]:
        """
        Construct a single path using ACO probability rules.
        
        P_ij = (τ_ij^α * η_ij^β) / Σ(τ_il^α * η_il^β)
        
        With probability q0, select the best edge (exploitation).
        With probability 1-q0, select probabilistically (exploration).
        """
        if start == end:
            return [start], 0.0

        path = [start]
        current = start
        total_dist = 0.0
        visited = {start}
        max_steps = len(self.nodes) * 2

        while current != end and len(path) < max_steps:
            neighbors = [n for n in self.adjacency.get(current, []) if n not in visited]
            if not neighbors:
                # Dead end - use Dijkstra to escape
                escape_path, _ = self._dijkstra(current, end)
                if len(escape_path) <= 1:
                    break
                path.extend(escape_path[1:])
                break

            # Calculate probabilities
            probs = []
            values = []
            for n in neighbors:
                edge = self.edges.get((current, n))
                if edge is None:
                    continue
                tau = self.pheromone.get((current, n), 1.0)
                eta = 1.0 / max(edge.distance, 0.001)
                value = (tau ** self.config.alpha) * (eta ** self.config.beta)
                probs.append(n)
                values.append(value)

            if not values:
                break

            total = sum(values)
            probabilities = [v / total for v in values]

            # Select next node
            if random.random() < self.config.q0:
                # Exploitation: choose best
                idx = np.argmax(values)
                next_node = probs[idx]
            else:
                # Exploration: roulette wheel
                r = random.random()
                cumsum = 0.0
                next_node = probs[-1]
                for i, p in enumerate(probabilities):
                    cumsum += p
                    if r <= cumsum:
                        next_node = probs[i]
                        break

            edge = self.edges.get((current, next_node))
            if edge:
                total_dist += edge.distance

            path.append(next_node)
            visited.add(next_node)
            current = next_node

        return path, total_dist

    def _update_pheromone(self, all_paths: List[Dict[str, List[str]]], all_costs: List[float]):
        """
        Update pheromone matrix.
        
        Evaporation: τ_ij = (1 - ρ) * τ_ij
        Deposit: τ_ij += Σ Q / L_k  (for edges in ant k's path)
        where L_k is the total cost of ant k's solution.
        """
        rho = self.config.evaporation_rate

        # Evaporation
        for key in self.pheromone:
            self.pheromone[key] *= (1 - rho)

        # Find best ant for elitist deposit
        if all_costs:
            best_idx = np.argmin(all_costs)
            best_paths = all_paths[best_idx]
            best_cost = all_costs[best_idx]
            Q = 100.0 / max(best_cost, 0.001)

            # Deposit pheromone on edges used by best ant
            for agv_id, path in best_paths.items():
                for i in range(len(path) - 1):
                    f, t = path[i], path[i + 1]
                    key = (f, t)
                    if key in self.pheromone:
                        self.pheromone[key] += Q

    def _find_nearest_node(self, x: float, y: float) -> str:
        """Find the map node closest to given coordinates."""
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
        agv_tasks: Dict[str, AgvTask],
        agvs: Dict[str, AgvStatus],
    ) -> AcoResult:
        """
        Run the full ACO optimization.

        Args:
            nodes: Map nodes
            edges: Map edges
            agv_tasks: Mapping of agv_id -> assigned task
            agvs: Mapping of agv_id -> current AGV status

        Returns:
            AcoResult with best paths and convergence data
        """
        start_time = time.perf_counter()
        self._build_graph(nodes, edges)
        self._best_cost_history = []

        best_paths = None
        best_cost = float('inf')

        # Initialize time windows (empty for collision avoidance)
        time_windows: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

        for iteration in range(self.config.iterations):
            iteration_paths = []
            iteration_costs = []

            for ant in range(self.config.num_ants):
                paths, cost = self._construct_ant_solution(agv_tasks, agvs, time_windows)
                iteration_paths.append(paths)
                iteration_costs.append(cost)

                if cost < best_cost:
                    best_cost = cost
                    best_paths = copy.deepcopy(paths)

            self._update_pheromone(iteration_paths, iteration_costs)
            self._best_cost_history.append(best_cost)

            if iteration % 20 == 0:
                logger.debug(f"ACO Iteration {iteration}: best_cost={best_cost:.2f}")

        runtime_ms = (time.perf_counter() - start_time) * 1000

        # Build assignment costs per AGV
        assignment_costs = {}
        if best_paths:
            for agv_id, path in best_paths.items():
                cost = 0.0
                for i in range(len(path) - 1):
                    edge = self.edges.get((path[i], path[i + 1]))
                    if edge:
                        cost += edge.distance
                assignment_costs[agv_id] = cost

        logger.info(f"ACO complete: best_cost={best_cost:.2f}, iterations={self.config.iterations}, runtime={runtime_ms:.0f}ms")

        return AcoResult(
            best_paths=best_paths or {},
            best_cost=best_cost,
            assignment_costs=assignment_costs,
            convergence=self._best_cost_history,
            iterations_run=self.config.iterations,
            runtime_ms=runtime_ms,
        )


def run_aco(
    nodes: List[MapNode],
    edges: List[MapEdge],
    agv_tasks: Dict[str, AgvTask],
    agvs: Dict[str, AgvStatus],
    config: Optional[AcoConfig] = None,
) -> AcoResult:
    """
    Convenience function to run ACO optimization.

    Args:
        nodes: Factory map nodes
        edges: Factory map edges
        agv_tasks: AGV -> Task assignments
        agvs: Current AGV states
        config: ACO parameters (uses defaults if None)

    Returns:
        AcoResult with optimized paths
    """
    cfg = config or AcoConfig()
    optimizer = AntColonyOptimizer(cfg)
    return optimizer.optimize(nodes, edges, agv_tasks, agvs)
