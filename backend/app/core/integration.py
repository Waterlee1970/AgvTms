"""
Phase 5: 架构集成层 — 将 Phase A-D 组件接入现有系统.

功能:
  1. 事件总线集成: schedule_service 调度完成/失败自动发出事件
  2. 状态机集成: 任务状态更新使用 OrderStateMachine
  3. 适配器集成: industrial_router 使用 adapter_manager
  4. WebSocket 事件推送: 事件总线 → WebSocket 实时推送
  5. 策略切换 API: 运行时切换 CostFunction/Dispatcher/Router
  6. SchedulerLoop 启动: main.py lifespan 中自动启动
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .event_bus import event_bus, Event, EventType
from .order_state_machine import OrderStateMachine, TransportOrderState
from .scheduler_loop import get_scheduler_loop

logger = logging.getLogger(__name__)


# ==================== 1. 事件总线订阅器 ====================

class EventToWebSocketBridge:
    """
    事件总线 → WebSocket 桥接器.

    订阅事件总线, 将事件转发到 WebSocket 连接管理器,
    实现前端实时推送。
    """

    def __init__(self):
        self._ws_manager = None
        self._subscribed = False

    def setup(self, ws_manager):
        """设置 WebSocket 管理器并订阅事件"""
        self._ws_manager = ws_manager
        if not self._subscribed:
            # 订阅关键事件类型
            for event_type in [
                EventType.TASK_ASSIGNED, EventType.TASK_COMPLETED, EventType.TASK_FAILED,
                EventType.AGV_STATE_CHANGED, EventType.AGV_ERROR, EventType.AGV_LOW_BATTERY,
                EventType.SCHEDULE_COMPLETED, EventType.SCHEDULE_FAILED,
                EventType.DEADLOCK_DETECTED, EventType.PATH_BLOCKED,
            ]:
                event_bus.subscribe(event_type, self._on_event)
            self._subscribed = True
            logger.info("EventToWebSocketBridge subscribed to %d event types", 10)

    def _on_event(self, event: Event):
        """事件回调 → WebSocket 广播"""
        if self._ws_manager:
            try:
                import asyncio
                message = {
                    "type": f"event_{event.event_type.value}",
                    "data": event.payload,
                    "source": event.source,
                    "timestamp": event.timestamp.isoformat(),
                }
                # 尝试异步广播
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._ws_manager.broadcast(message))
                except RuntimeError:
                    pass  # 无事件循环, 跳过
            except Exception as e:
                logger.debug("WebSocket bridge error: %s", e)


# 全局桥接器实例
ws_bridge = EventToWebSocketBridge()


# ==================== 2. 状态机集成辅助 ====================

def create_order_state_machine(task_id: str, current_status: str = "pending") -> OrderStateMachine:
    """
    从旧 AgvTaskStatus 创建状态机.

    用于 schedule_service 中替换直接的状态赋值。
    """
    return OrderStateMachine.from_legacy_status(task_id, current_status)


def transition_task_status(
    task_id: str,
    current_status: str,
    target_legacy_status: str,
    reason: str = "",
) -> str:
    """
    安全转换任务状态 (兼容旧 API).

    Args:
        task_id: 任务 ID
        current_status: 当前状态 (旧格式: pending/assigned/in_progress/completed/failed/cancelled)
        target_legacy_status: 目标状态 (旧格式)
        reason: 转换原因

    Returns:
        新状态 (旧格式字符串), 如果转换非法则返回原状态
    """
    from .order_state_machine import LEGACY_TO_NEW, NEW_TO_LEGACY, VALID_TRANSITIONS

    sm = OrderStateMachine.from_legacy_status(task_id, current_status)
    target_state = LEGACY_TO_NEW.get(target_legacy_status)

    if target_state and sm.can_transition(target_state):
        try:
            sm.transition(target_state, reason=reason)
            return sm.legacy_state
        except Exception as e:
            logger.warning("Status transition failed for %s: %s", task_id, e)

    return current_status  # 转换失败, 返回原状态


# ==================== 3. 策略切换 API 辅助 ====================

def get_strategy_info() -> Dict[str, Any]:
    """获取当前所有策略配置信息"""
    from ..algorithms.v2.core import (
        CostFunctionRegistry, DispatcherRegistry, RouterRegistry,
    )
    from ..adapters import AdapterRegistry

    return {
        "cost_functions": {
            "available": CostFunctionRegistry.list_functions(),
            "current": CostFunctionRegistry._default,
        },
        "dispatchers": {
            "available": DispatcherRegistry.list_dispatchers(),
            "current": DispatcherRegistry._default,
        },
        "routers": {
            "available": RouterRegistry.list_routers(),
            "current": RouterRegistry._default,
        },
        "adapters": {
            "available": AdapterRegistry.list_adapters(),
        },
    }


def set_strategy(
    cost_function: Optional[str] = None,
    dispatcher: Optional[str] = None,
    router: Optional[str] = None,
) -> Dict[str, str]:
    """
    运行时切换策略.

    Returns:
        切换后的策略配置
    """
    from ..algorithms.v2.core import (
        CostFunctionRegistry, DispatcherRegistry, RouterRegistry,
    )

    result = {}
    if cost_function:
        CostFunctionRegistry.set_default(cost_function)
        result["cost_function"] = cost_function
    if dispatcher:
        DispatcherRegistry.set_default(dispatcher)
        result["dispatcher"] = dispatcher
    if router:
        RouterRegistry.set_default(router)
        result["router"] = router

    logger.info("Strategy switched: %s", result)
    return result


# ==================== 4. SchedulerLoop 生命周期 ====================

async def start_scheduler_loop(schedule_service=None):
    """启动调度循环 (在 main.py lifespan 中调用)"""
    loop = get_scheduler_loop()
    if schedule_service:
        loop._schedule_service = schedule_service
    if not loop.is_running:
        await loop.start()
        logger.info("SchedulerLoop started via integration layer")


async def stop_scheduler_loop():
    """停止调度循环"""
    loop = get_scheduler_loop()
    if loop.is_running:
        await loop.stop()
        logger.info("SchedulerLoop stopped")


# ==================== 5. 初始化集成 ====================

def initialize_integration(ws_manager=None):
    """
    初始化 Phase 5 集成 — 在应用启动时调用.

    Args:
        ws_manager: WebSocket 连接管理器 (routes.py 的 manager)
    """
    # 事件总线 → WebSocket 桥接
    if ws_manager:
        ws_bridge.setup(ws_manager)

    # 订阅调度完成事件 → 日志
    def _log_schedule_complete(event: Event):
        logger.info(
            "Schedule completed: %s assignments (source: %s)",
            event.payload.get("assignments", 0) if event.payload else 0,
            event.source,
        )

    event_bus.subscribe(EventType.SCHEDULE_COMPLETED, _log_schedule_complete)

    # 订阅死锁检测 → 告警
    def _alert_deadlock(event: Event):
        logger.warning("Deadlock detected: %s", event.payload)

    event_bus.subscribe(EventType.DEADLOCK_DETECTED, _alert_deadlock)

    logger.info("Phase 5 integration initialized (ws_bridge=%s)", ws_manager is not None)
