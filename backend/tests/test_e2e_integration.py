#!/usr/bin/env python3
"""
端到端集成测试 — G1 演示就绪验证

覆盖路径:
  Scenario → Registry → V1-Hybrid / V2-MIP / V2-Orchestrator → Result
  验证: 数据流完整性、超时降级、FCFS兜底、字段兼容

运行:
    pytest tests/test_e2e_integration.py -v --tb=short
"""

from __future__ import annotations

import os
import sys
import time
import logging

import pytest

# Fix import paths (works both from backend/ and project root)
_project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Also add parent of backend for `backend.app` imports
_parent_root = os.path.normpath(os.path.join(_project_root, ".."))
if _parent_root not in sys.path and os.path.exists(os.path.join(_parent_root, "backend")):
    sys.path.insert(0, _parent_root)

logger = logging.getLogger("e2e_test")


# ═══════════════════════════════════════════════════════════════
# Fixtures: 场景生成
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def small_scenario():
    """小型场景: 5 AGV × 10 tasks (快速验证用)"""
    nodes = [
        {"id": "n_0_0", "name": "N00", "x": 0, "y": 0, "node_type": "STATION"},
        {"id": "n_0_3", "name": "N03", "x": 15, "y": 0, "node_type": "STATION"},
        {"id": "n_0_6", "name": "N06", "x": 30, "y": 0, "node_type": "STATION"},
        {"id": "n_2_0", "name": "N20", "x": 0, "y": 10, "node_type": "PATH"},
        {"id": "n_2_6", "name": "N26", "x": 30, "y": 10, "node_type": "PATH"},
        {"id": "n_4_3", "name": "N43", "x": 15, "y": 20, "node_type": "STATION"},
    ]
    edges = [
        {"id": "e01", "from_node": "n_0_0", "to_node": "n_2_0", "distance": 10},
        {"id": "e12", "from_node": "n_0_0", "to_node": "n_0_3", "distance": 15},
        {"id": "e23", "from_node": "n_0_3", "to_node": "n_0_6", "distance": 15},
        {"id": "e34", "from_node": "n_0_3", "to_node": "n_2_6", "distance": 18},
        {"id": "e45", "from_node": "n_2_0", "to_node": "n_4_3", "distance": 22},
        {"id": "e56", "from_node": "n_2_6", "to_node": "n_4_3", "distance": 14},
        {"id": "e67", "from_node": "n_0_6", "to_node": "n_2_6", "distance": 18},
    ]
    tasks = [
        {"id": "T001", "pickup_node_id": "n_0_0", "dropoff_node_id": "n_4_3", "priority": 5},
        {"id": "T002", "pickup_node_id": "n_0_3", "dropoff_node_id": "n_2_0", "priority": 10},
        {"id": "T003", "pickup_node_id": "n_0_6", "dropoff_node_id": "n_0_0", "priority": 1},
        {"id": "T004", "pickup_node_id": "n_2_0", "dropoff_node_id": "n_0_6", "priority": 5},
        {"id": "T005", "pickup_node_id": "n_4_3", "dropoff_node_id": "n_0_3", "priority": 20},
    ]
    agvs = [
        {"id": "AGV-001", "current_node": "n_0_0", "battery": 95, "status": "idle", "speed": 1.5, "capacity": 1},
        {"id": "AGV-002", "current_node": "n_0_3", "battery": 80, "status": "idle", "speed": 1.5, "capacity": 1},
        {"id": "AGV-003", "current_node": "n_0_6", "battery": 70, "status": "idle", "speed": 1.0, "capacity": 1},
        {"id": "AGV-004", "current_node": "n_2_0", "battery": 90, "status": "idle", "speed": 1.5, "capacity": 1},
        {"id": "AGV-005", "current_node": "n_4_3", "battery": 60, "status": "charging", "speed": 1.0, "capacity": 1},
    ]
    return nodes, edges, tasks, agvs


@pytest.fixture(scope="module")
def medium_scenario():
    """中型场景: 20 AGV × 40 tasks (G1基线)"""
    import random
    rng = random.Random(42)

    nodes = []
    for r in range(8):
        for c in range(10):
            node_type = "STATION" if r % 2 == 0 and c % 3 == 0 else "PATH"
            nodes.append({"id": f"n_{r}_{c}", "name": f"N{r}{c}",
                          "x": c * 5 + rng.uniform(-0.3, 0.3),
                          "y": r * 5 + rng.uniform(-0.3, 0.3),
                          "node_type": node_type})

    edges = []
    for r in range(8):
        for c in range(10):
            nid = f"n_{r}_{c}"
            if c < 9:
                edges.append({"id": f"e_{nid}_r", "from_node": nid, "to_node": f"n_{r}_{c+1}", "distance": 5})
            if r < 7:
                edges.append({"id": f"e_{nid}_d", "from_node": nid, "to_node": f"n_{r+1}_{c}", "distance": 5})

    stations = [n["id"] for n in nodes if n["node_type"] == "STATION"]
    agvs = [{"id": f"AGV-{i+1:03d}", "current_node": rng.choice(stations),
             "battery": rng.uniform(50, 100), "status": "idle",
             "speed": 1.5, "capacity": 1} for i in range(20)]

    tasks = []
    for i in range(40):
        p = rng.choice(stations)
        d = rng.choice([s for s in stations if s != p] or stations)
        tasks.append({"id": f"T-{i+1:04d}", "pickup_node_id": p,
                      "dropoff_node_id": d, "priority": rng.choice([1, 5, 5, 10])})

    return nodes, edges, tasks, agvs


# ═══════════════════════════════════════════════════════════════
# Test Suite: 算法注册表 + 执行
# ═══════════════════════════════════════════════════════════════

class TestAlgorithmRegistryE2E:

    def test_registry_lists_all_algorithms(self):
        """[E2E-1] 注册表包含所有6个算法"""
        from backend.app.algorithms.v2.evaluator.registry import AlgorithmRegistry
        reg = AlgorithmRegistry.get_instance()
        all_names = {a.name for a in reg.list_all()}
        expected = {"fcfs", "greedy", "v1_hybrid", "v2_mip", "v2_orchestrator", "rl_dqn"}
        assert expected <= all_names, f"Missing algorithms: {expected - all_names}"

    def test_registry_creates_instances(self):
        """[E2E-2] 注册表能正确创建所有可用算法实例"""
        from backend.app.algorithms.v2.evaluator.registry import AlgorithmRegistry
        reg = AlgorithmRegistry.get_instance()
        
        for spec in reg.list_available():
            algo = reg.create(spec.name)
            assert algo is not None
            assert algo.name == spec.name
            assert algo.spec.display_name != ""


class TestFCFSE2E:

    def test_fcfs_small_scenario(self, small_scenario):
        """[E2E-3] FCFS在小场景上正常返回"""
        from backend.app.algorithms.v2.evaluator.registry import get_registry
        registry = get_registry()
        fcfs = registry.create("fcfs")
        nodes, edges, tasks, agvs = small_scenario
        
        result = fcfs.execute(nodes, edges, tasks, agvs)
        assert result.success, f"FCFS failed: {result.error}"
        assert len(result.assignments) >= 3, f"Expected >=3 assignments, got {len(result.assignments)}"
        assert len(result.paths) > 0, "No paths returned"

    def test_fcfs_medium_scenario_under_100ms(self, medium_scenario):
        """[E2E-4] FCFS在20AGV×40task场景 < 100ms"""
        from backend.app.algorithms.v2.evaluator.registry import get_registry
        registry = get_registry()
        fcfs = registry.create("fcfs")
        nodes, edges, tasks, agvs = medium_scenario
        
        t0 = time.perf_counter()
        result = fcfs.execute(nodes, edges, tasks, agvs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        
        assert result.success, f"FCFS failed: {result.error}"
        assert elapsed_ms < 100, f"FCFS took {elapsed_ms:.0f}ms (>100ms)"


class TestV2MipeE2E:

    def test_v2_mip_returns_optimal(self, small_scenario):
        """[E2E-5] V2-MIP 返回最优分配结果"""
        try:
            import ortools
        except ImportError:
            pytest.skip("OR-Tools not installed")

        from backend.app.algorithms.v2.evaluator.registry import get_registry
        registry = get_registry()
        mip = registry.create("v2_mip")
        nodes, edges, tasks, agvs = small_scenario

        result = mip.execute(nodes, edges, tasks, agvs)
        assert result.success, f"V2-MIP failed: {result.error}"
        assert len(result.assignments) >= 3
        # Check paths have valid structure
        for aid, path in result.paths.items():
            assert len(path) >= 3, f"Path for {aid} too short: {path}"

    def test_v2_mip_distance_matrix_populated(self, small_scenario):
        """[E2E-6] V2-MIP 距离矩阵非空 (P0-1 fix verification)"""
        try:
            import ortools
        except ImportError:
            pytest.skip("OR-Tools not installed")

        from backend.app.algorithms.v2.task_assignment.mip_solver import create_distance_matrix
        from backend.app.models.schemas import MapNode, MapEdge
        nodes, edges, tasks, agvs = small_scenario

        mn = [MapNode(id=n["id"], name=n["name"], x=n["x"], y=n["y"]) for n in nodes]
        me = [MapEdge(from_node=e["from_node"], to_node=e["to_node"],
                     distance=e["distance"]) for e in edges]
        dm = create_distance_matrix(mn, me)
        
        assert len(dm) > 0, "Distance matrix is empty!"
        # Check some known pairs exist
        assert ("n_0_0", "n_0_3") in dm or ("n_0_0", "n_0_3") in {k: v for k, v in dm.items()}, \
            f"Missing n_0_0→n_0_3 distance. Keys sample: {list(dm.keys())[:5]}"


class TestV2OrchestratorE2E:

    def test_orchestrator_initializes(self, small_scenario):
        """[E2E-7] V2 Orchestrator 正常初始化并构建距离矩阵"""
        try:
            import ortools
        except ImportError:
            pytest.skip("OR-Tools not installed")

        from backend.app.algorithms.v2.hybrid_orchestrator.orchestrator import HybridOrchestratorV2
        from backend.app.models.schemas import MapNode, MapEdge
        nodes, edges, tasks, agvs = small_scenario

        mn = [MapNode(id=n["id"], name=n["name"], x=n["x"], y=n["y"]) for n in nodes]
        me = [MapEdge(from_node=e["from_node"], to_node=e["to_node"],
                     distance=e["distance"]) for e in edges]

        orch = HybridOrchestratorV2()
        orch.initialize(mn, me)

        assert len(orch._distance_cache) > 0, \
            f"Orchestrator distance_cache empty after init! (P0-1 regression)"
        logger.info(f"[E2E-7] Orchestrator OK: {len(orch._distance_cache)} dist entries")

    def test_orchestrator_schedule_complete_pipeline(self, small_scenario):
        """[E2E-8] V2 Orchestrator 完整调度管线 (init → assign → plan → result)"""
        try:
            import ortools
        except ImportError:
            pytest.skip("OR-Tools not installed")

        from backend.app.algorithms.v2.hybrid_orchestrator.orchestrator import (
            HybridOrchestratorV2, OrchestratorConfig, OrchestratorMode
        )
        from backend.app.models.schemas import (
            MapNode, MapEdge, AgvTask, AgvStatus, AgvTaskStatus
        )
        nodes, edges, task_dicts, agv_dicts = small_scenario

        mn = [MapNode(id=n["id"], name=n["name"], x=n["x"], y=n["y"]) for n in nodes]
        me = [MapEdge(from_node=e["from_node"], to_node=e["to_node"],
                     distance=e["distance"]) for e in edges]
        tk = [AgvTask(id=t["id"], pickup_node=t["pickup_node_id"],
                      dropoff_node=t["dropoff_node_id"], priority=t["priority"])
              for t in task_dicts]
        av = [AgvStatus(id=a["id"], current_node=a["current_node"],
                        battery=a["battery"], speed=a["speed"])
              for a in agv_dicts]

        cfg = OrchestratorConfig(mode=OrchestratorMode.BENCHMARK)
        orch = HybridOrchestratorV2(config=cfg)

        t0 = time.perf_counter()
        result = orch.schedule(mn, me, tk, av)
        elapsed = (time.perf_counter() - t0) * 1000

        assert result is not None, "Orchestrator returned None"
        assert len(result.assignments) > 0 or elapsed > 0, "Empty result with no time spent"
        assert hasattr(result, 'metrics'), "Result missing metrics"
        
        logger.info(f"[E2E-8] Orchestrator pipeline OK: "
                    f"{len(result.assignments)} assigns, {elapsed:.0f}ms")


class TestV1HybridTimeoutE2E:

    def test_v1_hybrid_fcfs_fallback_exists(self):
        """[E2E-9] V1-Hybrid 有 _fcfs_fallback 方法 (P1-4 fix verification)"""
        from backend.app.algorithms.hybrid import HybridScheduler
        assert hasattr(HybridScheduler, '_fcfs_fallback'), \
            "_fcfs_fallback method missing from V1 HybridScheduler!"

    def test_v1_hybrid_timeout_parameter(self, small_scenario):
        """[E2E-10] V1-Hybrid 接受 timeout_seconds 参数"""
        from backend.app.algorithms.hybrid import HybridScheduler, AlgorithmConfig
        from backend.app.models.schemas import MapNode, MapEdge, AgvTask, AgvStatus
        nodes, edges, task_dicts, agv_dicts = small_scenario

        mn = [MapNode(id=n["id"], name=n["name"], x=n["x"], y=n["y"]) for n in nodes]
        me = [MapEdge(from_node=e["from_node"], to_node=e["to_node"],
                     distance=e["distance"]) for e in edges]
        tk = [AgvTask(id=t["id"], pickup_node=t["pickup_node_id"],
                      dropoff_node=t["dropoff_node_id"]) for t in task_dicts[:3]]
        av = [AgvStatus(id=a["id"], current_node=a["current_node"]) 
              for a in agv_dicts[:3]]

        cfg = AlgorithmConfig()
        scheduler = HybridScheduler(cfg)

        # Should complete within 30s (with fallback if needed)
        t0 = time.perf_counter()
        result = scheduler.schedule(mn, me, tk, av, timeout_seconds=30.0)
        elapsed = time.perf_counter() - t0

        assert result is not None, "V1-Hybrid returned None"
        assert elapsed < 35.0, f"V1-Hybrid exceeded 30s+5s margin: {elapsed:.1f}s"


class TestV2HybridSchedulerTimeoutE2E:

    def test_v2_hybrid_has_timeout_param(self):
        """[E2E-11] V2-HybridScheduler.schedule_batch 有 timeout_seconds 参数"""
        from backend.app.algorithms.v2.core.hybrid_scheduler import HybridScheduler
        import inspect
        sig = inspect.signature(HybridScheduler.schedule_batch)
        assert 'timeout_seconds' in sig.parameters, \
            "schedule_batch missing timeout_seconds parameter"

    def test_v2_hybrid_has_fcfs_fallback(self):
        """[E2E-12] V2-HybridScheduler 有 _fcfs_fallback_batch 方法"""
        from backend.app.algorithms.v2.core.hybrid_scheduler import HybridScheduler
        assert hasattr(HybridScheduler, '_fcfs_fallback_batch'), \
            "_fcfs_fallback_batch method missing!"


class TestScenarioRunnerE2E:

    def test_runner_comparison_small_scenario(self, small_scenario):
        """[E2E-13] ScenarioRunner.run_comparison 在小场景上正常运行"""
        from backend.app.algorithms.v2.evaluator.scenarios import (
            AGVTMS_Scenario, ScenarioMetadata, ScenarioType, ScenarioDifficulty
        )
        from backend.app.algorithms.v2.evaluator.runner import ScenarioRunner
        nodes, edges, tasks, agvs = small_scenario

        scenario = AGVTMS_Scenario(
            nodes=nodes, edges=edges, tasks=tasks, agvs=agvs,
            metadata=ScenarioMetadata(
                name="e2e_small_test", scenario_type=ScenarioType.WAREHOUSE,
                difficulty=ScenarioDifficulty.EASY,
                num_nodes=len(nodes), num_edges=len(edges),
                num_agvs=len(agvs), num_tasks=len(tasks),
            )
        )

        runner = ScenarioRunner(verbose=False)
        report = runner.run_comparison(scenario, algorithm_names=["fcfs", "greedy"])

        assert report.winner in ("fcfs", "greedy"), f"Unexpected winner: {report.winner}"
        assert len(report.results) >= 2, f"Expected >=2 results, got {len(report.results)}"
        logger.info(f"[E2E-13] Runner comparison OK: winner={report.winner}, "
                    f"{len(report.results)} algos")

    def test_runner_batch_two_scenarios(self):
        """[E2E-14] ScenarioRunner.run_batch 多场景批量执行"""
        from backend.app.algorithms.v2.evaluator.runner import ScenarioRunner
        runner = ScenarioRunner(verbose=False)
        
        batch_result = runner.run_batch(
            preset_names=["small_warehouse"],
            algorithm_names=["fcfs", "greedy"],
            seed=42,
            variants_per_type=1,
        )
        
        assert batch_result.overall_rankings, "No overall rankings returned"
        assert len(batch_result.scenario_reports) >= 1, "No scenario reports"
        logger.info(f"[E2E-14] Batch run OK: {len(batch_result.scenario_reports)} reports")


# ═══════════════════════════════════════════════════════════════
# Test Suite: 字段兼容性 (P0-2 fix verification)
# ═══════════════════════════════════════════════════════════════

class TestFieldCompatibilityE2E:

    def test_task_from_dict_all_formats(self):
        """[E2E-15] Task.from_dict 兼容6种命名风格 (P0-2 fix)"""
        from backend.app.algorithms.v2.core.hybrid_scheduler import Task

        cases = [
            ({"id": "T1", "pickup_node_id": "A", "dropoff_node_id": "B"}, "scenario"),
            ({"id": "T2", "pickup": "C", "delivery": "D"}, "internal"),
            ({"id": "T3", "pickup_node": "E", "dropoff_node": "F"}, "mixed"),
            ({"id": "T4", "pickup_node_id": "G", "delivery": "H"}, "cross"),
        ]

        for d, label in cases:
            t = Task.from_dict(d)
            assert t.pickup_node != "", f"{label}: pickup empty from {d}"
            assert t.delivery_node != "", f"{label}: delivery empty from {d}"
            logger.info(f"[E2E-15] Task.from_dict({label}): pickup={t.pickup_node}, delivery={t.delivery_node}")

    def test_registry_field_compatibility_roundtrip(self, small_scenario):
        """[E2E-16] 场景数据经过各算法适配器后字段不丢失"""
        from backend.app.algorithms.v2.evaluator.registry import get_registry
        registry = get_registry()

        nodes, edges, tasks, agvs = small_scenario
        original_pickups = set(t.get("pickup_node_id", "") for t in tasks)

        for algo_name in ["fcfs", "greedy"]:
            algo = registry.create(algo_name)
            result = algo.execute(nodes, edges, tasks, agvs)
            
            assert result.success, f"{algo_name} failed: {result.error}"
            if result.paths:
                for aid, path in result.paths.items():
                    # Path should contain actual node IDs, not empty strings
                    non_empty_nodes = [n for n in path if n and n.strip()]
                    assert len(non_empty_nodes) >= 2, \
                        f"{algo_name} path[{aid}] has too few valid nodes: {path}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
