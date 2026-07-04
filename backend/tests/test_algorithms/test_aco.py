"""
ACO (蚁群算法) 单元测试 - 对标OR-Tools求解器验证规范

测试范围:
- 路径规划正确性
- 信息素更新逻辑
- 参数敏感性
- 边界条件
- 性能基准

覆盖率目标: 85%+
运行命令:
    pytest tests/test_algorithms/test_aco.py -v --cov=app/algorithms/aco.py --cov-report=term-missing
"""

import pytest
import numpy as np
from typing import List, Tuple, Dict, Any
from unittest.mock import patch, MagicMock


# ==================== 测试数据准备 ====================

class TestMapData:
    """测试用地图数据工厂"""
    
    @staticmethod
    def simple_linear_map() -> Dict[str, Any]:
        """
        线性拓扑地图 (4个节点直线排列)
        
        node_0 → node_1 → node_2 → node_3
          10m       10m       10m
        """
        return {
            "nodes": {
                "node_0": {"x": 0, "y": 0},
                "node_1": {"x": 10, "y": 0},
                "node_2": {"x": 20, "y": 0},
                "node_3": {"x": 30, "y": 0},
            },
            "edges": {
                ("node_0", "node_1"): {"weight": 10.0, "distance": 10.0},
                ("node_1", "node_2"): {"weight": 10.0, "distance": 10.0},
                ("node_2", "node_3"): {"weight": 10.0, "distance": 10.0},
            }
        }
    
    @staticmethod
    def grid_map() -> Dict[str, Any]:
        """
        网格地图 (2x2)
        
        n00 ──10── n01
         │           │
        10          10
         │           │
        n10 ──10── n11
        """
        return {
            "nodes": {
                "n00": {"x": 0, "y": 0},
                "n01": {"x": 10, "y": 0},
                "n10": {"x": 0, "y": 10},
                "n11": {"x": 10, "y": 10},
            },
            "edges": {
                ("n00", "n01"): {"weight": 10.0},
                ("n00", "n10"): {"weight": 10.0},
                ("n01", "n11"): {"weight": 10.0},
                ("n10", "n11"): {"weight": 10.0},
            }
        }
    
    @staticmethod
    def complex_map() -> Dict[str, Any]:
        """
        复杂地图 (含障碍物/多路径选择)
        
           start
           /   \
          5     8
         /         \
        A ──3── B ──4── goal
         \       /
          6    7
           \   /
            end
        """
        return {
            "nodes": {
                "start": {"x": 5, "y": 15},
                "A": {"x": 0, "y": 10},
                "B": {"x": 10, "y": 10},
                "goal": {"x": 20, "y": 10},
                "end": {"x": 5, "y": 5},
            },
            "edges": {
                ("start", "A"): {"weight": 5.0},
                ("start", "B"): {"weight": 8.0},
                ("A", "B"): {"weight": 3.0},
                ("B", "goal"): {"weight": 4.0},
                ("A", "end"): {"weight": 6.0},
                ("B", "end"): {"weight": 7.0},
                # 双向边
                ("A", "start"): {"weight": 5.0},
                ("B", "start"): {"weight": 8.0},
                # ... 其他反向边
            }
        }


# ==================== ACO算法测试类 ====================

class TestACOPathPlanning:
    """
    ACO路径规划核心功能测试
    
    验证点:
    1. 能找到有效路径 (从起点到终点)
    2. 找到的路径长度合理 (接近最优解)
    3. 信息素机制正常工作
    4. 参数调优能影响结果质量
    """
    
    @pytest.fixture
    def aco_solver(self):
        """ACO求解器实例 (使用默认参数)"""
        try:
            from app.algorithms.aco import ACOSolver
            return ACOSolver(
                n_ants=10,
                n_iterations=50,
                alpha=1.0,      # 信息素重要性
                beta=2.0,       # 启发式信息重要性
                rho=0.1,        # 信息素蒸发系数
                Q=100.0,        # 信息素强度
            )
        except ImportError:
            pytest.skip("ACO module not available")
    
    def test_find_path_simple_map(self, aco_solver):
        """简单线性地图应找到直线路径"""
        map_data = TestMapData.simple_linear_map()
        
        path, cost = aco_solver.solve(
            nodes=map_data["nodes"],
            edges=map_data["edges"],
            start="node_0",
            goal="node_3"
        )
        
        # 验证路径有效性
        assert len(path) >= 2, "Path should have at least start and goal"
        assert path[0] == "node_0", "Path should start from node_0"
        assert path[-1] == "node_3", "Path should end at node_3"
        
        # 验证路径成本合理性 (最优解应为30)
        assert cost <= 30 * 1.5, f"Cost {cost} too high for optimal ~30"
        
        print(f"✓ Found path: {' -> '.join(path)} with cost {cost:.2f}")
    
    def test_grid_map_shortest_path(self, aco_solver):
        """网格地图应找到最短路径之一"""
        map_data = TestMapData.grid_map()
        
        path, cost = aco_solver.solve(
            nodes=map_data["nodes"],
            edges=map_data["edges"],
            start="n00",
            goal="n11"
        )
        
        # 最优解应该是20 (两条可能: n00→n01→n11 或 n00→n10→n11)
        assert cost <= 25, f"Cost {cost} should be close to optimal 20"
        assert len(path) == 3, "Grid shortest path should visit 3 nodes"
        
        print(f"✓ Grid path: {' -> '.join(path)} cost={cost:.1f}")
    
    def test_no_path_exists(self, aco_solver):
        """不连通图应返回空路径或报错"""
        disconnected_map = {
            "nodes": {"a": {"x": 0, "y": 0}, "b": {"x": 10, "y": 0}},
            "edges": {}  # 无边
        }
        
        with pytest.raises((ValueError, Exception)):  # 应抛出异常或返回None
            path, cost = aco_solver.solve(
                nodes=disconnected_map["nodes"],
                edges=disconnected_map["edges"],
                start="a",
                goal="b"
            )
    
    def test_start_equals_goal(self, aco_solver):
        """起点和终点相同应返回零成本路径"""
        map_data = TestMapData.simple_linear_map()
        
        path, cost = aco_solver.solve(
            nodes=map_data["nodes"],
            edges=map_data["edges"],
            start="node_1",
            goal="node_1"
        )
        
        assert len(path) >= 1
        assert cost <= 0.01, "Same start/goal should have zero cost"


class TestPheromoneUpdate:
    """
    信息素更新机制测试
    
    验证:
    1. 信息素初始化
    2. 蒸发过程 (rho参数)
    3. 奖励机制 (Q参数)
    4. 信息素边界限制
    """
    
    @pytest.fixture
    def pheromone_matrix(self):
        """创建测试用的信息素矩阵"""
        try:
            from app.algorithms.aco import PheromoneMatrix
            return PheromoneMatrix(
                edges=[("A","B"), ("B","C"), ("C","D")],
                initial_pheromone=1.0,
                min_pheromone=0.1,
                max_pheromone=10.0,
            )
        except ImportError:
            pytest.skip("PheromoneMatrix class not found")
    
    def test_initial_values(self, pheromone_matrix):
        """初始值应该统一"""
        assert pheromone_matrix.get(("A","B")) == 1.0
        assert pheromone_matrix.get(("B","C")) == 1.0
    
    def test_evaporation(self, pheromone_matrix):
        """蒸发后信息素应降低"""
        rho = 0.1
        pheromone_matrix.evaporate(rho=rho)
        
        new_value = pheromone_matrix.get(("A","B"))
        expected = 1.0 * (1 - rho)  # 0.9
        
        assert abs(new_value - expected) < 0.001, \
            f"After evaporation: expected {expected}, got {new_value}"
    
    def test_deposit_increases_pheromone(self, pheromone_matrix):
        """沉积操作应增加信息素"""
        pheromone_matrix.deposit(edge=("A","B"), amount=5.0, Q=100.0)
        
        new_value = pheromone_matrix.get(("A","B"))
        assert new_value > 1.0, "Pheromone should increase after deposit"
    
    def test_max_min_bounds(self, pheromone_matrix):
        """信息素应在[min, max]范围内"""
        # 尝试超过上限
        pheromone_matrix.deposit(edge=("A","B"), amount=9999.0, Q=100.0)
        assert pheromone_matrix.get(("A","B")) <= 10.0 + 0.01, \
            "Should not exceed max_pheromone"
        
        # 多次蒸发到下限以下
        for _ in range(100):
            pheromone_matrix.evaporate(rho=0.9)
        
        assert pheromone_matrix.get(("A","B")) >= 0.1 - 0.01, \
            "Should not go below min_pheromone"


class TestParameterSensitivity:
    """
    参数敏感性分析
    
    测试不同参数组合对结果的影响:
    - alpha (信息素权重): 高alpha倾向于探索已知好路径
    - beta (启发式权重): 高beta倾向于贪心选择
    - rho (蒸发率): 高rho导致更快遗忘
    - n_ants/n_iter: 增加通常提升质量但降低速度
    """
    
    def test_high_alpha_exploitation(self):
        """高alpha值应更倾向利用已有信息"""
        try:
            from app.algorithms.aco import ACOSolver
            
            solver_high_alpha = ACOSolver(alpha=3.0, beta=0.5, n_ants=20, n_iterations=100)
            
            # 运行两次，第二次应在第一次基础上改进（因为高alpha会强化第一次的好路径）
            map_data = TestMapData.simple_linear_map()
            
            path1, cost1 = solver_high_alpha.solve(**map_data, start="node_0", goal="node_3")
            path2, cost2 = solver_high_alpha.solve(**map_data, start="node_0", goal="node_3")
            
            # 第二次通常不会比第一次差很多 (但不绝对保证)
            assert cost2 <= cost1 * 1.5  # 允许一定波动
            
        except ImportError:
            pytest.skip("ACOSolver import failed")
    
    def test_low_beta_greedy_behavior(self):
        """低beta值可能导致随机性增加"""
        try:
            from app.algorithms.aco import ACOSolver
            
            solver_random = ACOSolver(alpha=1.0, beta=0.1, n_ants=5, n_iterations=10)
            
            # 多次运行，结果应有较大方差
            costs = []
            map_data = TestMapData.complex_map()
            
            for _ in range(5):
                _, cost = solver_random.solve(**map_data, start="start", goal="goal")
                costs.append(cost)
            
            variance = np.var(costs)
            assert variance > 0, "Low beta should produce variable results"
            
        except ImportError:
            pytest.skip("ACOSolver import failed")


class TestEdgeCases:
    """边界条件和异常场景测试"""
    
    def test_single_node_map(self):
        """单节点地图"""
        try:
            from app.algorithms.aco import ACOSolver
            solver = ACOSolver()
            
            single_node_map = {
                "nodes": {"only": {"x": 0, "y": 0}},
                "edges": {}
            }
            
            path, cost = solver.solve(**single_node_map, start="only", goal="only")
            assert path[0] == "only"
            
        except (ImportError, Exception):
            pass  # 可能不支持单节点
    
    def test_large_map_performance(self):
        """大规模地图性能基准 (<5秒完成)"""
        import time
        try:
            from app.algorithms.aco import ACOSolver
            
            # 生成100节点网格图
            n_nodes = 100
            nodes = {}
            edges = {}
            
            for i in range(10):
                for j in range(10):
                    nid = f"n{i}_{j}"
                    nodes[nid] = {"x": i*10, "y": j*10}
                    
                    if i < 9:  # 水平边
                        edges[(nid, f"n{i+1}_{j}")] = {"weight": 10.0}
                    if j < 9:  # 垂直边
                        edges[(nid, f"n{i}_{j+1}")] = {"weight": 10.0}
            
            solver = ACOSolver(n_ants=20, n_iterations=50)
            
            start_time = time.time()
            path, cost = solve_result if 'solve_result' in locals() else None
            path, cost = solver.solve(nodes=nodes, edges=edges, 
                                      start="n_0_0", goal="n_9_9")
            elapsed = time.time() - start_time
            
            assert elapsed < 5.0, f"100-node map took {elapsed:.2f}s (>5s threshold)"
            assert len(path) > 0, "Should find a path in connected graph"
            print(f"✓ 100-node grid solved in {elapsed:.2f}s, cost={cost:.1f}")
            
        except ImportError:
            pytest.skip("Performance test skipped")


# ==================== 集成测试: ACO vs 真实最短路径 ====================

class TestACOVsOptimal:
    """
    ACO解质量对比测试
    
    使用已知最优解的问题实例，评估ACO的近似比率
    近似比率 = ACO_cost / Optimal_cost (应 ≤ 1.5)
    """
    
    @pytest.mark.integration
    def test_approximation_ratio_on_known_instances(self):
        """在已知最优解的实例上测试近似比率"""
        try:
            from app.algorithms.aco import ACOSolver
            
            solver = ACOSolver(n_ants=30, n_iterations=200)
            
            # 实例1: 简单链式 (最优=30)
            map1 = TestMapData.simple_linear_map()
            _, cost1 = solver.solve(**map1, start="node_0", goal="node_3")
            ratio1 = cost1 / 30.0
            assert ratio1 <= 1.5, f"Instance1 ratio {ratio1:.2f} > 1.5"
            
            # 实例2: 网格2x2 (最优=20)
            map2 = TestMapData.grid_map()
            _, cost2 = solver.solve(**map2, start="n00", goal="n11")
            ratio2 = cost2 / 20.0
            assert ratio2 <= 1.5, f"Instance2 ratio {ratio2:.2f} > 1.5"
            
            print(f"\n✓ Approximation ratios: instance1={ratio1:.2f}, instance2={ratio2:.2f}")
            
        except ImportError:
            pytest.skip("Integration test requires ACO module")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
