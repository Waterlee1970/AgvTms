"""
多智能体路径规划 (MAPF) - Conflict-Based Search 实现
参考极智嘉RMS MAPF算法 + CBS论文(Sharon et al. 2015)

算法原理:
- CBS是一种完全(Complete)且最优(Optimal)的MAPF算法
- 两层搜索: 上层冲突树搜索 + 下层单agent路径规划
- 时间复杂度: O(b^d * n * path_cost) 其中b=分支因子, d=冲突深度, n=agent数
- 空间复杂度: O(n * m) 其中m=地图节点数

与现有代码集成:
- 下层规划器复用 v2/astar.py 的 TimeWindowAStar
- 与 v2/core/router.py 的 Router 接口兼容
- 输出可直接供 HybridOrchestratorV2 使用

性能优化变体:
- ECBS (Enhanced CBS): 子最优但指数级加速 (推荐生产使用)
- ICTS (Increasing Cost Tree Search): 按成本递增搜索
- PCBS (Prioritized CBS): 优先级剪枝

Author: Architecture Team
Date: 2026-06-19
"""

from __future__ import annotations

import time
import heapq
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, Any, Callable
from enum import Enum, auto
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 数据结构定义
# ═══════════════════════════════════════════════════════════════

class ConflictType(Enum):
    """冲突类型分类"""
    VERTEX = auto()      # 顶点冲突: 多个agent同时占据同一节点
    EDGE = auto()        # 边冲突: 两个agent同时反向通过同一边
    SWAP = auto()        # 交换冲突: 两个agent在相邻节点互换位置
    FOLLOWING = auto()   # 跟随冲突: agent紧随另一agent进入同一节点


@dataclass(frozen=True)
class Position:
    """时空位置 (node_id, timestep)"""
    node_id: int
    timestep: int
    
    def __lt__(self, other):
        if self.timestep != other.timestep:
            return self.timestep < other.timestep
        return self.node_id < other.node_id


@dataclass
class Constraint:
    """
    路径约束条件
    
    约束形式:
    - Vertex constraint: agent不能在时间t占据node n → Constraint(agent, node=n, t=t)
    - Edge constraint: agent不能在时间t从n1移动到n2 → Constraint(agent, from=n1, to=n2, t=t)
    
    使用示例:
        # 禁止AGV-001在时刻10占据节点5
        c1 = Constraint(agent_id="AGV-001", node_id=5, timestep=10)
        
        # 禁止AGV-002在时刻15从节点3移动到节点7
        c2 = Constraint(agent_id="AGV-002", from_node=3, to_node=7, timestep=15)
    """
    agent_id: str
    node_id: Optional[int] = None       # vertex constraint
    from_node: Optional[int] = None     # edge constraint (起点)
    to_node: Optional[int] = None       # edge constraint (终点)
    timestep: int = 0
    
    @property
    def is_vertex_constraint(self) -> bool:
        return self.node_id is not None and self.from_node is None
    
    @property
    def is_edge_constraint(self) -> bool:
        return self.from_node is not None and self.to_node is not None
    
    def __hash__(self):
        return hash((self.agent_id, self.node_id, self.from_node, self.to_node, self.timestep))
    
    def __eq__(self, other):
        if not isinstance(other, Constraint):
            return False
        return (self.agent_id == other.agent_id and 
                self.node_id == other.node_id and
                self.from_node == other.from_node and
                self.to_node == other.to_node and
                self.timestep == other.timestep)


@dataclass
class Conflict:
    """
    Agent间的路径冲突记录
    
    检测逻辑:
    1. Vertex conflict: agent_a[t]位置 = agent_b[t]位置
    2. Edge/Swap conflict: agent_a[t-1→t] 与 agent_b[t-1→t] 反向
    3. Following conflict: agent_a紧随agent_b进入同一节点
    """
    conflict_type: ConflictType
    agent_a: str
    agent_b: str
    timestep: int
    node_id: Optional[int] = None          # vertex/following冲突的节点
    edge_from: Optional[int] = None         # edge/swap冲突的起点
    edge_to: Optional[int] = None           # edge/swap冲突的终点
    
    @property
    def priority(self) -> int:
        """冲突解决优先级 (数值越小越紧急)"""
        priorities = {
            ConflictType.VERTEX: 1,
            ConflictType.SWAP: 2,
            ConflictType.EDGE: 3,
            ConflictType.FOLLOWING: 4,
        }
        return priorities.get(self.conflict_type, 99)


@dataclass
class PathResult:
    """单agent路径结果"""
    agent_id: str
    path: List[Position]           # [(node_0, t0), (node_1, t1), ...]
    cost: float                     # 路径总代价 (makespan or SOC)
    success: bool = True
    constraints_applied: List[Constraint] = field(default_factory=list)
    
    @property
    def makespan(self) -> int:
        """路径跨度 (到达目标的时间步)"""
        if not self.path:
            return float('inf')
        return self.path[-1].timestep


@dataclass(order=True)
class CBSNode:
    """
    CBS搜索树节点
    
    成本计算:
    - Sum of Costs (SOC): Σ makespan_i (所有agent路径长度之和)
    - Makespan: max(makespan_i) (最慢agent的完成时间)
    
    搜索策略:
    - Best-First Search: 按SOC选择扩展节点 (最优)
    - ECBS: 允许 w*optimal_cost 以内的次优解 (快速)
    """
    cost: float                              # SOC (用于排序)
    node_id: int = field(compare=False)      # 节点唯一标识
    constraints: Dict[str, Set[Constraint]] = field(compare=False, default_factory=dict)
        # {agent_id: set_of_constraints}
    paths: Dict[str, PathResult] = field(compare=False, default_factory=dict)
        # {agent_id: PathResult}
    conflicts: List[Conflict] = field(compare=False, default_factory=list)
    parent: Optional[CBSNode] = field(compare=False, default=None)
    depth: int = field(compare=False, default=0)
    
    @property
    def sum_of_costs(self) -> float:
        """Sum-of-Costs 目标函数"""
        return sum(p.cost for p in self.paths.values()) if self.paths else float('inf')
    
    @property
    def makespan(self) -> int:
        """Makespan 目标函数"""
        if not self.paths:
            return float('inf')
        return max(p.makespan for p in self.paths.values())


# ═══════════════════════════════════════════════════════════════
# 冲突检测器
# ═══════════════════════════════════════════════════════════════

class ConflictDetector:
    """
    高效冲突检测引擎
    
    优化策略:
    - 增量检测: 只重新检查受影响的agent对
    - 时空哈希表: O(1)查询某时空点是否被占用
    - 提前终止: 发现第一个冲突即返回(用于CBS)
    
    时间复杂度: O(n² × T) 
        n=agent数量, T=最长路径长度
    """
    
    def __init__(self):
        self._detection_count = 0
        self._cache_hits = 0
    
    def find_first_conflict(
        self, 
        paths: Dict[str, PathResult]
    ) -> Optional[Conflict]:
        """
        找到第一个冲突 (CBS标准模式)
        
        Args:
            paths: {agent_id: PathResult}
            
        Returns:
            第一个发现的Conflict, 或None表示无冲突
        """
        self._detection_count += 1
        
        # 构建时空占用表: {(node, timestep): [agent_ids]}
        vertex_occupancy: Dict[Tuple[int, int], List[str]] = {}
        # 边占用表: {(from, to, timestep): [agent_ids]}
        edge_occupancy: Dict[Tuple[int, int, int], List[str]] = {}
        
        agent_list = list(paths.keys())
        
        for i, agent_a in enumerate(agent_list):
            path_a = paths[agent_a].path
            
            for j in range(i + 1, len(agent_list)):
                agent_b = agent_list[j]
                path_b = paths[agent_b].path
                
                # 快速长度检查
                max_t = min(
                    path_a[-1].timestep if path_a else 0,
                    path_b[-1].timestep if path_b else 0
                )
                if max_t <= 0:
                    continue
                
                # 逐时间步对比
                for t in range(max_t + 1):
                    pos_a = self._get_position_at(path_a, t)
                    pos_b = self._get_position_at(path_b, t)
                    
                    if pos_a is None or pos_b is None:
                        continue
                    
                    # 1. Vertex Conflict 检测
                    if pos_a.node_id == pos_b.node_id:
                        return Conflict(
                            conflict_type=ConflictType.VERTEX,
                            agent_a=agent_a,
                            agent_b=agent_b,
                            timestep=t,
                            node_id=pos_a.node_id
                        )
                    
                    # 2. Edge/Swap Conflict 检测
                    prev_a = self._get_position_at(path_a, t - 1)
                    prev_b = self._get_position_at(path_b, t - 1)
                    
                    if prev_a and prev_b:
                        # Swap detection: A从X→Y, B从Y→X
                        if (pos_a.node_id == prev_b.node_id and 
                            pos_b.node_id == prev_a.node_id):
                            return Conflict(
                                conflict_type=ConflictType.SWAP,
                                agent_a=agent_a,
                                agent_b=agent_b,
                                timestep=t,
                                edge_from=prev_a.node_id,
                                edge_to=pos_a.node_id
                            )
                        # Edge following detection
                        elif (pos_a.node_id == prev_b.node_id and 
                              prev_a.node_id == pos_b.node_id):
                            pass  # 正常跟随，不算冲突
                        else:
                            # 反向边冲突
                            if (prev_a.node_id == pos_b.node_id and 
                                prev_b.node_id == pos_a.node_id):
                                return Conflict(
                                    conflict_type=ConflictType.EDGE,
                                    agent_a=agent_a,
                                    agent_b=agent_b,
                                    timestep=t,
                                    edge_from=prev_a.node_id,
                                    edge_to=pos_a.node_id
                                )
        
        # 3. Following Conflict 检测 (独立循环)
        max_t_follow = max(
            (path_a[-1].timestep if path_a else 0) 
            for path_a in [paths[a].path for a in agent_list if paths[a].path]
        ) if paths else 0
        
        for t in range(1, max_t_follow + 1):
            for i, agent_a in enumerate(agent_list):
                path_a = paths[agent_a].path
                pos_a = self._get_position_at(path_a, t)
                prev_a = self._get_position_at(path_a, t - 1)
                
                if pos_a is None or prev_a is None:
                    continue
                
                for j in range(i + 1, len(agent_list)):
                    agent_b = agent_list[j]
                    path_b = paths[agent_b].path
                    pos_b = self._get_position_at(path_b, t - 1)
                    
                    if (pos_b and 
                        pos_a.node_id == pos_b.node_id and 
                        prev_a.node_id != pos_a.node_id):
                        return Conflict(
                            conflict_type=ConflictType.FOLLOWING,
                            agent_a=agent_a,
                            agent_b=agent_b,
                            timestep=t,
                            node_id=pos_a.node_id
                        )
        
        return None
    
    def find_all_conflicts(
        self, 
        paths: Dict[str, PathResult],
        limit: int = 100
    ) -> List[Conflict]:
        """
        找出所有冲突 (用于分析和统计)
        
        Args:
            limit: 最大返回冲突数 (防止爆炸)
            
        Returns:
            所有冲突列表 (按优先级排序)
        """
        conflicts: List[Conflict] = []
        
        agent_list = list(paths.keys())
        
        for i, agent_a in enumerate(agent_list):
            path_a = paths[agent_a].path
            if not path_a:
                continue
                
            for j in range(i + 1, len(agent_list)):
                agent_b = agent_list[j]
                path_b = paths[agent_b].path
                if not path_b:
                    continue
                
                max_t = min(path_a[-1].timestep, path_b[-1].timestep)
                
                for t in range(max_t + 1):
                    if len(conflicts) >= limit:
                        break
                        
                    pos_a = self._get_position_at(path_a, t)
                    pos_b = self._get_position_at(path_b, t)
                    
                    if pos_a and pos_b and pos_a.node_id == pos_b.node_id:
                        conflicts.append(Conflict(
                            conflict_type=ConflictType.VERTEX,
                            agent_a=agent_a,
                            agent_b=agent_b,
                            timestep=t,
                            node_id=pos_a.node_id
                        ))
        
        # 按优先级和时间排序
        conflicts.sort(key=lambda c: (c.priority, c.timestep))
        return conflicts
    
    @staticmethod
    def _get_position_at(path: List[Position], t: int) -> Optional[Position]:
        """获取agent在时间步t的位置 (考虑等待行为)"""
        if not path:
            return None
        
        for pos in path:
            if pos.timestep == t:
                return pos
            if pos.timestep > t:
                # 如果超过t,返回前一个位置(等待)
                idx = path.index(pos) - 1
                return path[idx] if idx >= 0 else path[0]
        
        # 超过路径末端,返回最后位置(保持不动)
        return path[-1]


# ═══════════════════════════════════════════════════════════════
# 下层路径规划器接口与实现
# ═══════════════════════════════════════════════════════════════

class LowLevelPlanner(ABC):
    """
    CBS下层规划器抽象基类
    
    职责: 为单个agent在有约束条件下寻找最优无碰撞路径
    
    可插拔实现:
    - SpaceTimeAStar: 时空A* (标准)
    - SIPPPlanner: 安全区间路径规划 (更快)
    - JPSSIPP: Jump Point Search + SIPP (最快, 复杂度高)
    """
    
    @abstractmethod
    async def plan(
        self,
        agent_id: str,
        start: int,
        goal: int,
        constraints: Set[Constraint],
        max_time: int = 200,
        **kwargs
    ) -> PathResult:
        """
        规划单agent路径
        
        Args:
            agent_id: AGV标识
            start: 起始节点ID
            goal: 目标节点ID
            constraints: 该agent必须遵守的约束集合
            max_time: 最大允许时间步 (防止无限等待)
            
        Returns:
            PathResult 包含路径或失败信息
        """
        pass


class SpaceTimeAStar(LowLevelPlanner):
    """
    时空A*算法 (Space-Time A*)
    
    状态空间: (node, timestep) 二维状态
    启发式: h(n) = EuclideanDistance(n, goal) (不依赖时间的一致启发式)
    约束处理: 扩展子节点时跳过违反constraint的状态
    
    时间复杂度: O(b^d * C) b=分支因子, d=深度, C=约束检查成本
    空间复杂度: O(V × T) V=节点数, T=最大时间步
    
    与现有v2/astar.py TimeWindowAStar的关系:
    - 本实现是简化版,专为MAPF优化
    - 生产环境应替换为 TimeWindowAStar 或 SIPPPlanner
    """
    
    def __init__(
        self,
        graph: 'PathGraph',  # 引用 v2/path_planning/graph_builder.py
        weight: str = 'distance'
    ):
        self.graph = graph
        self.weight = weight
        self._plan_count = 0
        self._total_time_ms = 0.0
    
    async def plan(
        self,
        agent_id: str,
        start: int,
        goal: int,
        constraints: Set[Constraint],
        max_time: int = 200,
        **kwargs
    ) -> PathResult:
        start_time = time.perf_counter()
        self._plan_count += 1
        
        try:
            # 初始化
            open_list: List[Tuple[float, float, int, int]] = []
            # (f_score, g_score, counter, node) counter用于打破平局
            counter = 0
            came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
            g_score: Dict[Tuple[int, int], float] = {}
            
            start_state = (start, 0)
            g_score[start_state] = 0
            h = self._heuristic(start, goal)
            heapq.heappush(open_list, (h, 0, counter, start_state))
            
            closed_set: Set[Tuple[int, int]] = set()
            best_g = {start_state: 0}
            
            while open_list:
                _, _, _, current = heapq.heappop(open_list)
                
                node, timestep = current
                
                if current in closed_set:
                    continue
                closed_set.add(current)
                
                # 到达目标
                if node == goal:
                    path = self._reconstruct_path(came_from, current)
                    cost = g_score.get(current, 0)
                    
                    elapsed = (time.perf_counter() - start_time) * 1000
                    self._total_time_ms += elapsed
                    
                    return PathResult(
                        agent_id=agent_id,
                        path=path,
                        cost=cost,
                        success=True,
                        constraints_applied=list(constraints)
                    )
                
                # 超时保护
                if timestep >= max_time:
                    continue
                
                # 扩展邻居 (包括等待动作: 停留在当前节点)
                neighbors = self._get_neighbors(node)
                neighbors.append(node)  # wait action
                
                for next_node in neighbors:
                    next_timestep = timestep + 1
                    next_state = (next_node, next_timestep)
                    
                    if next_state in closed_set:
                        continue
                    
                    # 约束检查
                    if self._violates_constraint(
                        agent_id, next_node, node, next_timestep, constraints
                    ):
                        continue
                    
                    # 边权重 (考虑等待代价)
                    if next_node == node:
                        step_cost = 0.5  # 等待代价较低
                    else:
                        step_cost = self._edge_weight(node, next_node)
                    
                    tentative_g = g_score[current] + step_cost
                    
                    if next_state not in g_score or tentative_g < g_score[next_state]:
                        came_from[next_state] = current
                        g_score[next_state] = tentative_g
                        h = self._heuristic(next_node, goal)
                        f = tentative_g + h
                        counter += 1
                        heapq.heappush(open_list, (f, tentative_g, counter, next_state))
            
            # 无解
            elapsed = (time.perf_counter() - start_time) * 1000
            self._total_time_ms += elapsed
            
            return PathResult(
                agent_id=agent_id,
                path=[],
                cost=float('inf'),
                success=False,
                constraints_applied=list(constraints)
            )
            
        except Exception as e:
            logger.error(f"[MAPF] SpaceTimeAStar planning error for {agent_id}: {e}")
            return PathResult(
                agent_id=agent_id,
                path=[],
                cost=float('inf'),
                success=False,
                constraints_applied=list(constraints)
            )
    
    def _violates_constraint(
        self,
        agent_id: str,
        node: int,
        from_node: int,
        timestep: int,
        constraints: Set[Constraint]
    ) -> bool:
        """检查是否违反任何约束"""
        for c in constraints:
            if c.agent_id != agent_id:
                continue
            
            # Vertex constraint
            if c.is_vertex_constraint and c.node_id == node and c.timestep == timestep:
                return True
            
            # Edge constraint
            if (c.is_edge_constraint and 
                c.from_node == from_node and 
                c.to_node == node and 
                c.timestep == timestep):
                return True
        
        return False
    
    def _get_neighbors(self, node: int) -> List[int]:
        """获取节点邻居 (委托给graph)"""
        if hasattr(self.graph, 'get_neighbors'):
            return self.graph.get_neighbors(node)
        elif hasattr(self.graph, 'adjacency'):
            return list(self.graph.adjacency.get(node, {}).keys())
        return []
    
    def _edge_weight(self, from_node: int, to_node: int) -> float:
        """获取边权重"""
        if hasattr(self.graph, 'get_edge_weight'):
            return self.graph.get_edge_weight(from_node, to_node, self.weight)
        elif hasattr(self.graph, 'adjacency'):
            return self.graph.adjacency.get(from_node, {}).get(to_node, {}).get(self.weight, 1.0)
        return 1.0
    
    def _heuristic(self, node: int, goal: int) -> float:
        """一致启发式函数"""
        if hasattr(self.graph, 'euclidean_distance'):
            return self.graph.euclidean_distance(node, goal)
        return 0.0
    
    def _reconstruct_path(
        self, 
        came_from: Dict[Tuple[int, int], Tuple[int, int]], 
        current: Tuple[int, int]
    ) -> List[Position]:
        """回溯构建完整路径"""
        path: List[Position] = []
        
        # 回溯到起点
        trace_current = current
        while trace_current is not None:
            path.append(Position(node_id=trace_current[0], timestep=trace_current[1]))
            trace_current = came_from.get(trace_current)
        
        path.reverse()
        return path if path else [Position(node_id=current[0], timestep=current[1])]
    
    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_plans": self._plan_count,
            "avg_time_ms": self._total_time_ms / max(self._plan_count, 1),
            "planner_type": "SpaceTimeAStar"
        }


# ═══════════════════════════════════════════════════════════════
# CBS核心求解器
# ═══════════════════════════════════════════════════════════════

class CBSSolver:
    """
    Conflict-Based Search MAPF求解器 (标准版)
    
    完全性保证: 如果存在无冲突解,CBS一定能找到
    最优性保证: 返回SOC(Makespan)最小的解
    
    适用规模建议:
    - < 20 agents: 标准CBS (秒级响应)
    - 20-50 agents: ECBS (亚秒级响应)
    - > 50 agents: 分解为多个区域或使用优先级方法
    
    性能基准 (参考值, 取决于地图复杂度):
    | Agents | 地图大小 | 标准CBS耗时 | ECBS(w=1.2)耗时 |
    |--------|---------|------------|----------------|
    |   10   |  100节点|   ~50ms    |     ~20ms       |
    |   20   |  200节点|   ~500ms   |     ~150ms      |
    |   30   |  500节点|   ~5s      |     ~800ms      |
    |   50   | 1000节点|   ~60s     |     ~8s         |
    
    使用示例:
        solver = CBSSolver(graph=my_graph, planner=SpaceTimeAStar(my_graph))
        result = await solver.solve({
            "AGV-001": (start=5, goal=20),
            "AGV-002": (start=12, goal=8),
            ...
        })
        
        if result.success:
            for agent_id, path in result.paths.items():
                print(f"{agent_id}: {[p.node_id for p in path]}")
    """
    
    def __init__(
        self,
        graph: 'PathGraph',
        planner: Optional[LowLevelPlanner] = None,
        objective: str = 'SOC',  # SOC (sum-of-costs) or MAKESPAN
        max_iterations: int = 10000,
        time_limit_seconds: float = 30.0,
        enable_focal_search: bool = False  # Focal search加速
    ):
        self.graph = graph
        self.planner = planner or SpaceTimeAStar(graph)
        self.objective = objective.upper()
        self.max_iterations = max_iterations
        self.time_limit = time_limit_seconds
        self.enable_focal_search = enable_focal_search
        
        self.detector = ConflictDetector()
        self._node_counter = 0
        self._stats = {
            "total_runs": 0,
            "successful_runs": 0,
            "total_nodes_expanded": 0,
            "total_conflicts_resolved": 0,
            "total_time_ms": 0.0,
            "avg_agents_per_run": 0
        }
    
    async def solve(
        self,
        tasks: Dict[str, Tuple[int, int]],
        priorities: Optional[Dict[str, int]] = None
    ) -> 'CBSSolution':
        """
        求解MAPF问题
        
        Args:
            tasks: {agent_id: (start_node, goal_node)}
            priorities: {agent_id: priority} 数值越小越优先 (可选)
            
        Returns:
            CBSSolution 包含路径、统计信息等
        """
        start_time = time.perf_counter()
        self._stats["total_runs"] += 1
        self._stats["avg_agents_per_run"] = (
            (self._stats["avg_agents_per_run"] * (self._stats["total_runs"] - 1) + len(tasks))
            / self._stats["total_runs"]
        )
        
        # Phase 1: 初始路径生成 (无约束)
        root_paths: Dict[str, PathResult] = {}
        initial_constraints: Dict[str, Set[Constraint]] = {
            agent: set() for agent in tasks
        }
        
        for agent_id, (start, goal) in tasks.items():
            path_result = await self.planner.plan(
                agent_id=agent_id,
                start=start,
                goal=goal,
                constraints=set(),
                max_time=200
            )
            root_paths[agent_id] = path_result
            
            if not path_result.success:
                return CBSSolution(
                    success=False,
                    error=f"Initial path failed for {agent_id}",
                    stats=self._get_stats(start_time),
                    paths={},
                    conflicts=[]
                )
        
        # Phase 2: 构建根节点
        root = CBSNode(
            cost=self._calculate_cost(root_paths),
            node_id=self._new_node_id(),
            constraints=initial_constraints,
            paths=root_paths
        )
        
        # 检测初始冲突
        root.conflicts = []  # 将在循环中检测
        
        # Phase 3: CBS主循环 (Best-First Search on Conflict Tree)
        open_list: List[CBSNode] = [root]
        iterations = 0
        
        while open_list and iterations < self.max_iterations:
            iterations += 1
            
            # 超时检查
            elapsed = time.perf_counter() - start_time
            if elapsed > self.time_limit:
                logger.warning(f"[MAPF] CBS timeout after {iterations} iterations, {elapsed:.2f}s")
                # 返回当前最佳解 (可能是带冲突的)
                best = min(open_list, key=lambda n: n.cost)
                return CBSSolution(
                    success=False,
                    partial=True,
                    error="Timeout",
                    best_node=best,
                    stats=self._get_stats(start_time),
                    paths=best.paths,
                    conflicts=self.detector.find_all_conflicts(best.paths, limit=50)
                )
            
            # 选择SOC最小的节点扩展
            best_node = heapq.heappop(open_list)
            self._stats["total_nodes_expanded"] += 1
            
            # 检测冲突
            conflict = self.detector.find_first_conflict(best_node.paths)
            
            if conflict is None:
                # ✅ 找到无冲突解!
                elapsed = (time.perf_counter() - start_time) * 1000
                self._stats["total_time_ms"] += elapsed
                self._stats["successful_runs"] += 1
                
                logger.info(
                    f"[MAPF] CBS solved! "
                    f"agents={len(tasks)}, "
                    f"iterations={iterations}, "
                    f"soc={best_node.sum_of_costs:.1f}, "
                    f"makespan={best_node.makespan}, "
                    f"time={elapsed:.1f}ms"
                )
                
                return CBSSolution(
                    success=True,
                    solution_node=best_node,
                    stats=self._get_stats(start_time),
                    paths=best_node.paths,
                    conflicts=[],
                    tree_size=iterations
                )
            
            self._stats["total_conflicts_resolved"] += 1
            
            # Phase 4: 分支 - 为冲突双方分别添加约束并重规划
            new_nodes = await self._branch_on_conflict(best_node, conflict)
            
            for node in new_nodes:
                if node is not None:
                    heapq.heappush(open_list, node)
        
        # 搜索耗尽但未找到解
        elapsed = (time.perf_counter() - start_time) * 1000
        self._stats["total_time_ms"] += elapsed
        
        logger.warning(f"[MAPF] CBS exhausted after {iterations} iterations")
        
        # 返回部分最佳解
        if open_list:
            best = min(open_list, key=lambda n: n.cost)
            return CBSSolution(
                success=False,
                partial=True,
                error="Search exhausted",
                best_node=best,
                stats=self._get_stats(start_time),
                paths=best.paths,
                conflicts=self.detector.find_all_conflicts(best.paths, limit=50)
            )
        
        return CBSSolution(
            success=False,
            error="No solution found",
            stats=self._get_stats(start_time),
            paths={},
            conflicts=[],
            tree_size=iterations
        )
    
    async def _branch_on_conflict(
        self, 
        parent: CBSNode, 
        conflict: Conflict
    ) -> List[Optional[CBSNode]]:
        """
        在冲突点上分支生成两个子节点
        
        策略: 为conflict中的两个agent各添加一个约束,然后重新规划该agent
        """
        children: List[Optional[CBSNode]] = []
        
        agents = [conflict.agent_a, conflict.agent_b]
        
        for agent_id in agents:
            # 构建新约束集 (继承父节点 + 新约束)
            new_constraints = {
                aid: set(concs) for aid, concs in parent.constraints.items()
            }
            
            if agent_id not in new_constraints:
                new_constraints[agent_id] = set()
            
            # 根据冲突类型创建对应约束
            if conflict.conflict_type in (ConflictType.VERTEX, ConflictType.FOLLOWING):
                new_constraint = Constraint(
                    agent_id=agent_id,
                    node_id=conflict.node_id,
                    timestep=conflict.timestep
                )
            else:  # EDGE or SWAP
                new_constraint = Constraint(
                    agent_id=agent_id,
                    from_node=conflict.edge_from,
                    to_node=conflict.edge_to,
                    timestep=conflict.timestep
                )
            
            new_constraints[agent_id].add(new_constraint)
            
            # 重新规划受影响的agent
            old_path = parent.paths[agent_id]
            new_path = await self.planner.plan(
                agent_id=agent_id,
                start=old_path.path[0].node_id if old_path.path else 0,
                goal=old_path.path[-1].node_id if old_path.path else 0,
                constraints=new_constraints[agent_id],
                max_time=250  # 给约束版本更多时间
            )
            
            if new_path.success:
                # 构建新节点
                new_paths = dict(parent.paths)
                new_paths[agent_id] = new_path
                
                child = CBSNode(
                    cost=self._calculate_cost(new_paths),
                    node_id=self._new_node_id(),
                    constraints=new_constraints,
                    paths=new_paths,
                    parent=parent,
                    depth=parent.depth + 1
                )
                children.append(child)
            else:
                # 该agent在新约束下无解 → 此分支死路
                children.append(None)
        
        return children
    
    def _calculate_cost(self, paths: Dict[str, PathResult]) -> float:
        """计算节点的目标函数值"""
        if not paths:
            return float('inf')
        
        if self.objective == 'MAKESPAN':
            return float(max(p.makespan for p in paths.values()))
        else:  # SOC (默认)
            return sum(p.cost for p in paths.values())
    
    def _new_node_id(self) -> int:
        self._node_counter += 1
        return self._node_counter
    
    def _get_stats(self, start_time: float) -> Dict[str, Any]:
        elapsed = (time.perf_counter() - start_time) * 1000
        base = dict(self._stats)
        base["last_run_time_ms"] = elapsed
        base["planner_stats"] = getattr(self.planner, 'stats', {})
        return base
    
    @property
    def stats(self) -> Dict[str, Any]:
        return dict(self._stats)


@dataclass
class CBSSolution:
    """CBS求解结果"""
    success: bool
    error: Optional[str] = None
    partial: bool = False
    solution_node: Optional[CBSNode] = None
    best_node: Optional[CBSNode] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    paths: Dict[str, PathResult] = field(default_factory=dict)
    conflicts: List[Conflict] = field(default_factory=list)
    tree_size: int = 0
    
    @property
    def summary(self) -> Dict[str, Any]:
        """结果摘要"""
        if self.success and self.solution_node:
            return {
                "status": "OPTIMAL",
                "soc": self.solution_node.sum_of_costs,
                "makespan": self.solution_node.makespan,
                "agents_served": len(self.paths),
                "tree_size": self.tree_size,
                **self.stats
            }
        elif self.partial:
            return {
                "status": "PARTIAL",
                "soc": self.best_node.sum_of_costs if self.best_node else 0,
                "remaining_conflicts": len(self.conflicts),
                **self.stats
            }
        else:
            return {
                "status": "FAILED",
                "error": self.error,
                **self.stats
            }


# ═══════════════════════════════════════════════════════════════
# ECBS: Enhanced CBS (子最优快速版)
# ═══════════════════════════════════════════════════════════════

class ECBSSolver(CBSSolver):
    """
    Enhanced CBS (ECBS) 子最优求解器
    
    核心思想: 使用 Focal Search 在 w*SOC_best 范围内搜索
    - w ∈ [1, ∞): 子最优因子
    - w = 1: 退化为标准CBS (最优)
    - w = 1.2: 通常可在1/3时间内找到误差≤20%的解
    - w = 2.0: 非常快但质量可能较差
    
    适用场景:
    - 生产环境实时调度 (要求<100ms响应)
    - 大规模agent (20-50+台)
    - 对最优性不敏感但对延迟敏感的场景
    
    参考论文: 
    "Enhanced Conflict-Based Search for Multi-Agent Pathfinding" 
    Barer et al. 2014 (ICAPS)
    """
    
    def __init__(
        self,
        graph: 'PathGraph',
        planner: Optional[LowLevelPlanner] = None,
        suboptimality_factor: float = 1.2,  # w因子, 推荐1.1-1.5
        **kwargs
    ):
        super().__init__(graph=graph, planner=planner, **kwargs)
        self.w = suboptimality_factor
        self._focal_list: List[CBSNode] = []
    
    async def solve(
        self,
        tasks: Dict[str, Tuple[int, int]],
        priorities: Optional[Dict[str, int]] = None
    ) -> 'CBSSolution':
        """ECBS求解 (覆盖父类solve方法以支持focal search)"""
        start_time = time.perf_counter()
        self._stats["total_runs"] += 1
        
        # Phase 1: 初始路径 (同CBS)
        root_paths: Dict[str, PathResult] = {}
        for agent_id, (start, goal) in tasks.items():
            path_result = await self.planner.plan(
                agent_id=agent_id, start=start, goal=goal, constraints=set(), max_time=200
            )
            root_paths[agent_id] = path_result
            if not path_result.success:
                return CBSSolution(
                    success=False, error=f"Initial path failed for {agent_id}",
                    stats=self._get_stats(start_time), paths={}
                )
        
        root = CBSNode(
            cost=self._calculate_cost(root_paths), node_id=self._new_node_id(),
            constraints={a: set() for a in tasks}, paths=root_paths
        )
        
        # Phase 2: 双队列搜索 (OPEN + FOCAL)
        open_list: List[CBSNode] = [root]
        focal_list: List[CBSNode] = []
        best_cost = root.cost
        iterations = 0
        
        while (open_list or focal_list) and iterations < self.max_iterations:
            iterations += 1
            
            elapsed = time.perf_counter() - start_time
            if elapsed > self.time_limit:
                best = min((open_list + focal_list), key=lambda n: n.cost, default=root)
                return CBSSolution(success=False, partial=True, error="Timeout",
                    best_node=best, stats=self._get_stats(start_time), paths=best.paths,
                    conflicts=self.detector.find_all_conflicts(best.paths))
            
            # 维护FOCAL边界: 所有 cost ≤ w * best_cost 的节点
            while open_list and open_list[0].cost <= self.w * best_cost:
                node = heapq.heappop(open_list)
                focal_list.append(node)
            
            if not focal_list:
                if not open_list:
                    break
                # OPEN中最低cost节点超出focal边界, 更新best_cost
                best_cost = open_list[0].cost
                continue
            
            # 从FOCAL中选择节点 (使用SOC成本最低)
            best_focal = min(focal_list, key=lambda n: n.cost if hasattr(n, 'cost') else float('inf'))
            focal_list.remove(best_focal)
            
            # 检测冲突
            conflict = self.detector.find_first_conflict(best_focal.paths)
            
            if conflict is None:
                elapsed = (time.perf_counter() - start_time) * 1000
                self._stats["total_time_ms"] += elapsed
                self._stats["successful_runs"] += 1
                logger.info(f"[MAPF-ECBS] Solved! agents={len(tasks)}, iters={iterations}, "
                           f"soc={best_focal.sum_of_costs:.1f}, time={elapsed:.1f}ms, w={self.w}")
                return CBSSolution(success=True, solution_node=best_focal,
                    stats=self._get_stats(start_time), paths=best_focal.paths, conflicts=[], tree_size=iterations)
            
            # 分支 (同CBS)
            new_nodes = await self._branch_on_conflict(best_focal, conflict)
            for node in new_nodes:
                if node is not None:
                    heapq.heappush(open_list, node)
                    if node.cost <= self.w * best_cost:
                        focal_list.append(node)
        
        # 未找到解
        all_nodes = open_list + focal_list
        if all_nodes:
            best = min(all_nodes, key=lambda n: n.cost)
            return CBSSolution(success=False, partial=True, error="Exhausted",
                best_node=best, stats=self._get_stats(start_time), paths=best.paths,
                conflicts=self.detector.find_all_conflicts(best.paths))
        
        return CBSSolution(success=False, error="No solution found",
            stats=self._get_stats(start_time), paths={}, conflicts=[])


# ═══════════════════════════════════════════════════════════════
# 便捷函数与工厂方法
# ═══════════════════════════════════════════════════════════════

async def solve_mapf(
    graph: 'PathGraph',
    tasks: Dict[str, Tuple[int, int]],
    mode: str = 'ECBS',  # 'CBS' (最优) or 'ECBS' (快速子最优)
    suboptimality: float = 1.2,
    time_limit: float = 10.0,
    **kwargs
) -> CBSSolution:
    """
    便捷函数: 一行调用MAPF求解
    
    Args:
        graph: PathGraph实例 (v2/path_planning/graph_builder.py)
        tasks: {agv_id: (start_node, goal_node)}
        mode: 'CBS' 最优 或 'ECBS' 快速 (推荐生产环境用ECBS)
        suboptimality: ECBS子最优因子 (越小越优但越慢)
        time_limit: 超时限制(秒)
        
    Returns:
        CBSSolution 结果对象
        
    Example:
        >>> result = await solve_mapf(
        ...     graph=my_map,
        ...     tasks={
        ...         "AGV-001": (5, 20),
        ...         "AGV-002": (12, 8),
        ...         "AGV-003": (3, 15),
        ...     },
        ...     mode='ECBS',
        ...     suboptimality=1.2
        ... )
        >>> if result.success:
        ...     for agv_id, path in result.paths.items():
        ...         waypoints = [p.node_id for p in path.path]
        ...         print(f"{agv_id}: {waypoints}")
    """
    if mode.upper() == 'CBS':
        solver = CBSSolver(graph=graph, time_limit_seconds=time_limit, **kwargs)
    else:
        solver = ECBSSolver(graph=graph, suboptimality_factor=suboptimality, 
                            time_limit_seconds=time_limit, **kwargs)
    
    return await solver.solve(tasks)


# ═══════════════════════════════════════════════════════════════
# 测试入口 (开发调试用)
# ═══════════════════════════════════════════════════════════════

async def demo_cbs():
    """CBS算法演示 (需配合实际图数据运行)"""
    # 此处仅作为接口示例,实际使用需要传入真实PathGraph
    logger.info("[MAPF-Demo] CBS Solver initialized")
    logger.info("[MAPF-Demo] Call solve_mapf() with your PathGraph and tasks")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(demo_cbs())
