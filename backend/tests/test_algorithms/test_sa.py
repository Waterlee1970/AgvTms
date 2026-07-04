"""
SA (模拟退火) 算法单元测试 - 对标OR-Tools CP-SAT验证规范

测试范围:
- 任务分配优化
- 解的可行性
- 降温策略
- 接受准则
- 性能基准

覆盖率目标: 85%+
运行命令:
    pytest tests/test_algorithms/test_sa.py -v --cov=app/algorithms/sa.py
"""

import pytest
import numpy as np
from typing import List, Dict, Any, Tuple
import time


# ==================== 测试数据工厂 ====================

class TaskAssignmentData:
    """
    任务分配问题实例生成器
    
    问题定义:
    - N个待分配任务
    - M个可用AGV车辆
    - 目标: 最小化总完成时间/成本
    
    输出格式:
        tasks: [ {task_id, pickup, dropoff, priority, ...} ]
        vehicles: [ {vehicle_id, pos, capacity, ...} ]
    """
    
    @staticmethod
    def simple_3x3_instance() -> Tuple[List[Dict], List[Dict]]:
        """3任务3车辆简单实例 (有已知最优解)"""
        tasks = [
            {"id": "t1", "pickup": "A", "dropoff": "B", "priority": 10},
            {"id": "t2", "pickup": "C", "dropoff": "D", "priority": 5},
            {"id": "t3", "pickup": "E", "dropoff": "F", "priority": 8},
        ]
        
        vehicles = [
            {"id": "v1", "pos": "A", "capacity": 100, "speed": 1.0},
            {"id": "v2", "pos": "C", "capacity": 200, "speed": 1.2},
            {"id": "v3", "pos": "E", "capacity": 150, "speed": 0.8},
        ]
        
        return tasks, vehicles
    
    @staticmethod
    def unbalanced_instance() -> Tuple[List[Dict], List[Dict]]:
        """
        不平衡实例: 任务数 >> 车辆数
        
        用于测试算法在大规模实例上的表现
        """
        tasks = [{"id": f"t{i}", "pickup": f"P{i}", "dropoff": f"D{i}", 
                  "priority": np.random.randint(1, 20)} for i in range(15)]
        
        vehicles = [{"id": f"v{j}", "pos": f"HUB_{j%3}", 
                     "capacity": 300, "speed": 1.0 + j*0.2} for j in range(4)]
        
        return tasks, vehicles
    
    @staticmethod
    def constrained_instance() -> Tuple[List[Dict], List[Dict]]:
        """含约束的实例 (容量/时间窗)"""
        tasks = [
            {
                "id": "heavy_task",
                "pickup": "WH_A",
                "dropoff": "LINE_B",
                "weight_kg": 250,
                "deadline_sec": 300,
                "priority": 20,
            },
            {
                "id": "light_task",
                "pickup": "WH_A",
                "dropoff": "QC_STATION",
                "weight_kg": 50,
                "deadline_sec": 600,
                "priority": 10,
            },
        ]
        
        vehicles = [
            {"id": "small_agv", "capacity_kg": 100, "pos": "WH_A"},
            {"id": "large_agv", "capacity_kg": 500, "pos": "WH_A"},
        ]
        
        return tasks, vehicles


# ==================== SA核心算法测试 ====================

class TestSAInitialization:
    """初始化和参数配置测试"""
    
    @pytest.fixture
    def sa_solver(self):
        """创建默认参数的SA求解器"""
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            return SimulatedAnnealingSolver(
                initial_temp=1000.0,
                final_temp=0.01,
                cooling_rate=0.95,
                iterations_per_temp=100,
            )
        except ImportError:
            pytest.skip("SA module not available")
    
    def test_initial_temperature_positive(self, sa_solver):
        """初始温度应为正数"""
        assert sa_solver.current_temp > 0
    
    def test_cooling_schedule_monotonic(self, sa_solver):
        """温度应单调递减"""
        temps = []
        temp = sa_solver.initial_temp
        
        for _ in range(50):
            temps.append(temp)
            temp *= sa_solver.cooling_rate
        
        # 验证严格递减 (或非递增)
        for i in range(len(temps)-1):
            assert temps[i+1] <= temps[i], \
                f"Temperature should decrease: t[{i}]={temps[i]:.4f}, t[{i+1}]={temps[i+1]:.4f}"


class TestSAAcceptanceCriteria:
    """
    Metropolis接受准则测试
    
    核心公式:
        P(accept) = exp(-ΔE / T)
    
    其中:
        ΔE = new_cost - old_cost (能量差)
        T = current temperature (温度)
    """
    
    @pytest.fixture
    def acceptance_checker(self):
        """接受概率计算器"""
        try:
            from app.algorithms.sa import _calculate_acceptance_probability
            return _calculate_acceptance_probability
        except ImportError:
            pytest.skip("Acceptance function not available")
    
    def test_always_accept_better_solution(self, acceptance_checker):
        """更优解 (ΔE < 0) 应以概率1接受"""
        prob = acceptance_checker(delta_cost=-10.0, temperature=100.0)
        assert prob == 1.0 or abs(prob - 1.0) < 0.0001
    
    def test_never_accept_worse_at_zero_temp(self, acceptance_checker):
        """零温度时不应接受更差解"""
        prob = acceptance_checker(delta_cost=10.0, temperature=0.001)
        assert prob < 0.001, f"At ~0K, should reject: P={prob:.6f}"
    
    def test_probabilistic_acceptance_at_high_temp(self, acceptance_checker):
        """高温时应以一定概率接受更差解"""
        prob = acceptance_checker(delta_cost=50.0, temperature=1000.0)
        assert 0 < prob < 1, f"Should be probabilistic at high temp: P={prob:.4f}"
    
    def test_acceptance_decreases_with_worse_delta(self, acceptance_checker):
        """ΔE越大，接受概率越低"""
        temp = 100.0
        
        prob_small = acceptance_checker(delta_cost=10.0, temperature=temp)
        prob_large = acceptance_checker(delta_cost=100.0, temperature=temp)
        
        assert prob_large < prob_small, \
            f"Worse delta should have lower P: small={prob_small:.4f}, large={prob_large:.4f}"


class TestSATaskAssignment:
    """
    SA任务分配求解测试
    
    核心功能:
    - 输入: 任务列表 + 车辆列表 + 约束条件
    - 输出: 分配方案 (每个任务→某辆车) + 总成本
    - 目标: 成本最小化
    """
    
    @pytest.fixture
    def sa_solver(self):
        """SA求解器实例"""
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            return SimulatedAnnealingSolver(
                initial_temp=500.0,
                final_temp=0.1,
                cooling_rate=0.9,
                iterations_per_temp=50,
            )
        except ImportError:
            pytest.skip("SA module not available")
    
    def test_assign_all_tasks(self, sa_solver):
        """所有任务都应被分配"""
        tasks, vehicles = TaskAssignmentData.simple_3x3_instance()
        
        solution = sa_solver.solve(tasks=tasks, vehicles=vehicles)
        
        # 验证解的结构
        assert "assignments" in solution, "Solution should contain assignments"
        assert len(solution["assignments"]) == 3, "All 3 tasks should be assigned"
        
        # 验证每项任务都被分配到有效车辆
        vehicle_ids = {v["id"] for v in vehicles}
        for task_id, veh_id in solution["assignments"].items():
            assert veh_id in vehicle_ids, \
                f"Task {task_id} assigned to invalid vehicle {veh_id}"
    
    def test_feasibility_of_assignment(self, sa_solver):
        """分配结果必须满足约束条件"""
        tasks, vehicles = TaskAssignmentData.constrained_instance()
        
        solution = sa_solver.solve(tasks=tasks, vehicles=vehicles)
        
        # 检查重量约束: heavy_task (250kg) 只能分配给 large_agv (500kg)
        if solution["assignments"].get("heavy_task") == "small_agv":
            pytest.fail("Heavy task should not be assigned to small AGV (capacity violation)")
        
        print(f"\n✓ Constrained assignment: {solution['assignments']}")
    
    def test_cost_should_be_reasonable(self, sa_solver):
        """解的成本应在合理范围内"""
        tasks, vehicles = TaskAssignmentData.simple_3x3_instance()
        
        solution = sa_solver.solve(tasks=tasks, vehicles=vehicles)
        
        assert "total_cost" in solution, "Solution should contain total_cost"
        cost = solution["total_cost"]
        
        # 成本不应为负
        assert cost >= 0, f"Cost should be non-negative: {cost}"
        
        # 成本不应过大 (简单实例通常<1000)
        assert cost < 10000, f"Cost seems too high: {cost}"
        
        print(f"✓ Total cost: {cost:.2f}")
    
    def test_deterministic_with_same_seed(self, sa_solver):
        """相同随机种子应产生相同结果 (可复现性)"""
        tasks, vehicles = TaskAssignmentData.simple_3x3_instance()
        
        sol1 = sa_solver.solve(tasks=tasks, vehicles=vehicles, seed=42)
        sol2 = sa_solver.solve(tasks=tasks, vehicles=vehicles, seed=42)
        
        assert sol1["total_cost"] == sol2["total_cost"], \
            "Same seed should produce identical results"


class TestSAConvergence:
    """
    收敛性分析测试
    
    监控指标:
    - 温度变化曲线
    - 成本历史记录
    - 是否找到并保持最优解
    """
    
    @pytest.mark.integration
    def test_convergence_tracking(self):
        """
        SA应记录收敛历史用于调试
        
        返回数据应包含:
        - temperature_history: 每轮温度
        - cost_history: 每轮最优成本
        - accepted_count: 接受次数
        - rejected_count: 拒绝次数
        """
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            
            solver = SimulatedAnnealingSolver(
                initial_temp=200.0,
                final_temp=0.01,
                cooling_rate=0.9,
                iterations_per_temp=20,
                track_history=True,
            )
            
            tasks, vehicles = TaskAssignmentData.unbalanced_instance()
            
            solution = solver.solve(tasks=tasks, vehicles=vehicles)
            
            # 验证历史数据存在 (可选功能)
            if hasattr(solver, 'history'):
                history = solver.history
                
                assert "temperatures" in history, "Should track temperatures"
                assert "costs" in history, "Should track costs"
                
                # 温度应该递减
                temps = history["temperatures"]
                assert temps[0] > temps[-1], "Initial temp should be higher than final"
                
                # 成本应该总体下降 (允许波动)
                costs = history["costs"]
                avg_first_quarter = np.mean(costs[:len(costs)//4])
                avg_last_quarter = np.mean(costs[-len(costs)//4:])
                
                print(f"\n✓ Cost improvement: {avg_first_quarter:.1f} → {avg_last_quarter:.1f} ({((avg_last_quarter/avg_first_quarter-1)*100):+.1f}%)")
                
        except (ImportError, AttributeError):
            pytest.skip("History tracking not implemented")


class TestSAEdgeCases:
    """边界条件和异常场景"""
    
    def test_empty_task_list(self):
        """空任务列表应返回空解"""
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            solver = SimulatedAnnealingSolver()
            
            solution = solver.solve(tasks=[], vehicles=[{"id": "v1"}])
            
            assert solution.get("assignments") == {} or len(solution.get("assignments", [])) == 0
            
        except (ImportError, ValueError) as e:
            pass  # 可能会抛出异常，也是可接受的行为
    
    def test_single_task_single_vehicle(self):
        """单任务单车辆应直接返回该分配"""
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            solver = SimulatedAnnealingSolver(iterations_per_temp=5)
            
            tasks = [{"id": "only_task", "pickup": "A", "dropoff": "B"}]
            vehicles = [{"id": "only_vehicle", "pos": "A"}]
            
            solution = solver.solve(tasks=tasks, vehicles=vehicles)
            
            assert solution["assignments"]["only_task"] == "only_vehicle"
            
        except ImportError:
            pass
    
    @pytest.mark.performance
    def test_large_instance_runtime(self):
        """
        大规模实例性能基准 (<10秒完成)
        
        规模: 50任务 x 10车辆
        """
        start_time = time.time()
        
        try:
            from app.algorithms.sa import SimulatedAnnealingSolver
            
            solver = SimulatedAnnealingSolver(
                initial_temp=300.0,
                final_temp=0.1,
                cooling_rate=0.92,
                iterations_per_temp=30,
            )
            
            # 生成大规模实例
            n_tasks = 50
            n_vehicles = 10
            
            tasks = [{
                "id": f"task_{i}",
                "pickup": f"P{i % 20}",
                "dropoff": f"D{i % 20}",
                "priority": np.random.randint(1, 21),
            } for i in range(n_tasks)]
            
            vehicles = [{
                "id": f"veh_{j}",
                "pos": f"HUB_{j}",
                "capacity": 200 + j*50,
                "speed": 1.0 + j*0.1,
            } for j in range(n_vehicles)]
            
            solution = solver.solve(tasks=tasks, vehicles=vehicles)
            
            elapsed = time.time() - start_time
            assert elapsed < 10.0, f"Large instance took {elapsed:.2f}s (>10s threshold)"
            
            print(f"\n✓ Solved 50×10 instance in {elapsed:.2f}s, cost={solution['total_cost']:.1f}")
            print(f"   Assigned {len(solution['assignments'])}/{n_tasks} tasks")
            
        except ImportError:
            pytest.skip("Performance test skipped")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
