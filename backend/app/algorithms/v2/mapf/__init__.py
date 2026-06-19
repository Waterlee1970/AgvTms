"""
MAPF (Multi-Agent Path Finding) 模块
多智能体路径规划算法集合

模块组成:
├── cbs_solver.py      - Conflict-Based Search (CBS/ECBS) 核心算法
├── traffic_manager.py - 交通管制与死锁预防系统
└── __init__.py         - 模块导出

与现有系统集成:
- 下层规划器复用 v2/astar.py TimeWindowAStar / SIPP
- 与 v2/core/router.py Router 接口兼容
- 输出供 v2/hybrid_orchestrator/orchestrator.py HybridOrchestratorV2 使用

性能基准 (参考值):
| 算法    | 10 agents | 20 agents | 30 agents | 50 agents |
|---------|---------- |-----------|-----------|----------|
| CBS     | ~50ms     | ~500ms    | ~5s       | ~60s     |
| ECBS1.2 | ~20ms     | ~150ms    | ~800ms    | ~8s      |

Author: Architecture Team
Version: 2.0.0
"""

from .cbs_solver import (
    # 核心数据类型
    ConflictType,
    Position,
    Constraint,
    Conflict,
    PathResult,
    CBSNode,
    CBSSolution,
    
    # 冲突检测
    ConflictDetector,
    
    # 下层规划器
    LowLevelPlanner,
    SpaceTimeAStar,
    
    # 求解器
    CBSSolver,
    ECBSSolver,
    
    # 便捷函数
    solve_mapf,
)

__all__ = [
    'ConflictType', 'Position', 'Constraint', 'Conflict',
    'PathResult', 'CBSNode', 'CBSSolution',
    'ConflictDetector', 'LowLevelPlanner', 'SpaceTimeAStar',
    'CBSSolver', 'ECBSSolver', 'solve_mapf',
]

__version__ = '2.0.0'
