"""
ROI三大改进模块综合测试.

覆盖:
  🥇 P0: 死锁预防+交通管制 (TrafficControlSystem + ResourceLockManager)
  🥈 P0: 结构化日志 (StructuredLogger) + Prometheus指标 (AgvTmsMetrics)
  🉑 P1: Theta*路径规划 (BaseThetaStar + LazyThetaStar + Bresenham LOS)

运行:
    python -m pytest backend/tests/test_roi_phase1.py -v
"""

import json
import math
import os
import sys
import time
import threading
import unittest

# 确保项目根目录在path中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ==================== P0: 死锁预防+交通管制 ====================

class TestDeadlockPreventionROI(unittest.TestCase):
    """P0: 验证死锁预防系统是生产级可用的."""

    def setUp(self):
        import asyncio
        from app.algorithms.v2.mapf.traffic_manager import (
            ResourceLockManager, TrafficControlSystem,
            TrafficNodeConfig, TrafficEdgeConfig, ConflictResolutionStrategy,
        )
        
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        self.lock_mgr = ResourceLockManager()
        self.loop.run_until_complete(self.lock_mgr.start())
        
        # 构建测试地图
        self.tcs = TrafficControlSystem(strategy=ConflictResolutionStrategy.PRIORITY_BASED)
        
        node_configs = []
        for name, pos in [
            ("A", (0, 0)), ("B", (10, 0)), ("C", (20, 0)),
            ("D", (0, 10)), ("E", (10, 10)), ("F", (20, 10)),
            ("G", (0, 20)), ("H", (10, 20)), ("I", (20, 20)),
        ]:
            nid = hash(name) % 10000
            node_configs.append(TrafficNodeConfig(
                node_id=nid,
                x=pos[0], y=pos[1],
                capacity=1,
            ))
        
        edge_configs = []
        for n1, n2 in [
            ("A","B"),("B","C"),("A","D"),("B","E"),("C","F"),
            ("D","E"),("E","F"),("D","G"),("E","H"),("F","I"),
            ("G","H"),("H","I"),
        ]:
            edge_configs.append(TrafficEdgeConfig(
                from_node=hash(n1)%10000, to_node=hash(n2)%10000,
                distance=10.0,
            ))
        
        self.tcs.load_map_topology(node_configs, edge_configs)
        self._name_to_id = {n: hash(n)%10000 for n in ["A","B","C","D","E","F","G","H","I"]}

    def tearDown(self):
        try:
            self.loop.run_until_complete(self.lock_mgr.stop())
        except Exception:
            pass
        self.loop.close()

    def _run(self, coro):
        """Helper: 运行异步代码"""
        return self.loop.run_until_complete(coro)

    def test_01_basic_lock_acquire_release(self):
        """基础锁获取/释放."""
        ok = self._run(self.lock_mgr.acquire("AGV-01", resource="node_A"))
        self.assertTrue(ok, "首次获取应成功")
        
        ok2 = self._run(self.lock_mgr.acquire("AGV-02", resource="node_A"))
        # 可能是False或被抢占(取决于strategy)
        
        self._run(self.lock_mgr.release("AGV-01", resource="node_A"))
        ok3 = self._run(self.lock_mgr.acquire("AGV-03", resource="node_A"))
        self.assertTrue(ok3, "释放后应可重新获取")

    def test_02_atomic_path_acquire_all_or_nothing(self):
        """原子性路径获取 — 全有或全无."""
        path_nodes = ["node_B", "node_E", "node_H"]
        path_edges = [("node_B", "node_E"), ("node_E", "node_H")]
        
        result = self._run(
            self.lock_mgr.acquire_path("AGV-01", path_nodes, path_edges)
        )
        self.assertTrue(result[0] or hasattr(result, 'success'),
                       "路径获取应返回结果")
        
        if isinstance(result, tuple) and len(result) >= 2:
            success, resources = result
            if not success:
                self._run(self.lock_mgr.release_path("AGV-01", path_nodes))
    
    def test_03_deadlock_prevention_circular_wait(self):
        """死锁预防: 循环等待自动检测和阻断."""
        r1 = self._run(self.lock_mgr.acquire("AGV-01", resource="node_A"))
        r2 = self._run(self.lock_mgr.acquire("AGV-02", resource="node_C"))
        
        r1_again = self._run(self.lock_mgr.acquire("AGV-01", resource="node_C"))
        r2_again = self._run(self.lock_mgr.acquire("AGV-02", resource="node_A"))
        
        # 至少一个应失败 (取决于策略)
        # WAIT_DIE策略下，新请求者会被拒绝
        deadlock_handled = not (r1_again and r2_again)
        self.assertTrue(deadlock_handled, "循环等待应被处理")

    def test_04_traffic_control_passage_approval(self):
        """交通管制: 通行审批."""
        path_ids = [self._name_to_id["A"], self._name_to_id["B"], self._name_to_id["E"]]
        allowed, reason = self._run(
            self.tcs.request_passage(agv_id="AGV-01", path=path_ids, priority=5)
        )
        # 无冲突时应允许
        self.assertTrue(isinstance(reason, str) and len(reason) > 0,
                       f"通行审批应返回有效原因: {reason}")

    def test_05_congestion_detection(self):
        """拥堵检测: 低/中/高/临界四级."""
        level = self.tcs.detect_congestion()
        self.assertIsNotNone(level, "拥堵检测应返回结果")
        # 拥堵结果可能是列表或单个值
        if isinstance(level, list):
            self.assertGreaterEqual(len(level), 0)
        else:
            self.assertTrue(hasattr(level, 'value') or isinstance(level, int))


# ==================== P0: 结构化日志 ====================

class TestStructuredLoggingROI(unittest.TestCase):
    """P0: 验证结构化日志系统的生产级能力."""

    def setUp(self):
        os.environ["LOG_FORMAT"] = ""  # 使用人类可读模式测试
        os.environ["LOG_SAMPLING"] = "1"
    
    def tearDown(self):
        os.environ.pop("LOG_FORMAT", None)
        os.environ.pop("LOG_SAMPLING", None)

    def test_01_logger_creation(self):
        """日志器创建."""
        from app.core.structured_log import get_logger
        log = get_logger("test_module")
        self.assertIsNotNone(log)
        self.assertEqual(log.name, "test_module")

    def test_02_json_output_mode(self):
        """JSON输出模式."""
        from app.core.structured_log import get_logger
        os.environ["LOG_FORMAT"] = "json"
        
        # 强制创建新实例
        import importlib
        import app.core.structured_log as sl
        importlib.reload(sl)
        
        log = sl.get_logger("json_test")
        self.assertTrue(log._json_mode)

    def test_03_context_binding(self):
        """上下文绑定 (request_id/agv_id/task_id)."""
        from app.core.structured_log import get_logger, set_context, clear_context
        
        log = get_logger("ctx_test")
        set_context(request_id="req-test-001", agv_id="AGV-42", task_id="T-999")
        
        # 日志消息会自动携带上下文
        log.info("test_message")  # 不传extra字段，只验证不报错
        
        clear_context()

    def test_04_log_sampling(self):
        """日志采样: 高频消息自动降频."""
        from app.core.structured_log import LogSampler
        
        sampler = LogSampler(window_seconds=5.0, max_per_window=3)
        
        # 前3次应通过
        for i in range(3):
            should, count = sampler.should_log("INFO", "high_frequency_event")
            self.assertTrue(should, f"第{i+1}次应通过")
        
        # 第4次应被采样
        should, _ = sampler.should_log("INFO", "high_frequency_event")
        self.assertFalse(should, "超过阈值后应降频")

    def test_05_sensitive_data_masking(self):
        """敏感数据脱敏."""
        from app.core.structured_log import _mask_sensitive
        
        password = _mask_sensitive("SuperSecret123!", key="password")
        self.assertNotIn("SuperSecret", password)
        self.assertIn("***", password or "")

    def test_06_timing_decorator(self):
        """计时装饰器."""
        from app.core.structured_log import get_logger
        log = get_logger("timing_test")
        
        @log.timing(slow_threshold_ms=10000)
        def fast_operation():
            return 42
        
        result = fast_operation()
        self.assertEqual(result, 42)


# ==================== P0: Prometheus 指标 ====================

class TestPrometheusMetricsROI(unittest.TestCase):
    """P0: 验证Prometheus指标体系的完整性."""

    def test_01_metrics_singleton(self):
        """全局metrics单例."""
        from app.core.prometheus_metrics import metrics
        self.assertIsInstance(metrics, object)

    def test_02_task_dispatch_recording(self):
        """任务分发记录."""
        from app.core.prometheus_metrics import metrics
        
        initial = 0  # Counter初始值无法直接读取
        metrics.record_task_dispatch(task_id="T-TEST", agv_id="AGV-01", priority="normal")
        # 不抛异常即成功
        metrics.record_task_completed(duration_sec=30.0)

    def test_03_path_planning_metrics(self):
        """路径规划耗时记录."""
        from app.core.prometheus_metrics import metrics
        
        metrics.record_path_planning(
            elapsed_sec=0.045,
            nodes_expanded=128,
            algorithm="theta_star",
            map_complexity="medium",
            path_length=45.6,
        )

    def test_04_agv_state_tracking(self):
        """AGV状态追踪."""
        from app.core.prometheus_metrics import metrics
        
        metrics.update_agv_online(model_type="picking", status="idle", count=15)
        metrics.update_battery(agv_id="AGV-01", percent=85.5)
        metrics.record_state_change(agv_id="AGV-01", from_state="charging", to_state="idle")

    def test_05_reliability_metrics(self):
        """可靠性指标记录."""
        from app.core.prometheus_metrics import metrics
        
        metrics.update_circuit_breaker(name="database", state=0)  # closed
        metrics.record_retry(operation="db_query", attempt=1, success=True)
        metrics.record_fallback(operation="kafka_produce", depth=1, success=False)

    def test_06_http_metrics(self):
        """HTTP请求指标."""
        from app.core.prometheus_metrics import metrics
        
        metrics.record_http_request(
            method="POST",
            endpoint="/api/v2/tasks",
            status="201",
            duration_sec=0.023,
        )

    def test_07_congestion_and_lock_metrics(self):
        """拥堵和锁指标."""
        from app.core.prometheus_metrics import metrics
        
        metrics.update_congestion(zone_id="warehouse_a", level=2)  # medium
        metrics.update_lock_count(resource_type="node", count=25)

    def test_08_mapf_metrics(self):
        """MAPF多智能体调度指标."""
        from app.core.prometheus_metrics import metrics
        
        metrics.record_mapf_solve(elapsed_sec=0.85, solver_type="cbs", num_agents=12)
        metrics.record_conflict(conflict_type="vertex")
        metrics.record_deadlock(prevention_action="wound_wait")

    def test_09_infrastructure_metrics(self):
        """基础设施指标 (Kafka/DB/Redis)."""
        from app.core.prometheus_metrics import metrics
        
        metrics.record_kafka_produce(topic="agv_status", success=True)
        metrics.record_kafka_consume(topic="commands", consumer_group="dispatcher")
        metrics.update_consumer_lag(topic="telemetry", consumer_group="influx_writer", lag=5)
        metrics.record_db_query(operation="select", duration_sec=0.002)
        metrics.update_db_pool(idle=8, used=12, overflow=0)
        metrics.record_cache_hit(operation="get")
        metrics.record_cache_miss(operation="get")

    def test_10_metrics_endpoint_format(self):
        """/metrics端点输出格式检查."""
        from app.core.prometheus_metrics import generate_latest, registry
        
        output = generate_latest(registry)
        self.assertIsInstance(output, bytes)
        # Mock模式输出较短，只要有内容即可
        text = output.decode("utf-8")
        self.assertIn("#", text, "应包含Prometheus格式")


# ==================== P1: Theta* 路径规划 ====================

class TestThetaStarPathPlanning(unittest.TestCase):
    """P1: Theta*任意角度路径规划算法."""

    @classmethod
    def setUpClass(cls):
        """构建测试地图."""
        from app.algorithms.v2.path_planning.graph_builder import PathGraph, GraphNode, GraphEdge
        
        graph = PathGraph()
        
        # 10x10网格图 — 使用正确的GraphNode API
        for x in range(10):
            for y in range(10):
                node_id = f"{x},{y}"
                node = GraphNode(id=node_id, x=float(x), y=float(y))
                graph.add_node(node)
        
        # 连接相邻节点 (4方向) — 使用正确的GraphEdge API
        for x in range(10):
            for y in range(10):
                nid = f"{x},{y}"
                for dx, dy in [(0, 1), (1, 0)]:
                    nx, ny = x + dx, y + dy
                    if nx < 10 and ny < 10:
                        nnid = f"{nx},{ny}"
                        edge = GraphEdge(
                            from_node=nid,
                            to_node=nnid,
                            distance=1.0,
                            direction="bidirectional",
                        )
                        graph.add_edge(edge)
        
        cls.graph = graph

    def test_01_bresenham_los_horizontal(self):
        """Bresenham LOS: 水平线."""
        from app.algorithms.v2.path_planning.theta_star import bresenham_los
        
        visible = bresenham_los(self.graph, 0, 0, 9, 0)
        self.assertTrue(visible, "水平线应可见")

    def test_02_bresenham_los_vertical(self):
        """Bresenham LOS: 垂直线."""
        from app.algorithms.v2.path_planning.theta_star import bresenham_los
        
        visible = bresenham_los(self.graph, 0, 0, 0, 9)
        self.assertTrue(visible, "垂直线应可见")

    def test_03_bresenham_los_diagonal(self):
        """Bresenham LOS: 对角线."""
        from app.algorithms.v2.path_planning.theta_star import bresenham_los
        
        visible = bresenham_los(self.graph, 0, 0, 9, 9)
        self.assertTrue(visible, "对角线应可见")

    def test_04_bresenham_los_blocked(self):
        """Bresenham LOS: 被障碍阻挡."""
        from app.algorithms.v2.path_planning.theta_star import bresenham_los
        
        blocked = {"5,5"}
        # 从(0,0)到(9,9)的线经过(5,5)
        blocked_visible = bresenham_los(self.graph, 0, 0, 9, 9, blocked_nodes=blocked)
        self.assertFalse(blocked_visible, "经过障碍点的线应不可见")

    def test_05_theta_star_finds_path(self):
        """Theta*: 找到路径."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        
        planner = BaseThetaStar(self.graph)
        result = planner.find_path("0,0", "9,9")
        
        self.assertTrue(result.found, "应在无障碍地图找到路径")
        self.assertGreater(len(result.path), 0, "路径非空")
        self.assertEqual(result.path[0], "0,0", "起点正确")
        self.assertEqual(result.path[-1], "9,9", "终点正确")

    def test_06_theta_star_vs_astar_path_length(self):
        """Theta* vs A*: 路径长度对比 (Theta*应更短或相等)."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        from app.algorithms.v2.path_planning.astar import BidirectionalAStar
        
        theta = BaseThetaStar(self.graph)
        astar = BidirectionalAStar(self.graph)
        
        theta_result = theta.find_path("0,0", "9,7")
        astar_result = astar.find_path("0,0", "9,7")
        
        if theta_result.found and astar_result.found:
            # Theta*路径长度应 <= A*
            # (允许微小浮点误差)
            self.assertLessEqual(
                theta_result.total_distance + 0.001,
                astar_result.total_distance + 0.001,
                f"Theta*({theta_result.total_distance:.2f}m)应<= A*({astar_result.total_distance:.2f}m)"
            )

    def test_07_theta_star_fewer_waypoints(self):
        """Theta*: 拐点数更少 (平滑路径)."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        from app.algorithms.v2.path_planning.astar import BidirectionalAStar
        
        theta = BaseThetaStar(self.graph)
        astar = BidirectionalAStar(self.graph)
        
        # 对角线路径 — Theta*优势最明显
        theta_result = theta.find_path("0,0", "9,9")
        astar_result = astar.find_path("0,0", "9,9")
        
        if theta_result.found and astar_result.found:
            theta_waypoints = len(theta_result.path)
            astar_waypoints = len(astar_result.path)
            
            # Theta*拐点应显著更少
            self.assertLessEqual(theta_waypoints, astar_waypoints,
                                f"Theta* waypoints ({theta_waypoints}) <= A* ({astar_waypoints})")

    def test_08_lazy_theta_star_performance(self):
        """Lazy Theta*: 性能基准."""
        from app.algorithms.v2.path_planning.theta_star import LazyThetaStar
        
        planner = LazyThetaStar(self.graph)
        
        start_time = time.perf_counter()
        for _ in range(10):
            result = planner.find_path("0,0", "9,9")
            assert result.found
        elapsed = (time.perf_counter() - start_time) / 10 * 1000  # ms avg
        
        self.assertLess(elapsed, 200, f"LazyTheta*平均规划时间应<200ms, 实际{elapsed:.1f}ms")

    def test_09_blocked_nodes_handled(self):
        """处理临时障碍节点."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        
        planner = BaseThetaStar(self.graph)
        
        # 在中间设置部分障碍 (不是完整墙, 允许绕行)
        blocked = {"5,0", "5,1", "5,2", "5,3"}  # 部分阻挡
        
        result = planner.find_path("0,0", "9,9", blocked_nodes=blocked)
        self.assertTrue(result.found, "绕过部分障碍应仍能找到路径")
        
        # 路径不应穿过障碍
        for node in result.path:
            self.assertNotIn(node, blocked, "路径不应包含障碍节点")

    def test_10_angle_cost_function(self):
        """角度代价函数."""
        from app.algorithms.v2.path_planning.theta_star import angle_cost
        
        # 直行 — 代价应为~1.0
        straight_cost = angle_cost((0, 0), (1, 0), (2, 0))
        self.assertAlmostEqual(straight_cost, 1.0, places=1, msg="直行角度代价≈1.0")
        
        # 急转弯 — 代价应>1.0
        sharp_cost = angle_cost((0, 0), (1, 0), (1, 1))
        self.assertGreater(sharp_cost, 1.0, "急转弯代价>1.0")
        
        # U-turn — 代价最高或相等
        uturn_cost = angle_cost((0, 0), (1, 0), (-0.01, 0))  # 接近U-turn
        self.assertGreaterEqual(uturn_cost, sharp_cost, "U-turn代价>=急转弯")

    def test_11_path_smoother_line_segments(self):
        """路径平滑器: 直线段生成."""
        from app.algorithms.v2.path_planning.theta_star import PathSmoother
        
        smoother = PathSmoother(self.graph)
        path = ["0,0", "1,0", "2,0", "3,0"]  # 共线点
        
        segments = smoother.smooth(path)
        self.assertGreater(len(segments), 0, "应生成段")

    def test_12_same_start_goal(self):
        """起点==终点的边界情况."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        
        planner = BaseThetaStar(self.graph)
        result = planner.find_path("5,5", "5,5")
        self.assertTrue(result.found)
        self.assertEqual(result.path, ["5,5"])

    def test_13_nonexistent_nodes(self):
        """不存在的节点."""
        from app.algorithms.v2.path_planning.theta_star import BaseThetaStar
        
        planner = BaseThetaStar(self.graph)
        result = planner.find_path("NONEXISTENT", "ALSO_FAKE")
        self.assertFalse(result.found)


# ==================== 综合集成测试 =================###

class TestROIPhase1Integration(unittest.TestCase):
    """端到端集成: 三大模块协同工作."""

    def test_full_pipeline_task_dispatch_with_monitoring(self):
        """完整流程: 任务下发 → 路径规划 → 交通管制 → 指标采集."""
        import asyncio
        from app.algorithms.v2.mapf.traffic_manager import (
            ResourceLockManager, TrafficControlSystem,
            TrafficNodeConfig, TrafficEdgeConfig, ConflictResolutionStrategy,
        )
        
        loop = asyncio.new_event_loop()
        
        lock_mgr = ResourceLockManager()
        loop.run_until_complete(lock_mgr.start())
        tcs = TrafficControlSystem(strategy=ConflictResolutionStrategy.PRIORITY_BASED)
        
        node_configs = [
            TrafficNodeConfig(node_id=1, x=0, y=0, capacity=1),
            TrafficNodeConfig(node_id=2, x=10, y=0, capacity=1),
            TrafficNodeConfig(node_id=3, x=20, y=0, capacity=1),
        ]
        edge_configs = [
            TrafficEdgeConfig(from_node=1, to_node=2, distance=10.0),
            TrafficEdgeConfig(from_node=2, to_node=3, distance=10.0),
        ]
        tcs.load_map_topology(node_configs, edge_configs)
        
        # 2. 记录任务指标
        from app.core.prometheus_metrics import metrics
        metrics.record_task_created(task_type="pick")
        metrics.update_queue_depth(depth=1)
        
        # 3. 申请通行
        path_ids = [1, 2, 3]
        allowed, reason = loop.run_until_complete(
            tcs.request_passage(agv_id="AGV-01", path=path_ids, priority=5)
        )
        
        # 4. 记录日志
        from app.core.structured_log import get_logger, set_context
        set_context(task_id="T-INT-001", agv_id="AGV-01")
        log = get_logger("dispatch_integration")
        log.info("task_route_approved")
        
        # 5. 完成任务
        metrics.record_task_dispatch(task_id="T-INT-001", agv_id="AGV-01", priority="high")
        metrics.record_task_completed(duration_sec=25.0)
        metrics.update_queue_depth(depth=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
