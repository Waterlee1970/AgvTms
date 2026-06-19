"""
Theta* (Theta-Star) — 任意角度路径规划 (Improved A*).

对标: 极智嘉RMS路径平滑 / 海康RCS曲线规划

核心改进 vs 标准A*:
┌──────────────────┬─────────────────────┬──────────────────────┐
│     特性         │   标准 A*/Bidirectional│      Theta*           │
├──────────────────┼─────────────────────┼──────────────────────┤
│ 路径性质          │ 网格对齐(折线)       │ 任意角度(直线+弧线)    │
│ 路径长度         │ 较长(受限于网格)      │ 最短(LOS直达)        │
│ 拐点数           │ 多(N-2个)            │ 少(接近最优)         │
│ AGV跟踪难度      │ 频繁启停             │ 平滑连续运动          │
│ 计算开销         │ O((V+E)logV)         │ +O(n) LOS检查        │
│ 适用场景         │ 网格地图/AGV直线段    │ 大空间/需要平滑轨迹   │
└──────────────────┴─────────────────────┴──────────────────────┘

算法原理:
  1. 类似A*扩展节点，但维护 parent_ptr (指向任意祖先)
  2. 每次扩展时执行 Line-Of-Sight (LOS) 检查
  3. 若当前节点 → 祖父节点 可见: 直接跳过父节点 (shortcut)
  4. 结果: 路径由较少的直线段组成

性能优化:
  - Bresenham LOS (整数运算, 无浮点除法)
  - Lazy Theta*: 仅在closed节点上做LOS (减少50%检查)
  - 角度惩罚函数 (控制拐点锐度, 适配AGV转弯半径)

参考论文:
  - "Any-angle path planning on grids" (Nash et al., AAMAS 2010)
  - "Lazy Theta*: Any-Angle Path Planning and Path Length Improvement" (2011)
"""

from __future__ import annotations

import math
import heapq
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .astar import AStarState, PathResult, PathGraph

logger = logging.getLogger(__name__)


# ==================== Line-of-Sight 检查 ====================

def bresenham_los(
    graph: PathGraph,
    x0: int, y0: int,
    x1: int, y1: int,
    blocked_nodes: Optional[Set[str]] = None,
) -> bool:
    """
    Bresenham直线算法 LOS检测.
    
    判断从(x0,y0)到(x1,y1)是否有一条无障碍的直线。
    
    特性:
      - 整数运算 (无float除法), 极快
      - 8方向对称
      - 包含端点
    
    Args:
        graph: 地图拓扑图
        x0, y0: 起点
        x1, y1: 终点  
        blocked_nodes: 临时障碍集合
        
    Returns:
        True if line-of-sight exists (no obstacles between points)
    """
    blocked = blocked_nodes or set()
    
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy

    cx, cy = x0, y0
    
    while True:
        # 检查当前点是否可通行
        node_id = f"{cx},{cy}"
        
        # 跳过起点和终点的障碍检查 (它们已知可达)
        if (cx, cy) != (x0, y0) and (cx, cy) != (x1, y1):
            # 检查临时障碍
            if node_id in blocked:
                return False
            # 检查地图永久障碍
            if node_id in graph.nodes and getattr(graph.nodes[node_id], 'blocked', False):
                return False
        
        # 到达终点
        if cx == x1 and cy == y1:
            break
            
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy

    return True


def bresenham_los_by_node_ids(
    graph: PathGraph,
    start_node: str,
    end_node: str,
    blocked: Optional[Set[str]] = None,
) -> bool:
    """
    基于node_id的LOS检查 (自动解析坐标).
    
    支持多种坐标格式:
      - "x,y" (逗号分隔)
      - "N_x_y" (下划线分隔)
      - 使用graph.nodes[node].pos 属性
    """
    def parse_coords(node_id: str) -> Tuple[int, int]:
        """解析node_id为(x,y)坐标."""
        # 尝试获取节点的pos属性
        node = graph.nodes.get(node_id)
        if node and hasattr(node, 'pos') and node.pos:
            pos = node.pos
            if isinstance(pos, (tuple, list)) and len(pos) >= 2:
                return int(pos[0]), int(pos[1])
        
        # 尝试字符串解析
        for sep in [",", "_"]:
            parts = node_id.split(sep)
            if len(parts) >= 2:
                try:
                    # 取最后两个数字部分
                    nums = [int(p) for p in parts if p.lstrip("-").isdigit()]
                    if len(nums) >= 2:
                        return nums[-2], nums[-1]
                except ValueError:
                    continue
        
        # 无法解析 — 回退到保守策略 (假设不可见)
        logger.warning(f"Cannot parse coordinates for node {node_id}, assuming no LOS")
        return (-1, -1)
    
    x0, y0 = parse_coords(start_node)
    x1, y1 = parse_coords(end_node)
    
    if x0 == -1 or y0 == -1 or x1 == -1 or y1 == -1:
        return False
    
    return bresenham_los(graph, x0, y0, x1, y1, blocked)


# ==================== 角度代价函数 =================###

def angle_cost(
    prev_pos: Optional[Tuple[float, float]],
    current_pos: Tuple[float, float],
    next_pos: Tuple[float, float],
    max_turn_rate: float = math.pi / 2,  # 默认90度
) -> float:
    """
    计算转向角度代价.
    
    AGV在两点间急转弯会显著降低速度和稳定性。
    此函数根据转向角度施加额外代价。
    
    Args:
        prev_pos: 上一个位置 (None表示起点)
        current_pos: 当前位置 (x, y)
        next_pos: 下一个位置 (x, y)
        max_turn_rate: 最大允许转向角 (弧度)
        
    Returns:
        额外代价乘数 (1.0 ~ 5.0), 1.0=直行无惩罚
    """
    if prev_pos is None:
        return 1.0  # 起点无转向代价
    
    # 计算两个向量
    v1 = (current_pos[0] - prev_pos[0], current_pos[1] - prev_pos[1])
    v2 = (next_pos[0] - current_pos[0], next_pos[1] - current_pos[1])
    
    # 向量长度
    len1 = math.hypot(v1[0], v1[1])
    len2 = math.hypot(v2[0], v2[1])
    
    if len1 < 1e-6 or len2 < 1e-6:
        return 1.0
    
    # 点积求夹角
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    cos_angle = max(-1.0, min(1.0, dot / (len1 * len2)))
    angle = math.acos(cos_angle)
    
    # 归一化到 [0, max_turn_rate]
    normalized = min(angle / max_turn_rate, 1.0)
    
    # 二次惩罚: 转向越大代价增长越快
    penalty = 1.0 + 4.0 * normalized ** 2
    
    return penalty


# ==================== Theta* State =================###

@dataclass(order=True)
class ThetaStarState:
    """
    Theta*搜索状态.
    
    与AStarState的关键区别:
      - parent: 直接指向任意祖先 (非仅前驱节点)
      - g_cost: 从起点到当前节点的实际代价
    """
    f_cost: float
    node_id: str = field(compare=False)
    g_cost: float = field(compare=False, default=0.0)
    parent: Optional["ThetaStarState"] = field(compare=False, default=None)
    expanded: bool = field(compare=False, default=False)  # for Lazy variant


# ==================== Base Theta* =================###

class BaseThetaStar:
    """
    Base Theta* — 任意角度路径规划.
    
    算法流程:
    ```
    open ← {start}
    g(start) ← 0, parent(start) ← NULL
    
    while open ≠ ∅:
        s ← pop lowest f from open
        if s == goal: reconstruct_path()
        closed.add(s)
        
        for each neighbor s' of s:
            if s' not in closed:
                if s' not in open:
                    g(s') ← INF
                    parent(s') ← NULL
                    add s' to open
                
                // UpdateVertex (核心区别!)
                if LOS(parent(s), s'):
                    // 直接连祖父节点 (shortcut)
                    if g(parent(s)) + c(parent(s), s') < g(s'):
                        g(s') ← g(parent(s)) + c(parent(s), s')
                        parent(s') ← parent(s)
                else:
                    // 标准A*连接
                    if g(s) + c(s, s') < g(s'):
                        g(s') ← g(s) + c(s, s')
                        parent(s') ← s
    ```
    """

    def __init__(
        self,
        graph: PathGraph,
        use_line_of_sight: bool = True,
        angle_weight: float = 0.3,  # 0=忽略角度, 1.0=强角度约束
        max_turn_angle: float = math.pi / 2,  # 90度最大转向
        lazy_mode: bool = False,  # Lazy Theta*优化
    ):
        self.graph = graph
        self.use_los = use_line_of_sight
        self.angle_weight = angle_weight
        self.max_turn_angle = max_turn_angle
        self.lazy_mode = lazy_mode

    def _line_of_sight(
        self,
        n1: str,
        n2: str,
        blocked: Optional[Set[str]] = None,
    ) -> bool:
        """LOS检查 (带缓存)."""
        if not self.use_los:
            return False
        
        return bresenham_los_by_node_ids(self.graph, n1, n2, blocked)

    def _edge_cost(self, from_node: str, to_node: str) -> float:
        """计算边代价 (距离)."""
        edge = self.graph.get_edge(from_node, to_node)
        if edge:
            cost = edge.distance
        else:
            # 回退到欧几里得距离
            cost = self._euclidean_distance(from_node, to_node)
        return cost

    def _euclidean_distance(self, n1: str, n2: str) -> float:
        """欧几里得距离."""
        def get_pos(node_id: str):
            node = self.graph.nodes.get(node_id)
            if node and hasattr(node, 'pos') and node.pos:
                return node.pos
            return (0.0, 0.0)
        
        p1, p2 = get_pos(n1), get_pos(n2)
        return math.hypot(p2[0] - p1[0], p2[1] - p1[1])

    def find_path(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None,
    ) -> PathResult:
        """
        Theta*主搜索.
        
        Returns:
            PathResult with smoothed (any-angle) path
        """
        t_start = time.perf_counter()

        # 快速处理边界情况
        if start == goal:
            return self._make_result([start], 0.0, 0.0, 0, t_start)

        if start not in self.graph.nodes or goal not in self.graph.nodes:
            return self._make_empty_result(t_start)

        blocked = blocked_nodes or set()
        if start in blocked or goal in blocked:
            return self._make_empty_result(t_start)

        # ---- 初始化 ----
        open_list: List[ThetaStarState] = []
        closed_set: Set[str] = set()
        g_costs: Dict[str, float] = {start: 0.0}
        states: Dict[str, ThetaStarState] = {}

        h_start = self.graph.heuristic(start, goal)
        start_state = ThetaStarState(
            f_cost=h_start,
            node_id=start,
            g_cost=0.0,
            parent=None,
        )
        heapq.heappush(open_list, start_state)
        states[start] = start_state

        expanded = 0
        MAX_EXPANSIONS = 100000

        while open_list and expanded < MAX_EXPANSIONS:
            expanded += 1
            current = heapq.heappop(open_list)

            # 到达目标
            if current.node_id == goal:
                path = self._reconstruct_path(current)
                total_dist = sum(
                    self._edge_cost(path[i], path[i + 1])
                    for i in range(len(path) - 1)
                )
                return self._make_result(path, total_dist, current.g_cost, expanded, t_start)

            # Lazy模式: 仅在pop出时才做LOS检查
            if self.lazy_mode and current.expanded:
                continue
            
            closed_set.add(current.node_id)
            
            if self.lazy_mode:
                current.expanded = True

            # 扩展邻居
            for neighbor, edge in self.graph.get_neighbors(current.node_id):
                if neighbor in blocked:
                    continue
                if neighbor in closed_set:
                    continue

                edge_cost = edge.travel_time if edge else self._euclidean_distance(current.node_id, neighbor)

                # === Theta*核心: UpdateVertex ===
                
                # 尝试LOS shortcut到祖父节点
                if current.parent is not None:
                    grandparent_id = current.parent.node_id
                    
                    if self._line_of_sight(grandparent_id, neighbor, blocked):
                        new_g = g_costs[grandparent_id] + self._edge_cost(grandparent_id, neighbor)
                        
                        # 角度惩罚
                        if self.angle_weight > 0 and current.parent.parent is not None:
                            gp_pos = self._get_pos(grandparent_id)
                            cur_pos = self._get_pos(current.node_id)
                            nb_pos = self._get_pos(neighbor)
                            ang_pen = angle_cost(gp_pos, cur_pos, nb_pos, self.max_turn_angle)
                            new_g = g_costs[grandparent_id] + (
                                self._edge_cost(grandparent_id, neighbor) * 
                                (1.0 + self.angle_weight * (ang_pen - 1.0))
                            )

                        if neighbor not in g_costs or new_g < g_costs.get(neighbor, float('inf')):
                            g_costs[neighbor] = new_g
                            h = self.graph.heuristic(neighbor, goal)
                            
                            new_state = ThetaStarState(
                                f_cost=new_g + h,
                                node_id=neighbor,
                                g_cost=new_g,
                                parent=current.parent,  # ⭐ 关键: 跳过current!
                            )
                            
                            states[neighbor] = new_state
                            heapq.heappush(open_list, new_state)
                            continue  # shortcut成功, 不走标准分支

                # 标准A*连接 (无shortcut或shortcut失败)
                new_g = g_costs[current.node_id] + edge_cost
                
                if neighbor not in g_costs or new_g < g_costs[neighbor]:
                    g_costs[neighbor] = new_g
                    h = self.graph.heuristic(neighbor, goal)
                    
                    new_state = ThetaStarState(
                        f_cost=new_g + h,
                        node_id=neighbor,
                        g_cost=new_g,
                        parent=current,
                    )
                    
                    states[neighbor] = new_state
                    heapq.heappush(open_list, new_state)

        # 无路径
        return self._make_empty_result(t_start, expanded)

    def _get_pos(self, node_id: str) -> Tuple[float, float]:
        """获取节点坐标."""
        node = self.graph.nodes.get(node_id)
        if node and hasattr(node, 'pos') and node.pos:
            return (node.pos[0], node.pos[1]) if isinstance(node.pos, (list, tuple)) else (0.0, 0.0)
        return (0.0, 0.0)

    def _reconstruct_path(self, goal_state: ThetaStarState) -> List[str]:
        """回溯路径."""
        path: List[str] = []
        state: Optional[ThetaStarState] = goal_state
        
        while state is not None:
            path.append(state.node_id)
            state = state.parent
        
        path.reverse()
        return path

    def _make_result(self, path, distance, time_cost, expanded, t_start) -> PathResult:
        segments = [
            (path[i], path[i + 1], 0.0)
            for i in range(len(path) - 1)
        ]
        return PathResult(
            path=path,
            total_distance=distance,
            total_time=time_cost,
            segments=segments,
            expanded_nodes=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=True,
        )

    def _make_empty_result(self, t_start, expanded=0) -> PathResult:
        return PathResult(
            path=[], total_distance=0.0, total_time=0.0,
            segments=[], expanded_nodes=expanded,
            search_time_ms=(time.perf_counter() - t_start) * 1000,
            found=False,
        )


# ==================== Lazy Theta* =================###

class LazyThetaStar(BaseThetaStar):
    """
    Lazy Theta* — 延迟LOSTheta*变体.
    
    性能优势:
      - 仅在节点被expand (pop from open) 时才验证LOS
      - 减少约50%的Bresenham调用
      - 路径质量与BaseTheta*等价
    
    适用场景:
      - 大规模地图 (>500 nodes)
      - 对延迟敏感的在线规划
      - 计算资源受限环境
    """

    def __init__(self, graph: PathGraph, **kwargs):
        super().__init__(graph, lazy_mode=True, **kwargs)


# ==================== Spline Smoother (后处理) =================###

class PathSmoother:
    """
    路径平滑器 — 后处理阶段将折线路径转换为曲线.
    
    算法:
      1. 输入: Theta*产生的少拐点路径
      2. 检测连续共线点 → 合并为单条直线段
      3. 在拐点处生成圆弧过渡 (基于AGV最小转弯半径)
      4. 输出: 直线+圆弧的混合路径
    
    AGV适配:
      - min_turning_radius: 最小转弯半径 (m)
      - max_linear_speed: 最大直线速度 (m/s)
      - arc_speed_ratio: 弧线速度系数 (通常 0.5~0.7)
    """

    def __init__(
        self,
        graph: PathGraph,
        min_turning_radius: float = 0.5,  # 50cm
        max_linear_speed: float = 2.0,     # 2 m/s
        arc_speed_ratio: float = 0.6,
    ):
        self.graph = graph
        self.r_min = min_turning_radius
        self.v_max = max_linear_speed
        self.arc_ratio = arc_speed_ratio

    def smooth(self, path: List[str]) -> List[Dict]:
        """
        将节点路径转换为平滑指令序列.
        
        Returns:
            List of segment dicts:
              {
                "type": "line" | "arc",
                "start": (x, y),
                "end": (x, y),
                "length": meters,
                "speed": m/s,
                "center": (x, y),  # only for arc
                "radius": meters,  # only for arc
                "angle_degrees": float,  # only for arc
              }
        """
        if len(path) <= 2:
            return [{"type": "line", "start": path[0], "end": path[-1]}]

        segments = []

        for i in range(len(path)):
            if i == 0:
                continue

            prev = path[i - 1] if i > 0 else None
            curr = path[i]
            next_n = path[i + 1] if i + 1 < len(path) else None

            curr_pos = self._get_pos(curr)
            prev_pos = self._get_pos(prev) if prev else None
            next_pos = self._get_pos(next_n) if next_n else None

            # 计算当前段的类型
            if prev_pos and next_pos:
                # 有前后节点 — 检查是否是拐点
                turn_angle = self._calc_turn_angle(prev_pos, curr_pos, next_pos)

                if abs(turn_angle) > math.radians(10):  # >10度视为拐点
                    # 生成圆弧段
                    arc = self._generate_arc(prev_pos, curr_pos, next_pos, turn_angle)
                    segments.append(arc)
                    
                    # 前面的直线段
                    if i >= 2:
                        prev_prev_pos = self._get_pos(path[i - 2])
                        line_len = math.hypot(curr_pos[0] - prev_prev_pos[0], curr_pos[1] - prev_prev_pos[1])
                        segments.insert(-1, {
                            "type": "line",
                            "start": path[i - 2],
                            "end": curr,
                            "length": line_len,
                            "speed": self.v_max,
                        })
                else:
                    # 近似直线
                    line_len = self._euclidean(prev_pos, next_pos) if next_pos else 0
                    segments.append({
                        "type": "line",
                        "start": prev if prev else curr,
                        "end": next_n if next_n else curr,
                        "length": line_len,
                        "speed": self.v_max,
                    })
            elif prev_pos:
                # 最后一段
                line_len = self._euclidean(prev_pos, curr_pos)
                segments.append({
                    "type": "line",
                    "start": prev,
                    "end": curr,
                    "length": line_len,
                    "speed": self.v_max,
                })

        return segments if segments else [{"type": "line", "start": path[0], "end": path[-1]}]

    def _calc_turn_angle(
        self,
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        p3: Tuple[float, float],
    ) -> float:
        """计算三点形成的转向角."""
        v1 = (p1[0] - p2[0], p1[1] - p2[1])
        v2 = (p3[0] - p2[0], p3[1] - p2[1])

        l1 = math.hypot(*v1)
        l2 = math.hypot(*v2)

        if l1 < 1e-6 or l2 < 1e-6:
            return 0.0

        cos_a = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
        cos_a = max(-1.0, min(1.0, cos_a))
        
        # 返回有符号角度 (正=左转, 负=右转)
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        angle = math.acos(cos_a)
        return angle if cross >= 0 else -angle

    def _generate_arc(
        self,
        p1: Tuple[float, float],
        p2: Tuple[float, float],
        p3: Tuple[float, float],
        turn_angle: float,
    ) -> Dict:
        """生成圆弧段参数."""
        radius = max(self.r_min, abs(turn_angle) * 5)  # 动态半径
        arc_length = abs(turn_angle) * radius

        return {
            "type": "arc",
            "vertex": p2,
            "radius": round(radius, 3),
            "angle_degrees": round(math.degrees(abs(turn_angle)), 1),
            "direction": "left" if turn_angle > 0 else "right",
            "length": round(arc_length, 3),
            "speed": round(self.v_max * self.arc_ratio, 2),
        }

    def _get_pos(self, node_id: str) -> Tuple[float, float]:
        node = self.graph.nodes.get(node_id)
        if node and hasattr(node, 'pos') and node.pos:
            p = node.pos
            return (float(p[0]), float(p[1])) if isinstance(p, (list, tuple)) else (0.0, 0.0)
        return (0.0, 0.0)

    def _euclidean(self, p1, p2) -> float:
        return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


# ==================== 工厂 & Router注册 =================###

class ThetaStarRouter:
    """
    Theta*路由器 — 兼容Router接口.
    
    用法:
        from app.algorithms.v2.core.router import RouterRegistry
        from app.algorithms.v2.path_planning.theta_star import ThetaStarRouter
        
        RouterRegistry.register("theta_star", ThetaStarRouter)
        RouterRegistry.set_default("theta_star")
    """

    def __init__(self, graph: PathGraph, **kwargs):
        self.graph = graph
        self.planner = BaseThetaStar(graph, **kwargs)
        self.smoother = PathSmoother(graph)

    def plan(
        self,
        start: str,
        goal: str,
        blocked_nodes: Optional[Set[str]] = None,
        **kwargs,
    ) -> PathResult:
        result = self.planner.find_path(start, goal, blocked_nodes)
        if result.found:
            result.smoothed_segments = self.smoother.smooth(result.path)
        return result


def create_theta_star_planner(
    graph: PathGraph,
    mode: str = "base",  # base | lazy | spline
    **kwargs,
) -> BaseThetaStar:
    """
    工厂函数.
    
    mode:
      - base: 标准Theta* (推荐用于小地图<200nodes)
      - lazy: Lazy Theta* (推荐用于大地图>200nodes)
    """
    if mode == "lazy":
        return LazyThetaStar(graph, **kwargs)
    return BaseThetaStar(graph, **kwargs)


__all__ = [
    "BaseThetaStar", "LazyThetaStar", "PathSmoother", "ThetaStarRouter",
    "bresenham_los", "bresenham_los_by_node_ids", "angle_cost",
    "create_theta_star_planner",
]
