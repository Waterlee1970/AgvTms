# Phase 1 第一周启动任务清单

> 路径规划引擎重构 — 将 ACO 基础版升级为 A*+时间窗 工业级引擎

## Day 1-2: 双向A*路径搜索

### 任务

- [ ] 实现 `TimeWindowAStar` 类
  - [ ] 状态空间 = `(node_id, time_step)`, 用 `heapq` 优先队列
  - [ ] g值 = 累计行程时间 + 等待时间; h值 = 欧几里得距离/最大速度
  - [ ] 在 500 节点随机图上验证: <10ms/路径
- [ ] 编写 `test_astar.py` 单元测试

### 关键代码骨架

```python
# backend/app/algorithms/v2/path_planning/astar.py
import heapq
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

@dataclass
class PathState:
    node_id: str
    time: float
    g_cost: float
    f_cost: float
    parent: Optional["PathState"] = None
    
    def __lt__(self, other): return self.f_cost < other.f_cost

class BidirectionalAStar:
    def __init__(self, graph: Dict[str, List[Tuple[str, float]]]):
        self.graph = graph
        self.node_positions = {}  # {node_id: (x, y)}
    
    def find_path(self, start: str, goal: str) -> List[str]:
        """双向A*: 同时从起点和终点搜索，在中间相遇"""
        pass
    
    def heuristic(self, a: str, b: str) -> float:
        """欧几里得启发式"""
        ax, ay = self.node_positions[a]
        bx, by = self.node_positions[b]
        return ((ax - bx)**2 + (ay - by)**2) ** 0.5

class TimeWindowAStar(BidirectionalAStar):
    def __init__(self, graph, node_positions, time_windows: Dict[str, List[Tuple[float, float]]]):
        super().__init__(graph, node_positions)
        self.time_windows = time_windows  # node -> [(start, end), ...]
    
    def find_path_with_time(self, start: str, goal: str, start_time: float) -> List[Tuple[str, float, float]]:
        """
        返回: [(node_id, arrive_time, depart_time), ...]
        约束: 在时间窗允许区间内进入/离开每个节点
        """
        pass
    
    def _is_node_free(self, node: str, arrive: float, duration: float) -> bool:
        """检查节点在 [arrive, arrive+duration] 区间内是否空闲"""
        pass
```

---

## Day 3-4: 时间窗路径规划 + SIPP

### 任务

- [ ] 实现时间窗占用表 `TimeWindowTable`
  - [ ] 节点维: `Dict[NodeId, List[(start, end, agv_id)]]`
  - [ ] 边维度: `Dict[(from,to), List[(start, end, agv_id)]]`
- [ ] 实现 `SIPPPlanner` (安全区间路径规划)
  - [ ] 不存储连续时间步，存储安全区间
  - [ ] 大幅降低状态空间
- [ ] 编写 20 车同时运行无冲突测试场景

### 时间窗冲突检测逻辑

```
对AGV_k规划时:
  for each node in candidate_path:
    查询 node 的时间窗占用表
    找到第一个满足以下条件的区间 [t_arrive, t_depart]:
      - 不与任何已占用的 [occ_start, occ_end] 重叠
      - t_depart - t_arrive ≥ min_stay_time
      - t_arrive 满足 AGV 速度约束
    如果找不到 → 在该节点等待/绕行
```

---

## Day 5: 动态重规划引擎

### 任务

- [ ] 实现 `DynamicReplanner`
  - [ ] 触发条件: 路径阻塞、新障碍物、优先级插队
  - [ ] 局部重规划: 仅重规划受影响区间
  - [ ] <50ms 重规划延迟
- [ ] 实现增量 A* (D* Lite 简化版)

---

## Day 6-7: 集成 + 性能测试

### 任务

- [ ] 将新路径规划引擎接入 `hybrid.py` 调度协调器
- [ ] 编写 benchmark 脚本
  - [ ] 小场景: 10AGV, 100节点, 30任务
  - [ ] 中场景: 50AGV, 500节点, 200任务
  - [ ] 记录路径规划耗时、冲突次数、平均等待时间
- [ ] 与现有 ACO 路径规划对比
- [ ] 文档: `docs/path_planning_v2.md`

### Benchmark 指标

| 指标 | 当前ACO | 目标A*+TW | 
|------|---------|-----------|
| 单路径规划 | ~5ms | <1ms |
| 20车规划 | ~200ms | <50ms |
| 碰撞率 | 未检测 | 0% |
| 重规划延迟 | N/A | <50ms |
