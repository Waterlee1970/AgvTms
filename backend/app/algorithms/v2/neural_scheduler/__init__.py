"""
Neural Scheduler — 神经组合优化调度模块.

提供基于深度学习的 AGV 任务调度能力:

核心组件:
  - NeuralCombinatorialScheduler: GNN + Pointer Network 调度器
  - NeuralSchedulerNet: 端到端可训练的神经网络
  - GraphAttentionLayer: 图注意力编码层
  - PointerDecoder: 指针网络解码层

使用示例:
    from backend.app.algorithms.v2.neural_scheduler import (
        NeuralCombinatorialScheduler, 
        AgvNode, 
        TaskNode,
    )
    
    scheduler = NeuralCombinatorialScheduler()
    
    agvs = [AgvNode(id="agv1", x=5.0, y=3.0, battery=0.9)]
    tasks = [TaskNode(id="task1", pickup_x=10.0, pickup_y=8.0, dropoff_x=15.0, dropoff_y=2.0)]
    
    result = scheduler.schedule(agvs, tasks)
    print(f"Assignments: {result.assignments}")
"""

from .neural_combinatorial import (
    # 核心调度器
    NeuralCombinatorialScheduler,
    
    # 数据结构
    AgvNode,
    TaskNode,
    ScheduleResult,
    OptimizationObjective,
    PredictorType,
    
    # 神经网络模型 (需要 PyTorch)
    NeuralSchedulerNet,
    TaskAgvEncoder,
    PointerDecoder,
    GraphAttentionLayer,
    
    # 启发式回退
    greedy_schedule,
    
    # API 路由工厂
    create_neural_scheduler_routes,
)

__all__ = [
    "NeuralCombinatorialScheduler",
    "NeuralSchedulerNet",
    "TaskAgvEncoder", 
    "PointerDecoder",
    "GraphAttentionLayer",
    "AgvNode",
    "TaskNode",
    "ScheduleResult",
    "OptimizationObjective",
    "PredictorType",
    "greedy_schedule",
    "create_neural_scheduler_routes",
]

# 版本信息
__version__ = "1.0.0"
__author__ = "AGV-TMS Team"
