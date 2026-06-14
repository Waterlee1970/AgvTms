"""
TransportOrder 状态机 — 参考 openTCS TransportOrder 生命周期.

openTCS 状态流:
  RAW → DISPATCHABLE → BEING_PROCESSED → COMPLETE / FAILED
                          ↘ UNROUTABLE

AGV-TMS 扩展状态流:
  RAW → DISPATCHABLE → ASSIGNED → PROCESSING → AWAITING_RESPONSE → COMPLETE
                         ↓            ↓                                  ↘ FAILED
                      UNROUTABLE    FAILED                                 ↘ CANCELLED

特性:
  - 8 态完整生命周期
  - 合法转换守卫 (非法转换抛异常)
  - 状态变更自动触发事件总线事件
  - 向后兼容旧 AgvTaskStatus 枚举
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


class TransportOrderState(str, Enum):
    """
    运输订单完整状态 (参考 openTCS TransportOrder.state).

    比 AgvTaskStatus 更细粒度, 支持执行过程追踪。
    """
    RAW = "raw"                      # 刚创建, 尚未校验
    DISPATCHABLE = "dispatchable"    # 已校验, 可分派
    ASSIGNED = "assigned"            # 已分配给 AGV, 待执行
    PROCESSING = "processing"        # AGV 正在执行
    AWAITING_RESPONSE = "awaiting"   # 等待 AGV 回复 (如到达确认)
    COMPLETE = "complete"            # 成功完成
    FAILED = "failed"                # 执行失败
    CANCELLED = "cancelled"          # 被取消
    UNROUTABLE = "unroutable"        # 无法规划路径


# ==================== 合法状态转换表 ====================

VALID_TRANSITIONS: Dict[TransportOrderState, Set[TransportOrderState]] = {
    TransportOrderState.RAW: {
        TransportOrderState.DISPATCHABLE,
        TransportOrderState.FAILED,
        TransportOrderState.CANCELLED,
    },
    TransportOrderState.DISPATCHABLE: {
        TransportOrderState.ASSIGNED,
        TransportOrderState.UNROUTABLE,
        TransportOrderState.CANCELLED,
        TransportOrderState.FAILED,
    },
    TransportOrderState.ASSIGNED: {
        TransportOrderState.PROCESSING,
        TransportOrderState.FAILED,
        TransportOrderState.CANCELLED,
    },
    TransportOrderState.PROCESSING: {
        TransportOrderState.AWAITING_RESPONSE,
        TransportOrderState.COMPLETE,
        TransportOrderState.FAILED,
        TransportOrderState.CANCELLED,
    },
    TransportOrderState.AWAITING_RESPONSE: {
        TransportOrderState.PROCESSING,
        TransportOrderState.COMPLETE,
        TransportOrderState.FAILED,
    },
    TransportOrderState.COMPLETE: set(),       # 终态
    TransportOrderState.FAILED: set(),         # 终态
    TransportOrderState.CANCELLED: set(),      # 终态
    TransportOrderState.UNROUTABLE: {
        TransportOrderState.DISPATCHABLE,  # 路径恢复后可重新分派
        TransportOrderState.CANCELLED,
    },
}

# 终态集合
TERMINAL_STATES = {
    TransportOrderState.COMPLETE,
    TransportOrderState.FAILED,
    TransportOrderState.CANCELLED,
}


# ==================== 旧枚举兼容映射 ====================

# AgvTaskStatus → TransportOrderState
LEGACY_TO_NEW: Dict[str, TransportOrderState] = {
    "pending": TransportOrderState.RAW,
    "assigned": TransportOrderState.ASSIGNED,
    "in_progress": TransportOrderState.PROCESSING,
    "completed": TransportOrderState.COMPLETE,
    "failed": TransportOrderState.FAILED,
    "cancelled": TransportOrderState.CANCELLED,
}

# TransportOrderState → AgvTaskStatus (向下兼容)
NEW_TO_LEGACY: Dict[TransportOrderState, str] = {
    TransportOrderState.RAW: "pending",
    TransportOrderState.DISPATCHABLE: "pending",
    TransportOrderState.ASSIGNED: "assigned",
    TransportOrderState.PROCESSING: "in_progress",
    TransportOrderState.AWAITING_RESPONSE: "in_progress",
    TransportOrderState.COMPLETE: "completed",
    TransportOrderState.FAILED: "failed",
    TransportOrderState.CANCELLED: "cancelled",
    TransportOrderState.UNROUTABLE: "pending",
}


class IllegalStateTransitionError(Exception):
    """非法状态转换异常"""
    pass


class OrderStateMachine:
    """
    订单状态机管理器.

    用法:
        sm = OrderStateMachine(order_id="T001")
        sm.transition(TransportOrderState.DISPATCHABLE)  # RAW → DISPATCHABLE
        sm.transition(TransportOrderState.ASSIGNED)       # DISPATCHABLE → ASSIGNED
        sm.transition(TransportOrderState.PROCESSING)     # ASSIGNED → PROCESSING
        sm.transition(TransportOrderState.COMPLETE)       # PROCESSING → COMPLETE

        if sm.is_terminal():
            print("Order finished")
    """

    def __init__(
        self,
        order_id: str,
        initial_state: TransportOrderState = TransportOrderState.RAW,
    ):
        self.order_id = order_id
        self._state: TransportOrderState = initial_state
        self._history: list = [(initial_state, None)]

    @property
    def state(self) -> TransportOrderState:
        return self._state

    @property
    def legacy_state(self) -> str:
        """返回兼容旧 AgvTaskStatus 的字符串值"""
        return NEW_TO_LEGACY.get(self._state, "pending")

    def can_transition(self, target: TransportOrderState) -> bool:
        """检查是否可以转换到目标状态"""
        return target in VALID_TRANSITIONS.get(self._state, set())

    def transition(
        self,
        target: TransportOrderState,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TransportOrderState:
        """
        执行状态转换.

        Args:
            target: 目标状态
            reason: 转换原因 (记录到历史)
            metadata: 附加元数据

        Returns:
            新状态

        Raises:
            IllegalStateTransitionError: 非法转换
        """
        if not self.can_transition(target):
            raise IllegalStateTransitionError(
                f"Order {self.order_id}: 非法状态转换 {self._state.value} → {target.value}"
            )

        old_state = self._state
        self._state = target
        self._history.append((target, reason))

        logger.info(
            "Order %s state: %s → %s (reason: %s)",
            self.order_id, old_state.value, target.value, reason or "N/A"
        )

        # 触发事件总线 (延迟导入避免循环引用)
        try:
            from .event_bus import event_bus, Event, EventType

            # 映射状态到事件类型
            state_event_map = {
                TransportOrderState.ASSIGNED: EventType.TASK_ASSIGNED,
                TransportOrderState.PROCESSING: EventType.TASK_PROCESSING,
                TransportOrderState.COMPLETE: EventType.TASK_COMPLETED,
                TransportOrderState.FAILED: EventType.TASK_FAILED,
                TransportOrderState.CANCELLED: EventType.TASK_CANCELLED,
            }
            event_type = state_event_map.get(target)
            if event_type:
                event_bus.publish(Event(
                    event_type=event_type,
                    payload={
                        "order_id": self.order_id,
                        "old_state": old_state.value,
                        "new_state": target.value,
                        "reason": reason,
                        "metadata": metadata or {},
                    },
                    source="order_state_machine",
                ))
        except Exception as e:
            logger.debug("Event bus publish failed: %s", e)

        return self._state

    def is_terminal(self) -> bool:
        """是否处于终态"""
        return self._state in TERMINAL_STATES

    def is_active(self) -> bool:
        """是否处于活跃态 (非终态)"""
        return not self.is_terminal()

    def get_history(self) -> list:
        """获取状态转换历史"""
        return list(self._history)

    @classmethod
    def from_legacy_status(cls, order_id: str, legacy_status: str) -> "OrderStateMachine":
        """从旧 AgvTaskStatus 字符串创建状态机"""
        new_state = LEGACY_TO_NEW.get(legacy_status, TransportOrderState.RAW)
        return cls(order_id=order_id, initial_state=new_state)
