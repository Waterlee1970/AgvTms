"""
事件总线 (Event Bus) — 参考 openTCS EventBus 设计.

提供发布-订阅模式的事件系统，实现核心组件解耦:
  - 调度器发出事件 → 监控/告警/WebSocket 自动响应
  - 状态变更自动触发事件 → 无需手动通知

特性:
  - 同步 + 异步订阅者支持
  - 容错设计: 单订阅者异常不影响其他
  - 事件类型安全 (EventType 枚举)

参考: openTCS Guava EventBus + TCSObjectEvent 机制
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ==================== 事件类型 ====================

class EventType(str, Enum):
    """系统事件类型 (参考 openTCS TCSObjectEvent)"""
    # 任务生命周期
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_DISPATCHED = "task_dispatched"
    TASK_PROCESSING = "task_processing"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    # AGV 状态
    AGV_STATE_CHANGED = "agv_state_changed"
    AGV_IDLE = "agv_idle"
    AGV_OFFLINE = "agv_offline"
    AGV_LOW_BATTERY = "agv_low_battery"
    AGV_ERROR = "agv_error"

    # 调度事件
    SCHEDULE_STARTED = "schedule_started"
    SCHEDULE_COMPLETED = "schedule_completed"
    SCHEDULE_FAILED = "schedule_failed"

    # 路径与交通
    PATH_BLOCKED = "path_blocked"
    PATH_UNBLOCKED = "path_unblocked"
    DEADLOCK_DETECTED = "deadlock_detected"
    DEADLOCK_RESOLVED = "deadlock_resolved"
    COLLISION_WARNING = "collision_warning"

    # 资源
    RESOURCE_LOCKED = "resource_locked"
    RESOURCE_RELEASED = "resource_released"

    # 对象变更 (参考 openTCS TCSObjectEvent)
    OBJECT_CREATED = "object_created"
    OBJECT_MODIFIED = "object_modified"
    OBJECT_REMOVED = "object_removed"


@dataclass
class Event:
    """事件对象"""
    event_type: EventType
    payload: Any = None
    source: str = ""              # 事件来源标识
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"Event({self.event_type.value}, source={self.source})"


# 订阅者类型: 同步回调或异步回调
SyncSubscriber = Callable[[Event], None]
AsyncSubscriber = Callable[[Event], Any]  # 返回 coroutine


class EventBus:
    """
    事件总线 — 发布-订阅模式.

    用法:
        bus = EventBus()

        # 订阅
        bus.subscribe(EventType.TASK_ASSIGNED, lambda e: print(e.payload))

        # 异步订阅
        async def on_task_assigned(event):
            await notify_frontend(event.payload)
        bus.subscribe_async(EventType.TASK_ASSIGNED, on_task_assigned)

        # 发布
        bus.publish(Event(EventType.TASK_ASSIGNED, payload={"task_id": "T001", "agv_id": "AGV1"}))
    """

    def __init__(self):
        self._sync_subscribers: Dict[EventType, List[SyncSubscriber]] = defaultdict(list)
        self._async_subscribers: Dict[EventType, List[AsyncSubscriber]] = defaultdict(list)
        self._global_subscribers: List[Union[SyncSubscriber, AsyncSubscriber]] = []
        self._event_history: List[Event] = []
        self._max_history: int = 1000

    def subscribe(self, event_type: EventType, callback: SyncSubscriber) -> None:
        """订阅特定事件类型 (同步回调)"""
        self._sync_subscribers[event_type].append(callback)
        logger.debug("Subscribed sync %s -> %s", event_type.value, callback.__name__)

    def subscribe_async(self, event_type: EventType, callback: AsyncSubscriber) -> None:
        """订阅特定事件类型 (异步回调)"""
        self._async_subscribers[event_type].append(callback)
        logger.debug("Subscribed async %s -> %s", event_type.value, callback.__name__)

    def subscribe_all(self, callback: Union[SyncSubscriber, AsyncSubscriber]) -> None:
        """订阅所有事件 (用于日志/审计)"""
        self._global_subscribers.append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callable) -> None:
        """取消订阅"""
        if callback in self._sync_subscribers[event_type]:
            self._sync_subscribers[event_type].remove(callback)
        if callback in self._async_subscribers[event_type]:
            self._async_subscribers[event_type].remove(callback)

    def publish(self, event: Event) -> None:
        """
        同步发布事件 — 立即执行所有同步订阅者.

        异步订阅者会被调度到事件循环 (如果存在)。
        """
        # 记录历史
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        # 执行同步订阅者 (容错: 单个异常不影响其他)
        for callback in self._sync_subscribers.get(event.event_type, []):
            try:
                callback(event)
            except Exception as e:
                logger.warning("Sync subscriber %s error: %s", callback.__name__, e)

        # 全局同步订阅者
        for callback in self._global_subscribers:
            if not asyncio.iscoroutinefunction(callback):
                try:
                    callback(event)
                except Exception as e:
                    logger.warning("Global subscriber error: %s", e)

        # 调度异步订阅者
        for callback in self._async_subscribers.get(event.event_type, []):
            try:
                coro = callback(event)
                # 尝试在运行中的事件循环里调度
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(coro)
                except RuntimeError:
                    # 无事件循环, 仅记录 (避免阻塞)
                    logger.debug("No event loop for async subscriber %s", callback.__name__)
            except Exception as e:
                logger.warning("Async subscriber %s scheduling error: %s", callback.__name__, e)

        # 全局异步订阅者
        for callback in self._global_subscribers:
            if asyncio.iscoroutinefunction(callback):
                try:
                    coro = callback(event)
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(coro)
                    except RuntimeError:
                        pass
                except Exception as e:
                    logger.warning("Global async subscriber error: %s", e)

    async def publish_async(self, event: Event) -> None:
        """异步发布事件 — await 所有异步订阅者完成"""
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)

        # 同步订阅者
        for callback in self._sync_subscribers.get(event.event_type, []):
            try:
                callback(event)
            except Exception as e:
                logger.warning("Sync subscriber %s error: %s", callback.__name__, e)

        # 异步订阅者 (await)
        for callback in self._async_subscribers.get(event.event_type, []):
            try:
                await callback(event)
            except Exception as e:
                logger.warning("Async subscriber %s error: %s", callback.__name__, e)

        # 全局订阅者
        for callback in self._global_subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.warning("Global subscriber error: %s", e)

    def get_history(self, event_type: Optional[EventType] = None, limit: int = 100) -> List[Event]:
        """获取事件历史 (可选过滤类型)"""
        events = self._event_history if event_type is None else [
            e for e in self._event_history if e.event_type == event_type
        ]
        return events[-limit:]

    def clear_history(self) -> None:
        self._event_history.clear()


# ==================== 全局单例 ====================

event_bus = EventBus()
