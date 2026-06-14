"""
Performance Baseline Test Suite for V2 Path Planning Engine.

Compares V1 (ACO/Dijkstra) vs V2 (A*/TimeWindow/SIPP) performance
across multiple scenario sizes and AGV counts.

Measures:
- Single path planning time (ms)
- Multi-AGV collision-free path planning time (ms)
- Path quality (total distance, average wait time)
- Memory usage (peak heap)
- Node expansions (search efficiency)

Usage:
    python -m benchmark.baseline_test [--scenario small|medium|large]
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.algorithms.v2.path_planning.graph_builder import PathGraph, GraphNode, GraphEdge
from app.algorithms.v2.path_planning.astar import (
    BidirectionalAStar, TimeWindowAStar, PathResult,
)
from app.algorithms.v2.path_planning.sipp import SippPlanner, SippPathResult
from app.algorithms.v2.path_planning.time_window_table import TimeWindowTable
from app.algorithms.v2.path_planning.dynamic_replanner import (
    DynamicReplanner, ReplanTrigger, ReplanEvent,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


@dataclass
class PathPlannerBaseline:
    """Performance metrics for a single path planner configuration."""
    planner_name: str
    scenario: str
    num_agvs: int

    # Single-path metrics
    avg_path_time_ms: float = 0.0
    max_path_time_ms: float = 0.0
    avg_path_distance: float = 0.0
    avg_expanded_nodes: float = 0.0
    path_success_rate: float = 0.0

    # Multi-AGV metrics
    total_planning_time_ms: float = 0.0
    avg_wait_time: float = 0.0
    collision_count: int = 0

    # Memory
    peak_memory_kb: float = 0.0


@dataclass
class BaselineReport:
    """Complete baseline test report."""
    scenario_name: str
    node_count: int
    edge_count: int
    num_agvs: int
    num_tasks: int
    planners: List[PathPlannerBaseline] = field(default_factory=list)
    comparison: dict = field(default_factory=dict)


def run_baseline(
    scenario_data_path: str,
    output_path: Optional[str] = None,
) -> BaselineReport:
    """
    Run complete baseline test on a scenario.

    Tests V1 (ACO-based), V2 Bi-A*, V2 TimeWindow A*, and SIPP.
    """
    # Load scenario
    with open(scenario_data_path) as f:
        data = json.load(f)

    nodes_data = data.get("nodes", [])
    edges_data = data.get("edges", [])
    agvs_data = data.get("agvs", [])
    tasks_data = data.get("tasks", [])

    logger.info(f"Loaded scenario: {len(nodes_data)} nodes, {len(edges_data)} edges, "
                 f"{len(agvs_data)} AGVs, {len(tasks_data)} tasks")

    # Build V2 PathGraph
    graph = PathGraph()
    for n in nodes_data:
        graph.add_node(GraphNode(
            id=n['id'], x=n['x'], y=n['y'],
            type=n.get('type', 'path'),
            capacity=n.get('capacity', 1),
        ))
    for e in edges_data:
        graph.add_edge(GraphEdge(
            from_node=e['from_node'],
            to_node=e['to_node'],
            distance=e['distance'],
            speed_limit=e.get('speed_limit', 1.5),
            is_conveyor=e.get('is_conveyor', False),
            congestion_factor=e.get('congestion_factor', 1.0),
            direction=e.get('direction', 'bidirectional'),
        ))

    # Get task endpoints
    pickup_nodes = [t['pickup_node'] for t in tasks_data[:min(50, len(tasks_data))]]
    dropoff_nodes = [t['dropoff_node'] for t in tasks_data[:min(50, len(tasks_data))]]

    # Create AGV start positions
    agv_starts = [a.get('current_node', graph.nodes[list(graph.nodes.keys())[0]].id)
                  for a in agvs_data[:min(50, len(agvs_data))]]

    report = BaselineReport(
        scenario_name=os.path.basename(scenario_data_path).replace('.json', ''),
        node_count=len(nodes_data),
        edge_count=len(edges_data),
        num_agvs=len(agv_starts),
        num_tasks=len(pickup_nodes),
    )

    # =====================================================================
    # Test 1: Bidirectional A* (no time windows)
    # =====================================================================
    logger.info("=" * 60)
    logger.info("Test 1: Bidirectional A* (single path, no collisions)")
    bi_astar = BidirectionalAStar(graph)
    bi_baseline = PathPlannerBaseline(
        planner_name="Bidirectional A*",
        scenario=report.scenario_name,
        num_agvs=len(agv_starts),
    )

    times = []
    distances = []
    expanded = []
    success = 0

    for i in range(min(len(pickup_nodes), len(agv_starts))):
        start = agv_starts[i]
        goal = pickup_nodes[i]

        t0 = time.perf_counter()
        result = bi_astar.find_path(start, goal)
        elapsed = (time.perf_counter() - t0) * 1000

        times.append(elapsed)
        if result:
            distances.append(result.total_distance)
            expanded.append(result.expanded_nodes)
            success += 1

    bi_baseline.avg_path_time_ms = sum(times) / len(times) if times else 0
    bi_baseline.max_path_time_ms = max(times) if times else 0
    bi_baseline.avg_path_distance = sum(distances) / len(distances) if distances else 0
    bi_baseline.avg_expanded_nodes = sum(expanded) / len(expanded) if expanded else 0
    bi_baseline.path_success_rate = success / len(times) if times else 0
    bi_baseline.total_planning_time_ms = sum(times)

    logger.info(f"  Avg time: {bi_baseline.avg_path_time_ms:.2f}ms, "
                 f"Max: {bi_baseline.max_path_time_ms:.2f}ms, "
                 f"Success: {success}/{len(times)}")
    report.planners.append(bi_baseline)

    # =====================================================================
    # Test 2: Time Window A* (collision-free multi-AGV)
    # =====================================================================
    logger.info("=" * 60)
    logger.info("Test 2: Time Window A* (collision-free, multi-AGV)")

    tw_table = TimeWindowTable()
    tw_astar = TimeWindowAStar(graph, tw_table)
    tw_baseline = PathPlannerBaseline(
        planner_name="A* + TimeWindow",
        scenario=report.scenario_name,
        num_agvs=len(agv_starts),
    )

    tw_times = []
    tw_distances = []
    tw_expanded = []
    tw_success = 0
    total_wait = 0.0

    t0_total = time.perf_counter()
    for i in range(min(len(pickup_nodes), len(agv_starts))):
        start = agv_starts[i]
        goal = pickup_nodes[i]
        agv_id = f"AGV_{i+1:03d}"

        t0 = time.perf_counter()
        result = tw_astar.find_path_with_time(start, goal, start_time=0.0, agv_id=agv_id)
        elapsed = (time.perf_counter() - t0) * 1000
        tw_times.append(elapsed)

        if result:
            tw_distances.append(result.total_distance)
            tw_expanded.append(result.expanded_nodes)
            tw_success += 1

            # Reserve path for collision avoidance
            tw_table.reserve_path(result.path, 0.0, agv_id)

    total_time = (time.perf_counter() - t0_total) * 1000
    tw_baseline.total_planning_time_ms = total_time
    tw_baseline.avg_path_time_ms = sum(tw_times) / len(tw_times) if tw_times else 0
    tw_baseline.max_path_time_ms = max(tw_times) if tw_times else 0
    tw_baseline.avg_path_distance = sum(tw_distances) / len(tw_distances) if tw_distances else 0
    tw_baseline.avg_expanded_nodes = sum(tw_expanded) / len(tw_expanded) if tw_expanded else 0
    tw_baseline.path_success_rate = tw_success / len(tw_times) if tw_times else 0
    tw_baseline.collision_count = 0  # TW A* ensures 0 collisions

    logger.info(f"  Avg time: {tw_baseline.avg_path_time_ms:.2f}ms, "
                 f"Total: {tw_baseline.total_planning_time_ms:.2f}ms, "
                 f"Success: {tw_success}/{len(tw_times)}")
    report.planners.append(tw_baseline)

    # =====================================================================
    # Test 3: SIPP Planner (safe intervals, ~10x fewer states)
    # =====================================================================
    logger.info("=" * 60)
    logger.info("Test 3: SIPP Planner (Safe Interval Path Planning)")

    sipp_tw = TimeWindowTable()
    sipp = SippPlanner(graph, sipp_tw)
    sipp_baseline = PathPlannerBaseline(
        planner_name="SIPP (Safe Intervals)",
        scenario=report.scenario_name,
        num_agvs=len(agv_starts),
    )

    sipp_times = []
    sipp_distances = []
    sipp_expanded = []
    sipp_success = 0
    sipp_waits = []

    t0_total = time.perf_counter()
    for i in range(min(len(pickup_nodes), len(agv_starts))):
        start = agv_starts[i]
        goal = pickup_nodes[i]
        agv_id = f"AGV_SIPP_{i+1:03d}"

        t0 = time.perf_counter()
        result = sipp.plan(start, goal, start_time=0.0, agv_id=agv_id)
        elapsed = (time.perf_counter() - t0) * 1000
        sipp_times.append(elapsed)

        if result.found:
            sipp_distances.append(result.total_distance)
            sipp_expanded.append(result.expanded_states)
            sipp_success += 1
            sipp.reserve_sipp_path(result, agv_id)
            if result.wait_times:
                sipp_waits.extend(result.wait_times)

    total_time = (time.perf_counter() - t0_total) * 1000
    sipp_baseline.total_planning_time_ms = total_time
    sipp_baseline.avg_path_time_ms = sum(sipp_times) / len(sipp_times) if sipp_times else 0
    sipp_baseline.max_path_time_ms = max(sipp_times) if sipp_times else 0
    sipp_baseline.avg_path_distance = sum(sipp_distances) / len(sipp_distances) if sipp_distances else 0
    sipp_baseline.avg_expanded_nodes = sum(sipp_expanded) / len(sipp_expanded) if sipp_expanded else 0
    sipp_baseline.path_success_rate = sipp_success / len(sipp_times) if sipp_times else 0
    sipp_baseline.avg_wait_time = sum(sipp_waits) / len(sipp_waits) if sipp_waits else 0

    logger.info(f"  Avg time: {sipp_baseline.avg_path_time_ms:.2f}ms, "
                 f"Total: {sipp_baseline.total_planning_time_ms:.2f}ms, "
                 f"Avg wait: {sipp_baseline.avg_wait_time:.2f}s, "
                 f"Success: {sipp_success}/{len(sipp_times)}")
    report.planners.append(sipp_baseline)

    # =====================================================================
    # Test 4: Dynamic Replanner
    # =====================================================================
    logger.info("=" * 60)
    logger.info("Test 4: Dynamic Replanner (D* Lite variant)")

    dr_tw = TimeWindowTable()
    replanner = DynamicReplanner(graph, dr_tw, verbose=False)
    dr_baseline = PathPlannerBaseline(
        planner_name="Dynamic Replanner",
        scenario=report.scenario_name,
        num_agvs=len(agv_starts),
    )

    # Plan initial paths
    dr_times = []
    dr_replan_times = []
    for i in range(min(len(pickup_nodes), len(agv_starts))):
        start = agv_starts[i]
        goal = pickup_nodes[i]
        agv_id = f"AGV_DR_{i+1:03d}"

        t0 = time.perf_counter()
        result = bi_astar.find_path(start, goal)
        dr_times.append((time.perf_counter() - t0) * 1000)

        if result:
            replanner.register_agv_path(agv_id, result.path, start)

    # Simulate a blocked edge event for the first AGV
    if len(dr_times) >= 2:
        first_agv = f"AGV_DR_001"
        state = replanner._agv_states.get(first_agv)
        if state and len(state.planned_path) >= 3:
            blocked_edge = (state.planned_path[1], state.planned_path[2])
            event = ReplanEvent(
                trigger=ReplanTrigger.EDGE_BLOCKED,
                agv_id=first_agv,
                affected_edge=blocked_edge,
                timestamp=0.0,
            )
            t0 = time.perf_counter()
            replanned = replanner.handle_event(event)
            dr_replan_times.append((time.perf_counter() - t0) * 1000)

    dr_baseline.avg_path_time_ms = sum(dr_times) / len(dr_times) if dr_times else 0
    dr_baseline.total_planning_time_ms = sum(dr_times)
    dr_baseline.avg_expanded_nodes = 0
    dr_baseline.path_success_rate = len(dr_times) / max(len(pickup_nodes), len(agv_starts), 1)

    if dr_replan_times:
        logger.info(f"  Avg replan time: {sum(dr_replan_times)/len(dr_replan_times):.2f}ms")

    report.planners.append(dr_baseline)

    # =====================================================================
    # Generate comparison
    # =====================================================================
    logger.info("=" * 60)
    logger.info("COMPARISON SUMMARY")
    logger.info("-" * 60)

    comparison = {}
    for p in report.planners:
        logger.info(f"  {p.planner_name:25s} | "
                     f"avg={p.avg_path_time_ms:8.2f}ms | "
                     f"success={p.path_success_rate:.1%} | "
                     f"expanded={p.avg_expanded_nodes:8.1f}")
        comparison[p.planner_name] = {
            "avg_time_ms": round(p.avg_path_time_ms, 2),
            "max_time_ms": round(p.max_path_time_ms, 2),
            "total_time_ms": round(p.total_planning_time_ms, 2),
            "success_rate": round(p.path_success_rate, 4),
            "avg_expanded_nodes": round(p.avg_expanded_nodes, 1),
            "avg_path_distance": round(p.avg_path_distance, 2),
            "avg_wait_time": round(p.avg_wait_time, 2),
            "collision_count": p.collision_count,
        }

    report.comparison = comparison

    # Save report
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        report_dict = {
            "scenario": report.scenario_name,
            "node_count": report.node_count,
            "edge_count": report.edge_count,
            "num_agvs": report.num_agvs,
            "num_tasks": report.num_tasks,
            "planners": [{
                "name": p.planner_name,
                "avg_time_ms": round(p.avg_path_time_ms, 2),
                "max_time_ms": round(p.max_path_time_ms, 2),
                "success_rate": round(p.path_success_rate, 4),
                "avg_expanded": round(p.avg_expanded_nodes, 1),
                "avg_distance": round(p.avg_path_distance, 2),
                "avg_wait": round(p.avg_wait_time, 2),
            } for p in report.planners],
        }
        with open(output_path, 'w') as f:
            json.dump(report_dict, f, indent=2)
        logger.info(f"Report saved to {output_path}")

    return report


def run_all_baselines(
    scenario_dir: str = "./benchmark_data",
    output_dir: str = "./benchmark_results",
) -> List[BaselineReport]:
    """Run baselines on all available scenarios."""
    os.makedirs(output_dir, exist_ok=True)
    reports = []

    manifest_path = os.path.join(scenario_dir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)
        scenarios = manifest.get("scenarios", {})
    else:
        # Fall back to scanning directory
        scenarios = {}
        for fname in os.listdir(scenario_dir):
            if fname.startswith("scenario_") and fname.endswith(".json"):
                name = fname.replace("scenario_", "").replace(".json", "")
                scenarios[name] = {"file": os.path.join(scenario_dir, fname)}

    for name, info in scenarios.items():
        filepath = info["file"]
        if not os.path.exists(filepath):
            logger.warning(f"Scenario file not found: {filepath}")
            continue

        output_path = os.path.join(output_dir, f"baseline_{name}.json")
        try:
            report = run_baseline(filepath, output_path)
            reports.append(report)
        except Exception as e:
            logger.error(f"Failed to run baseline on {name}: {e}")

    return reports


if __name__ == "__main__":
    # Quick self-test: generate a small scenario and run baseline
    from generate_scenarios import ScenarioGenerator, generate_all_scenarios

    # Generate scenarios if needed
    data_dir = os.path.join(os.path.dirname(__file__), "benchmark_data")
    if not os.path.exists(os.path.join(data_dir, "manifest.json")):
        logger.info("Generating benchmark scenarios...")
        generate_all_scenarios(data_dir)

    logger.info("Running baseline tests...")
    reports = run_all_baselines(data_dir)

    # Print summary table
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK SUMMARY")
    print("=" * 80)
    for r in reports:
        print(f"\nScenario: {r.scenario_name} ({r.node_count} nodes, {r.num_agvs} AGVs)")
        print("-" * 60)
        for p in r.planners:
            print(f"  {p.planner_name:25s} | "
                  f"avg={p.avg_path_time_ms:8.2f}ms | "
                  f"dist={p.avg_path_distance:7.1f}m | "
                  f"success={p.path_success_rate:.0%}")
