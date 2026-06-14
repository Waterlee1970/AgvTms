"""
匈牙利算法任务分配器 — O(n³) 最优二分匹配

适用于中小规模 (N < 200) 的 AGV-任务分配场景，
作为 MIP 求解器的轻量级替代方案，无需外部依赖。

时间复杂度: O(n³), 空间复杂度: O(n²)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class HungarianResult:
    """匈牙利算法分配结果"""
    assignments: Dict[str, str]       # task_id → agv_id
    total_cost: float
    compute_time_ms: float
    status: str = "OPTIMAL"
    unmatched_tasks: List[str] = None
    unmatched_agvs: List[str] = None

    def __post_init__(self):
        if self.unmatched_tasks is None:
            self.unmatched_tasks = []
        if self.unmatched_agvs is None:
            self.unmatched_agvs = []


class HungarianAssigner:
    """
    匈牙利算法 (Kuhn-Munkres) 用于最优二分匹配。

    用法:
        assigner = HungarianAssigner()
        result = assigner.assign(cost_matrix)
        # cost_matrix[i][j] = 任务 i 分配给 AGV j 的代价
    """

    def __init__(self, maximize: bool = False):
        """
        Args:
            maximize: True=求最大匹配(如效率/收益), False=求最小匹配(如距离/时间)
        """
        self.maximize = maximize

    def assign(
        self,
        cost_matrix: List[List[float]],
        task_ids: Optional[List[str]] = None,
        agv_ids: Optional[List[str]] = None,
    ) -> HungarianResult:
        """
        执行匈牙利算法最优分配。

        Args:
            cost_matrix: N×M 代价矩阵 (N个任务, M个AGV)
                         若 N != M, 自动补零扩展为方阵
            task_ids: 任务 ID 列表 (长度=N)
            agv_ids:   AGV ID 列表 (长度=M)

        Returns:
            HungarianResult 包含 assignments, total_cost 等
        """
        t0 = time.perf_counter()

        if not cost_matrix or not cost_matrix[0]:
            return HungarianResult(
                assignments={}, total_cost=0.0,
                compute_time_ms=(time.perf_counter()-t0)*1000,
            )

        n_tasks = len(cost_matrix)
        n_agvs = len(cost_matrix[0])

        # 生成默认 ID
        if task_ids is None:
            task_ids = [f"task_{i}" for i in range(n_tasks)]
        if agv_ids is None:
            agv_ids = [f"agv_{j}" for j in range(n_agvs)]

        # 确保非负
        matrix = [
            [max(0.0, val) for val in row]
            for row in cost_matrix
        ]

        # 如果是最大化问题，转换为最小化 (用大数减去每个值)
        if self.maximize:
            max_val = max(max(row) for row in matrix) + 1
            matrix = [[max_val - v for v in row] for row in matrix]

        # 方阵化: 补零使 N == M
        size = max(n_tasks, n_agvs)
        square = [[0.0] * size for _ in range(size)]
        for i in range(n_tasks):
            for j in range(n_agvs):
                square[i][j] = matrix[i][j]

        # 运行核心匈牙利算法
        row_ind, col_ind = self._hungarian(square)

        # 解析结果
        assignments: Dict[str, str] = {}
        unmatched_tasks: List[str] = []
        unmatched_agvs: List[str] = list(agv_ids)
        total_cost = 0.0

        assigned_agv_set = set()
        for i, j in zip(row_ind, col_ind):
            if i < n_tasks and j < n_agvs:
                tid = task_ids[i]
                aid = agv_ids[j]
                # 仅当实际有代价时才分配（避免虚拟的零成本匹配）
                if cost_matrix[i][j] > 0 or (i < n_tasks and j < n_agvs):
                    assignments[tid] = aid
                    total_cost += cost_matrix[i][j]
                    assigned_agv_set.add(aid)
                    if aid in unmatched_agvs:
                        unmatched_agvs.remove(aid)

        # 未匹配的任务
        for tid in task_ids:
            if tid not in assignments:
                unmatched_tasks.append(tid)

        return HungarianResult(
            assignments=assignments,
            total_cost=total_cost,
            compute_time_ms=(time.perf_counter() - t0) * 1000,
            status="OPTIMAL",
            unmatched_tasks=unmatched_tasks,
            unmatched_agvs=unmatched_agvs,
        )

    @staticmethod
    def _hungarian(cost: List[List[float]]) -> Tuple[List[int], List[int]]:
        """
        核心匈牙利算法实现。

        基于 Kuhn-Munkres 算法，O(n³) 复杂度。
        参考: https://en.wikipedia.org/wiki/Hungarian_algorithm

        Args:
            cost: N×N 非负代价方阵

        Returns:
            (row_indices, col_indices) 最优匹配
        """
        n = len(cost)
        if n == 0:
            return [], []

        # 初始化
        u = [0.0] * (n + 1)  # 行势能
        v = [0.0] * (n + 1)  # 列势能
        p = [0] * (n + 1)     # p[j] = 匹配到列 j 的行
        way = [0] * (n + 1)   # 路径记录

        for i in range(1, n + 1):
            p[0] = i
            j0 = 0
            minv = [float('inf')] * (n + 1)
            used = [False] * (n + 1)

            while True:
                used[j0] = True
                i0 = p[j0]
                delta = float('inf')
                j1 = -1

                for j in range(1, n + 1):
                    if not used[j]:
                        cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                        if cur < minv[j]:
                            minv[j] = cur
                            way[j] = j0
                        if minv[j] < delta:
                            delta = minv[j]
                            j1 = j

                for j in range(n + 1):
                    if used[j]:
                        u[p[j]] += delta
                        v[j] -= delta
                    else:
                        minv[j] -= delta

                j0 = j1
                if p[j0] == 0:
                    break

            # 增广路径回溯
            while j0 != 0:
                j1 = way[j0]
                p[j0] = p[j1]
                j0 = j1

        # 提取结果: p[j] = 分配给列 j 的行号
        row_indices = [0] * n
        col_indices = [0] * n
        for j in range(1, n + 1):
            if p[j] != 0:
                row_indices[p[j] - 1] = j - 1
                col_indices[j - 1] = p[j] - 1

        return row_indices, col_indices


def create_distance_cost_matrix(
    tasks: List[Dict[str, Any]],
    agvs: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
) -> Tuple[List[List[float]], List[str], List[str]]:
    """
    从场景数据构建距离代价矩阵。

    Args:
        tasks: 任务列表, 每个 dict 含 pickup_node/dropoff_node
        agvs: AGV 列表, 每个 dict 含 current_node/current_node_id/pos
        nodes: 节点列表, 每个 dict 含 id/x/y

    Returns:
        (cost_matrix, task_ids, agv_ids)
    """
    pos_map = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in nodes}

    task_ids = []
    agv_ids = []
    cost_matrix = []

    for t in tasks:
        pickup = t.get("pickup_node", t.get("pickup_node_id", t.get("pickup", "")))
        if pickup and pickup in pos_map:
            task_ids.append(t.get("id", t.get("task_id", "")))
            row = []
            for a in agvs:
                apos = a.get("current_node", a.get("current_node_id", a.get("pos", "")))
                if apos in pos_map and pickup in pos_map:
                    px, py = pos_map[pickup]
                    ax, ay = pos_map[apos]
                    dist = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
                    row.append(dist)
                else:
                    row.append(float('inf'))
            cost_matrix.append(row)
            agv_ids = [a.get("id", a.get("agv_id", "")) for a in agvs]

    return cost_matrix, task_ids, agv_ids
