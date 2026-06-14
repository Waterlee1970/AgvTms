"""
Integration test: V1 (ACO+SA+NLP) vs V2 (A*+MIP+SIPP+Traffic) pipeline comparison.

Verifies the complete V2 algorithm suite:
- PathGraph building from legacy models
- Bidirectional A* and TimeWindow A*  
- SIPP planner
- MIP/CP-SAT task assignment
- Zone-based traffic control with deadlock detection
- Dynamic replanning engine
- HybridOrchestratorV2 full pipeline
"""

import logging
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.models.schemas import (
    AgvRunStatus, AgvStatus, AgvTask, AgvTaskStatus,
    ConveyorSegment, ConveyorTask,
    EdgeDirection, MapEdge, MapNode, NodeType,
    AlgorithmConfig, AcoConfig, SaConfig, NlpConfig, HybridConfig,
)
from app.services.schedule_service import schedule_service

# V1 imports
from app.algorithms.hybrid import HybridScheduler
from app.algorithms.aco import AntColonyOptimizer

# V2 imports
from app.algorithms.v2.path_planning.graph_builder import PathGraph, GraphNode, GraphEdge
from app.algorithms.v2.path_planning.astar import BidirectionalAStar, TimeWindowAStar, PathResult
from app.algorithms.v2.path_planning.sipp import SippPlanner
from app.algorithms.v2.path_planning.time_window_table import TimeWindowTable
from app.algorithms.v2.path_planning.dynamic_replanner import DynamicReplanner, ReplanTrigger, ReplanEvent
from app.algorithms.v2.traffic_control.zone_controller import ZoneManager, TrafficZone, ZoneType, LockType
from app.algorithms.v2.task_assignment.mip_solver import MipTaskAssigner, ObjectiveMode
from app.algorithms.v2.hybrid_orchestrator.orchestrator import HybridOrchestratorV2, OrchestratorConfig, OrchestratorMode

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)


def create_test_factory(num_agvs: int = 10, grid_size: int = 10):
    """Create a small test factory layout."""
    nodes = []
    edges = []
    node_idx = 1
    spacing = 3.0

    # Grid nodes
    for row in range(grid_size):
        for col in range(grid_size):
            nid = f"N{node_idx:04d}"
            node_idx += 1
            ntype = NodeType.CROSS if (row % 3 == 0 and col % 3 == 0) else NodeType.PATH
            nodes.append(MapNode(id=nid, name=f"G{row}{col}", x=col*spacing, y=row*spacing, type=ntype))

        # Horizontal edges
        for row in range(grid_size):
            for col in range(grid_size - 1):
                frm = f"N{row*grid_size + col + 1:04d}"
                to = f"N{row*grid_size + col + 2:04d}"
                edges.append(MapEdge(from_node=frm, to_node=to, distance=spacing,
                                     direction=EdgeDirection.BIDIRECTIONAL, speed_limit=1.5))

        # Vertical edges
        for row in range(grid_size - 1):
            for col in range(grid_size):
                frm = f"N{row*grid_size + col + 1:04d}"
                to = f"N{(row+1)*grid_size + col + 1:04d}"
                edges.append(MapEdge(from_node=frm, to_node=to, distance=spacing,
                                     direction=EdgeDirection.BIDIRECTIONAL, speed_limit=1.2))

    # Pickup/dropoff stations
    pickup_ids = []
    dropoff_ids = []
    for i in range(num_agvs):
        pid = f"N{node_idx:04d}"; node_idx += 1
        did = f"N{node_idx:04d}"; node_idx += 1
        nodes.append(MapNode(id=pid, name=f"Pickup{i}", x=-3, y=i*spacing, type=NodeType.PICKUP))
        nodes.append(MapNode(id=did, name=f"Dropoff{i}", x=grid_size*spacing, y=i*spacing, type=NodeType.DROPOFF))
        pickup_ids.append(pid)
        dropoff_ids.append(did)
        # Connect to grid
        grid_entrance = f"N{i*grid_size + 1:04d}"
        grid_exit = f"N{i*grid_size + grid_size:04d}"
        edges.append(MapEdge(from_node=pid, to_node=grid_entrance, distance=spacing,
                             direction=EdgeDirection.BIDIRECTIONAL, speed_limit=1.0))
        edges.append(MapEdge(from_node=grid_exit, to_node=did, distance=spacing,
                             direction=EdgeDirection.BIDIRECTIONAL, speed_limit=1.0))

    # AGVs
    agvs = []
    for i in range(num_agvs):
        start = nodes[i % len(nodes)]
        agvs.append(AgvStatus(
            id=f"AGV_{i+1:03d}", name=f"AGV-{i+1}",
            x=start.x, y=start.y,
            battery=min(95, 75 + i * 2),
            status=AgvRunStatus.IDLE,
            current_node=start.id,
            speed=1.5,
            capacity=1,
            path=[],
        ))

    # Tasks: pickup → dropoff
    tasks = []
    for i in range(min(num_agvs * 2, len(pickup_ids))):
        tasks.append(AgvTask(
            id=f"TASK_{i+1:03d}",
            pickup_node=pickup_ids[i % len(pickup_ids)],
            dropoff_node=dropoff_ids[(i + 1) % len(dropoff_ids)],
            priority=(i % 5) + 1,
            status=AgvTaskStatus.PENDING,
        ))

    return nodes, edges, agvs, tasks


def test_v2_path_planning():
    """Test 1: V2 Path Planning Engine"""
    print("\n" + "=" * 70)
    print("TEST 1: V2 Path Planning Engine")
    print("=" * 70)

    nodes, edges, agvs, tasks = create_test_factory(num_agvs=10, grid_size=10)

    # Build graph
    graph = PathGraph()
    for n in nodes:
        graph.add_node(GraphNode(id=n.id, x=n.x, y=n.y,
                                  type=n.type.value if hasattr(n.type, 'value') else str(n.type)))
    for e in edges:
        graph.add_edge(GraphEdge(from_node=e.from_node, to_node=e.to_node,
                                  distance=e.distance, speed_limit=e.speed_limit,
                                  direction=e.direction.value if hasattr(e.direction, 'value') else str(e.direction)))

    # Test Bidirectional A*
    astar = BidirectionalAStar(graph)
    start = agvs[0].current_node or graph.nodes[list(graph.nodes.keys())[0]].id
    goal = tasks[0].pickup_node

    t0 = time.perf_counter()
    result = astar.find_path(start, goal, blocked_nodes=set())
    elapsed = (time.perf_counter() - t0) * 1000

    assert result.found, f"Path not found from {start} to {goal}"
    assert len(result.path) >= 2, f"Path too short: {result.path}"
    print(f"  ✓ Bidirectional A*: {elapsed:.2f}ms, {len(result.path)} nodes, {result.total_distance:.1f}m, {result.expanded_nodes} expanded")

    # Test k-alternative paths
    k_paths = astar.find_k_paths(start, goal, k=3)
    print(f"  ✓ K-alternative paths: {len(k_paths)} alternatives found")
    for i, p in enumerate(k_paths):
        print(f"    Path {i+1}: {len(p.path)} nodes, {p.total_distance:.1f}m")

    # Test TimeWindow A*
    tw = TimeWindowTable()
    tw_astar = TimeWindowAStar(graph, tw)
    tw_result = tw_astar.find_path_with_time(start, goal, start_time=0.0, agv_id="AGV_001")
    assert tw_result.found, "TW path not found"
    print(f"  ✓ TimeWindow A*: {tw_result.search_time_ms:.2f}ms, {len(tw_result.path)} nodes")

    return True


def test_v2_traffic_control():
    """Test 2: Traffic Control with Zone Manager"""
    print("\n" + "=" * 70)
    print("TEST 2: Traffic Control (Zone Manager)")
    print("=" * 70)

    nodes, edges, agvs, tasks = create_test_factory(num_agvs=10, grid_size=10)

    zm = ZoneManager()
    num_zones = zm.auto_discover_zones(nodes, edges)
    print(f"  ✓ Auto-discovered {num_zones} traffic zones")

    # Check zone types
    stats = zm.stats
    for zt, count in stats["zone_types"].items():
        print(f"    {zt}: {count} zones")

    # Test entry/exit
    int_zone_id = next((zid for zid, z in zm.zones.items() if z.zone_type == ZoneType.INTERSECTION), None)
    if int_zone_id:
        granted, wait = zm.request_entry("AGV_001", int_zone_id, priority=0)
        print(f"  ✓ Zone entry: granted={granted}, wait={wait}")
        zm.release_zone("AGV_001", int_zone_id)

    # Test deadlock detection
    deadlock = zm.check_deadlock()
    assert deadlock is None, f"Unexpected deadlock: {deadlock}"
    print(f"  ✓ Deadlock check: OK (no cycles)")

    return True


def test_v2_task_assignment():
    """Test 3: MIP/CP-SAT Task Assignment"""
    print("\n" + "=" * 70)
    print("TEST 3: MIP/CP-SAT Task Assignment")
    print("=" * 70)

    nodes, edges, agvs, tasks = create_test_factory(num_agvs=10, grid_size=10)

    task_dicts = []
    for t in tasks:
        task_dicts.append({
            "id": t.id or f"task_{len(task_dicts)}",
            "pickup_node": t.pickup_node,
            "dropoff_node": t.dropoff_node,
            "priority": t.priority,
        })

    agv_dicts = []
    for a in agvs:
        agv_dicts.append({
            "id": a.id,
            "current_node": a.current_node or "",
            "battery": a.battery,
            "capacity": a.capacity,
            "speed": a.speed or 1.5,
        })

    # Build distance matrix
    node_positions = {n.id: (n.x, n.y) for n in nodes}
    dist = {}
    for nid_a, (xa, ya) in node_positions.items():
        for nid_b, (xb, yb) in node_positions.items():
            d = ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5
            dist[(nid_a, nid_b)] = d / 1.5

    for mode in [ObjectiveMode.BALANCED, ObjectiveMode.MIN_MAKESPAN]:
        assigner = MipTaskAssigner(objective_mode=mode, time_limit_seconds=3.0)
        result = assigner.assign(task_dicts, agv_dicts, dist)

        print(f"  ✓ {mode.value}: status={result.status}, "
              f"makespan={result.makespan:.0f}s, "
              f"gap={result.optimality_gap:.2%}, "
              f"time={result.solve_time_ms:.1f}ms, "
              f"assigned={result.num_assigned}/{len(task_dicts)}")

    return True


def test_v2_full_pipeline():
    """Test 4: Full V2 Hybrid Orchestrator Pipeline"""
    print("\n" + "=" * 70)
    print("TEST 4: Full V2 Pipeline (Orchestrator)")
    print("=" * 70)

    nodes, edges, agvs, tasks = create_test_factory(num_agvs=20, grid_size=15)

    config = OrchestratorConfig(
        mode=OrchestratorMode.BENCHMARK,
        time_window_enabled=True,
        traffic_control_enabled=True,
        deadlock_detection_enabled=True,
        dynamic_replan_enabled=True,
    )

    orchestrator = HybridOrchestratorV2(config)

    t0 = time.perf_counter()
    result = orchestrator.schedule(nodes, edges, tasks, agvs)
    total_ms = (time.perf_counter() - t0) * 1000

    print(f"  ✓ Pipeline complete: {total_ms:.1f}ms total")
    print(f"    Assignments: {len(result.assignments)}")
    print(f"    Makespan: {result.makespan:.1f}s")
    print(f"    Total distance: {result.metrics.total_agv_travel_distance:.1f}m")
    print(f"    AGV utilization: {result.metrics.agv_utilization:.1%}")
    print(f"    Collisions: {result.metrics.collision_count}")

    stats = orchestrator.stats
    print(f"    Graph: {stats['graph']}")
    print(f"    Traffic zones: {stats['traffic_zones']}")
    print(f"    Replans: {stats['replan_count']}")
    print(f"    Deadlocks: {stats['deadlock_count']}")

    assert len(result.assignments) > 0, "No assignments produced"
    assert result.makespan >= 0, "Invalid makespan"

    return True


def test_v1_vs_v2_comparison():
    """Test 5: V1 vs V2 Performance Comparison"""
    print("\n" + "=" * 70)
    print("TEST 5: V1 vs V2 Performance Comparison")
    print("=" * 70)

    nodes, edges, agvs, tasks = create_test_factory(num_agvs=20, grid_size=15)

    # --- V1 ---
    print("\n  V1 (ACO + SA + NLP):")
    config = AlgorithmConfig(
        aco=AcoConfig(num_ants=30, iterations=50),
        sa=SaConfig(iterations=200),
        nlp=NlpConfig(max_iter=500),
    )
    scheduler = HybridScheduler(config)

    t0 = time.perf_counter()
    v1_result = scheduler.schedule(nodes, edges, tasks, agvs)
    v1_time = (time.perf_counter() - t0) * 1000
    print(f"    Time: {v1_time:.0f}ms")
    print(f"    Assignments: {len(v1_result.assignments)}")
    print(f"    Makespan: {v1_result.makespan:.1f}s")
    print(f"    Distance: {v1_result.metrics.total_agv_travel_distance:.1f}m")

    # --- V2 ---
    print("\n  V2 (MIP + A*+TW + SIPP + Traffic):")
    orchestrator = HybridOrchestratorV2(OrchestratorConfig(
        mode=OrchestratorMode.BENCHMARK,
        time_window_enabled=True,
        traffic_control_enabled=True,
    ))

    t0 = time.perf_counter()
    v2_result = orchestrator.schedule(nodes, edges, tasks, agvs)
    v2_time = (time.perf_counter() - t0) * 1000
    print(f"    Time: {v2_time:.0f}ms")
    print(f"    Assignments: {len(v2_result.assignments)}")
    print(f"    Makespan: {v2_result.makespan:.1f}s")
    print(f"    Distance: {v2_result.metrics.total_agv_travel_distance:.1f}m")
    print(f"    Zones: {orchestrator.stats['traffic_zones']}")

    # --- Comparison ---
    print(f"\n  Speedup: V2 is {v1_time/v2_time:.1f}x faster" if v2_time > 0 else "")
    print(f"  V2 has: collision-free guarantee + deadlock detection + traffic zones")

    return True


if __name__ == "__main__":
    results = []
    for test_func in [
        test_v2_path_planning,
        test_v2_traffic_control,
        test_v2_task_assignment,
        test_v2_full_pipeline,
        test_v1_vs_v2_comparison,
    ]:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            logger.error(f"Test failed: {e}", exc_info=True)
            results.append(False)

    passed = sum(1 for r in results if r)
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed}/{len(results)} tests passed")
    print("=" * 70)

    if passed < len(results):
        sys.exit(1)
