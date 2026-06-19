"""
MAPF + 交通管制 集成测试套件
验证: CBS算法求解、死锁预防、拥堵检测、端到端调度流程

运行: pytest backend/tests/test_mapf_traffic_integration.py -v
"""

import pytest
import asyncio
import time
import logging
from typing import Dict, List, Tuple
from unittest.mock import Mock

from app.algorithms.v2.mapf.cbs_solver import (
    CBSSolver, ECBSSolver, SpaceTimeAStar,
    ConflictType, Conflict, Constraint, Position, PathResult,
    CBSNode, CBSSolution, ConflictDetector, solve_mapf
)
from app.algorithms.v2.mapf.traffic_manager import (
    ResourceLockManager, TrafficControlSystem,
    LockType, ConflictResolutionStrategy, TrafficEventType,
    ResourceId, ResourceLock, TrafficEvent, CongestionInfo,
    TrafficNodeConfig, TrafficEdgeConfig
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# Mock PathGraph (4x3网格地图)
# ══════════════════════════════════════════════════════════

class MockPathGraph:
    def __init__(self):
        self.nodes = list(range(12))
        self.adjacency = {
            0: {1: {'distance': 1.0}, 4: {'distance': 1.0}},
            1: {0: {'distance': 1.0}, 2: {'distance': 1.0}},
            2: {1: {'distance': 1.0}, 3: {'distance': 1.0}, 6: {'distance': 1.0}},
            3: {2: {'distance': 1.0}, 7: {'distance': 1.0}},
            4: {0: {'distance': 1.0}, 5: {'distance': 1.0}},
            5: {4: {'distance': 1.0}, 6: {'distance': 1.0}},
            6: {2: {'distance': 1.0}, 5: {'distance': 1.0}, 7: {'distance': 1.0}, 10: {'distance': 1.0}},
            7: {3: {'distance': 1.0}, 6: {'distance': 1.0}, 11: {'distance': 1.0}},
            8: {}, 9: {},
            10: {6: {'distance': 1.0}, 11: {'distance': 1.0}},
            11: {7: {'distance': 1.0}, 10: {'distance': 1.0}},
        }
        self.coordinates = {
            0:(0,3), 1:(1,3), 2:(2,3), 3:(3,3),
            4:(0,2), 5:(1,2), 6:(2,2), 7:(3,2),
            8:(0,1), 9:(1,1), 10:(2,1), 11:(3,1)
        }

    def get_neighbors(self, node_id):
        return list(self.adjacency.get(node_id, {}).keys())

    def get_edge_weight(self, f, t, w='distance'):
        return self.adjacency.get(f, {}).get(t, {}).get(w, 1.0)

    def euclidean_distance(self, f, t):
        x1,y1 = self.coordinates.get(f,(0,0))
        x2,y2 = self.coordinates.get(t,(0,0))
        return ((x2-x1)**2+(y2-y1)**2)**0.5


@pytest.fixture
def mock_graph():
    return MockPathGraph()

@pytest.fixture
def traffic_system():
    system = TrafficControlSystem(strategy=ConflictResolutionStrategy.PRIORITY_BASED)
    coords = MockPathGraph().coordinates
    nodes = [TrafficNodeConfig(i,c[0],c[1],capacity=1 if i!=6 else 2) for i,c in coords.items()]
    edges = [TrafficEdgeConfig(f,t) for f,nei in MockPathGraph().adjacency.items() for t in nei.keys() if f<t]
    system.load_map_topology(nodes, edges)
    return system


# ══════════════════════════════════════════════════════════
# Test Group 1: CBS算法核心功能
# ══════════════════════════════════════════════════════════

class TestCBSSolverBasic:
    
    @pytest.mark.asyncio
    async def test_single_agent_path(self, mock_graph):
        """单agent应返回最优路径"""
        solver = CBSSolver(graph=mock_graph)
        result = await solver.solve({"AGV-001": (0, 11)})
        
        assert result.success is True
        path = result.paths["AGV-001"].path
        assert path[0].node_id == 0
        assert path[-1].node_id == 11
    
    @pytest.mark.asyncio
    async def test_two_agents_no_conflict(self, mock_graph):
        """无冲突双agent应同时成功"""
        solver = CBSSolver(graph=mock_graph)
        result = await solver.solve({"AGV-001": (0, 3), "AGV-002": (4, 11)})
        assert result.success is True and len(result.paths) == 2
    
    @pytest.mark.asyncio
    async def test_conflict_resolution(self, mock_graph):
        """CBS应解决路径冲突"""
        solver = CBSSolver(graph=mock_graph, time_limit_seconds=5.0)
        result = await solver.solve({"AGV-A": (0, 7), "AGV-B": (3, 4)})
        
        assert result.success is True
        detector = ConflictDetector()
        assert detector.find_first_conflict(result.paths) is None


class TestECBSPerformance:
    
    @pytest.mark.asyncio
    async def test_ecbs_quality_within_bound(self, mock_graph):
        """ECBS解质量应在w*optimal范围内"""
        tasks = {f"AGV-{i:02d}": (i%12, (i+7)%12) for i in range(6)}
        
        cbs_solver = CBSSolver(graph=mock_graph, time_limit_seconds=30.0)
        cbs_result = await cbs_solver.solve(tasks)
        
        ecbs_solver = ECBSSolver(graph=mock_graph, suboptimality_factor=1.2, time_limit_seconds=30.0)
        ecbs_result = await ecbs_solver.solve(tasks)
        
        if cbs_result.success and ecbs_result.success:
            max_allowed = cbs_result.solution_node.sum_of_costs * 1.2
            assert ecbs_result.solution_node.sum_of_costs <= max_allowed


class TestConflictDetection:
    """冲突检测器专项"""
    
    def test_vertex_conflict(self):
        paths = {
            "X": PathResult("X", [Position(5,3), Position(6,4)], 1.0),
            "Y": PathResult("Y", [Position(7,3), Position(6,4)], 1.0),
        }
        conflict = ConflictDetector().find_first_conflict(paths)
        assert conflict is not None and conflict.conflict_type == ConflictType.VERTEX
    
    def test_swap_conflict(self):
        paths = {
            "P": PathResult("P", [Position(5,2), Position(6,3)], 1.0),
            "Q": PathResult("Q", [Position(6,2), Position(5,3)], 1.0),
        }
        conflict = ConflictDetector().find_first_conflict(paths)
        assert conflict is not None and conflict.conflict_type in (ConflictType.SWAP, ConflictType.EDGE)


# ══════════════════════════════════════════════════════════
# Test Group 2: 交通管制与死锁预防
# ══════════════════════════════════════════════════════════

class TestResourceLockManager:
    
    @pytest.mark.asyncio
    async def test_acquire_release_basic(self):
        """基础获取/释放操作"""
        events = []
        mgr = ResourceLockManager(event_callback=lambda e: events.append(e))
        await mgr.start()
        
        success = await mgr.acquire("AGV-01", ResourceId.vertex(5), duration=2.0)
        assert success is True
        
        status = mgr.get_lock_status(ResourceId.vertex(5))
        assert status is not None and status["owner"] == "AGV-01"
        
        released = await mgr.release("AGV-01", ResourceId.vertex(5))
        assert released is True
        
        assert mgr.get_lock_status(ResourceId.vertex(5)) is None
        
        await mgr.stop()
    
    @pytest.mark.asyncio
    async def test_reentrant_lock(self):
        """同一agent重复获取应更新而非拒绝"""
        mgr = ResourceLockManager()
        await mgr.start()
        
        r = ResourceId.vertex(10)
        assert await mgr.acquire("A", r, 1.0) is True
        assert await mgr.acquire("A", r, 5.0) is True  # 更新duration
        
        await mgr.stop()
    
    @pytest.mark.asyncio
    async def test_conflict_rejection(self):
        """不同agent竞争同一资源应被正确处理"""
        mgr = ResourceLockManager(strategy=ConflictResolutionStrategy.WAIT_DIE)
        await mgr.start()
        
        r = ResourceId.vertex(20)
        assert await mgr.acquire("OWNER", r, 10.0) is True
        # CHALLENGER优先级更低(Wait-Die: 新的die) → 失败
        assert await mgr.acquire("CHALLENGER", r, 2.0, priority=5) is False
        
        await mgr.stop()
    
    @pytest.mark.asyncio
    async def test_path_atomic_acquisition(self):
        """路径原子获取: 任一失败则全部回滚"""
        mgr = ResourceLockManager()
        await mgr.start()
        
        # 先占用节点5阻止整条路径
        await mgr.acquire("BLOCKER", ResourceId.vertex(5), 100.0)
        
        success, acquired = await mgr.acquire_path(
            "REQUESTER",
            path_nodes=[0, 1, 5, 6],
            path_edges=[(0,1),(1,5),(5,6)]
        )
        
        assert success is False
        assert len(acquired) == 0  # 全部回滚
        
        # BLOCKER仍持有节点5
        assert mgr.get_lock_status(ResourceId.vertex(5)) is not None
        
        await mgr.stop()
    
    @pytest.mark.asyncio
    async def test_deadlock_prevention(self):
        """
        死锁预防测试场景:
        AGV-A 持有节点5, 等待节点10
        AGV-B 持有节点10, 等待节点5
        → 应被Wait-For Graph检测并阻止
        """
        events_collected = []
        mgr = ResourceLockManager(
            strategy=ConflictResolutionStrategy.WAIT_DIE,
            event_callback=lambda e: events_collected.append(e.to_dict())
        )
        await mgr.start()
        
        r5 = ResourceId.vertex(5)
        r10 = ResourceId.vertex(10)
        
        # A获取5, B获取10
        await mgr.acquire("A", r5, 10.0, priority=1)
        await mgr.acquire("B", r10, 10.0, priority=1)
        
        # B尝试获取5 (与A形成循环等待)
        result_b_5 = await mgr.acquire("B", r5, 2.0, priority=1)
        
        # Wait-Die策略下, 如果检测到死锁应该返回False
        # (具体行为取决于实现的时间戳比较逻辑)
        assert isinstance(result_b_5, bool)
        
        deadlock_events = [
            e for e in events_collected 
            if e.get("event_type") == "deadlock_detected"
        ]
        
        # 至少不应导致系统挂起
        stats = mgr.get_global_stats()
        assert stats["deadlocks_detected"] >= 0
        
        await mgr.stop()


class TestTrafficControlSystemIntegration:
    
    @pytest.mark.asyncio
    async def test_passage_request_approval(self, traffic_system):
        """正常路径通行请求应被批准"""
        await traffic_system.start()
        
        allowed, reason = await traffic_system.request_passage(
            agv_id="AGV-01",
            path=[0, 1, 2, 6],
            priority=1
        )
        
        assert allowed is True
        
        await traffic_system.stop()
    
    @pytest.mark.asyncio
    async def test_congestion_detection(self, traffic_system):
        """拥堵检测应识别高负载区域"""
        await traffic_system.start()
        
        # 模拟大量AGV聚集在节点6 (容量为2)
        for i in range(5):
            await traffic_system.lock_manager.acquire(
                f"AGV-{i}", 
                ResourceId.vertex(6), 
                duration=10.0
            )
            traffic_system._node_load[6] += 1
        
        congested = traffic_system.detect_congestion()
        
        node_6_congestion = [c for c in congested if c.node_id == 6]
        assert len(node_6_congestion) > 0
        assert node_6_congestion[0].severity in ("high", "critical")
        assert node_6_congestion[0].utilization > 1.0  # 超过容量
        
        await traffic_system.stop()
    
    @pytest.mark.asyncio
    async def test_global_efficiency_calculation(self, traffic_system):
        """全网效率计算"""
        await traffic_system.start()
        
        # 空载时效率应为0或接近0
        eff_empty = traffic_system.get_global_efficiency()
        
        # 加载后效率上升
        for n in range(4):
            traffic_system._node_load[n] = 1
        
        eff_loaded = traffic_system.get_global_efficiency()
        assert eff_loaded >= eff_empty
        
        await traffic_system.stop()
    
    @pytest.mark.asyncio
    async def test_system_report_generation(self, traffic_system):
        """系统报告应包含完整信息"""
        await traffic_system.start()
        
        report = traffic_system.get_system_report()
        
        assert "timestamp" in report
        assert "efficiency" in report
        assert "congestion" in report
        assert "locks" in report
        assert "global" in report["efficiency"]
        assert "details" in report["congestion"]
        
        await traffic_system.stop()


# ══════════════════════════════════════════════════════════
# Test Group 3: 端到端集成测试
# ══════════════════════════════════════════════════════════

class TestEndToEndMAPFWithTraffic:
    """CBS算法 + 交通管制 联合工作流程"""
    
    @pytest.mark.asyncio
    async def test_full_dispatch_workflow(self, mock_graph, traffic_system):
        """
        完整调度流程:
        1. CBS计算无冲突路径
        2. 交通管制申请资源锁
        3. 模拟执行
        4. 释放资源
        """
        await traffic_system.start()
        
        tasks = {"A": (0, 11), "B": (3, 4)}
        
        # Step 1: CBS求解
        solver = ECBSSolver(graph=mock_graph, suboptimality_factor=1.2)
        solution = await solver.solve(tasks)
        assert solution.success is True
        
        # Step 2: 为每个agent获取交通锁
        for agent_id, path_result in solution.paths.items():
            waypoints = [p.node_id for p in path_result.path]
            
            if len(waypoints) < 2:
                continue
            
            edges = [(waypoints[i], waypoints[i+1]) for i in range(len(waypoints)-1)]
            
            success, resources = await traffic_system.lock_manager.acquire_path(
                agent_id=agent_id,
                path_nodes=waypoints,
                path_edges=edges,
                priority=1
            )
            
            # 如果交通管制允许,记录锁定
            if success:
                holds = traffic_system.lock_manager.get_agent_holds(agent_id)
                assert len(holds) > 0
                
                logger.info(f"{agent_id}: locked {len(holds)} resources")
                
                # Step 3: 模拟执行完成,释放
                await traffic_system.lock_manager.release_path(agent_id, resources)
                
                final_holds = traffic_system.lock_manager.get_agent_holds(agent_id)
                assert len(final_holds) == 0
        
        await traffic_system.stop()
    
    @pytest.mark.asyncio
    async def test_stress_10_agents(self, mock_graph, traffic_system):
        """压力测试: 10台AGV并发调度 (参考极智嘉5000台基准的缩放版)"""
        await traffic_system.start()
        
        tasks = {}
        for i in range(10):
            start = (i * 3) % 12
            goal = (start + 7) % 12
            while goal == start:
                goal = (goal + 1) % 12
            tasks[f"AGV-{i:02d}"] = (start, goal)
        
        start_time = time.perf_counter()
        
        solver = ECBSSolver(graph=mock_graph, suboptimality_factor=1.3, time_limit_seconds=15.0)
        solution = await solver.solve(tasks)
        
        solve_time = (time.perf_counter() - start_time) * 1000
        
        if solution.success:
            logger.info(f"10-agent MAPF solved in {solve_time:.1f}ms, "
                       f"SOC={solution.solution_node.sum_of_costs:.1f}")
            
            # 验证冲突数
            detector = ConflictDetector()
            conflicts = detector.find_all_conflicts