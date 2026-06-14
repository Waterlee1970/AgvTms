"""
AGV-TMS 核心层 (Core) — 参考 openTCS Kernel 设计理念.

Phase A (事件驱动基础):
  - event_bus: 事件总线 (Pub/Sub 解耦)
  - order_state_machine: TransportOrder 状态机
  - tcs_object: TCSObject 基类 (实体事件通知)

Phase D (调度循环):
  - scheduler_loop: 持续调度循环 (事件驱动 + 定时检查)
  - time_slot_reservation: 时间片预约 (路径资源锁定)

Phase 5 (架构集成):
  - integration: Phase A-D 组件接入现有系统

Phase 6 (分布式):
  - distributed: Redis Streams 消息队列 + 分布式锁 + 无状态化

Phase 8 (智能化):
  - digital_twin: 3D 数字孪生数据模型
  - rl_production: RL 推理服务 + A/B 测试 + 自适应调度
"""

from .event_bus import EventBus, Event, EventType, event_bus
from .order_state_machine import (
    TransportOrderState,
    OrderStateMachine,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
    IllegalStateTransitionError,
)
from .tcs_object import TCSObjectMixin, TCSObjectType, emit_object_event
from .scheduler_loop import SchedulerLoop, SchedulerLoopConfig, get_scheduler_loop
from .time_slot_reservation import (
    TimeSlotReservation,
    Reservation,
    reservation_manager,
)

# Phase 5
from .integration import (
    initialize_integration,
    start_scheduler_loop,
    stop_scheduler_loop,
    get_strategy_info,
    set_strategy,
    transition_task_status,
    ws_bridge,
)

# Phase 6
from .distributed import (
    DistributedLock,
    MessageQueue,
    DistributedScheduler,
    distributed_scheduler,
    message_queue,
    distributed_lock,
    acquire_schedule_lock,
)

# Phase 8
from .digital_twin import (
    SceneModel,
    Agv3DModel,
    Trajectory3D,
    HeatmapData,
    build_scene_from_schedule,
)
from .rl_production import (
    RLInferenceService,
    ABTestFramework,
    AdaptiveScheduler,
    rl_inference,
    ab_test_framework,
    adaptive_scheduler,
)

__all__ = [
    # Event Bus
    "EventBus", "Event", "EventType", "event_bus",
    # State Machine
    "TransportOrderState", "OrderStateMachine",
    "VALID_TRANSITIONS", "TERMINAL_STATES",
    "IllegalStateTransitionError",
    # TCSObject
    "TCSObjectMixin", "TCSObjectType", "emit_object_event",
    # Scheduler Loop
    "SchedulerLoop", "SchedulerLoopConfig", "get_scheduler_loop",
    # Time Slot Reservation
    "TimeSlotReservation", "Reservation", "reservation_manager",
    # Phase 5 Integration
    "initialize_integration", "start_scheduler_loop", "stop_scheduler_loop",
    "get_strategy_info", "set_strategy", "transition_task_status", "ws_bridge",
    # Phase 6 Distributed
    "DistributedLock", "MessageQueue", "DistributedScheduler",
    "distributed_scheduler", "message_queue", "distributed_lock",
    "acquire_schedule_lock",
    # Phase 8 Digital Twin
    "SceneModel", "Agv3DModel", "Trajectory3D", "HeatmapData",
    "build_scene_from_schedule",
    # Phase 8 RL
    "RLInferenceService", "ABTestFramework", "AdaptiveScheduler",
    "rl_inference", "ab_test_framework", "adaptive_scheduler",
]
