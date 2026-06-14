"""
SchedulerLoop 持续调度循环 — 参考 openTCS Kernel Dispatcher 持续运行机制.

openTCS: Kernel 启动后 Dispatcher 持续轮询, 事件触发重调度
AGV-TMS: asyncio 后台任务, 事件驱动 + 定时检查

两种触发模式:
  1. 事件触发: 新订单到达 / AGV 空闲 / 故障恢复 → 立即重调度
  2. 定时触发: 每 N 秒扫描需重调度的任务

用法:
    loop = SchedulerLoop(schedule_service)
    await loop.start()       # 启动后台循环
    await loop.submit_order(task)  # 提交新订单 (触发重调度)
    await loop.stop()        # 停止
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SchedulerLoopConfig:
    """调度循环配置"""
    check_interval: float = 5.0          # 定时检查间隔 (秒)
    max_concurrent_dispatches: int = 3   # 最大并发分派
    redispatch_on_idle: bool = True      # AGV 空闲时重调度
    redispatch_on_failure: bool = True   # 故障恢复时重调度
    auto_charge_threshold: float = 20.0  # 自动充电电量阈值


class SchedulerLoop:
    """
    持续调度循环 — 事件驱动 + 定时检查.

    生命周期:
      start() → _loop() (持续运行) → stop()

    事件触发:
      submit_order()      → 设置 _order_event → 立即唤醒循环
      notify_agv_idle()   → 设置 _redispatch_event
      notify_failure()    → 设置 _failure_event
    """

    def __init__(
        self,
        schedule_service=None,
        config: Optional[SchedulerLoopConfig] = None,
    ):
        self._schedule_service = schedule_service
        self._config = config or SchedulerLoopConfig()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 事件信号
        self._order_queue: asyncio.Queue = None  # 延迟初始化 (需事件循环)
        self._redispatch_event: asyncio.Event = None
        self._failure_event: asyncio.Event = None

        # 统计
        self._stats = {
            "total_dispatches": 0,
            "total_orders_processed": 0,
            "total_redispatches": 0,
            "total_failures_handled": 0,
            "last_dispatch_time": None,
            "uptime_start": None,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    async def start(self) -> None:
        """启动调度循环"""
        if self._running:
            logger.warning("SchedulerLoop already running")
            return

        self._order_queue = asyncio.Queue()
        self._redispatch_event = asyncio.Event()
        self._failure_event = asyncio.Event()

        self._running = True
        self._stats["uptime_start"] = datetime.now().isoformat()
        self._task = asyncio.create_task(self._loop())
        logger.info("SchedulerLoop started (interval=%.1fs)", self._config.check_interval)

    async def stop(self) -> None:
        """停止调度循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SchedulerLoop stopped. Stats: %s", self._stats)

    async def submit_order(self, order_data: Any) -> None:
        """提交新订单 (触发重调度)"""
        if self._order_queue:
            await self._order_queue.put(order_data)
            logger.debug("Order submitted to scheduler loop")

    def notify_agv_idle(self, agv_id: str) -> None:
        """通知 AGV 空闲 (触发重调度)"""
        if self._redispatch_event:
            self._redispatch_event.set()
            logger.debug("AGV %s idle notification received", agv_id)

    def notify_failure(self, agv_id: str, error: str) -> None:
        """通知故障 (触发恢复)"""
        if self._failure_event:
            self._failure_event.set()
            logger.warning("AGV %s failure notification: %s", agv_id, error)

    async def _loop(self) -> None:
        """主循环 — 事件驱动 + 定时检查"""
        logger.info("SchedulerLoop main loop started")

        while self._running:
            try:
                # 等待事件或超时
                try:
                    order = await asyncio.wait_for(
                        self._order_queue.get(),
                        timeout=self._config.check_interval,
                    )
                    # 有新订单, 立即处理
                    await self._handle_new_order(order)
                except asyncio.TimeoutError:
                    # 定时检查
                    await self._periodic_check()

                # 检查重调度信号
                if self._redispatch_event and self._redispatch_event.is_set():
                    self._redispatch_event.clear()
                    await self._handle_redispatch()

                # 检查故障信号
                if self._failure_event and self._failure_event.is_set():
                    self._failure_event.clear()
                    await self._handle_failure_recovery()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SchedulerLoop error: %s", e, exc_info=True)
                await asyncio.sleep(5.0)  # 错误后等待

    async def _handle_new_order(self, order_data: Any) -> None:
        """处理新订单"""
        logger.info("Processing new order: %s", order_data)
        self._stats["total_orders_processed"] += 1
        await self._dispatch()

    async def _periodic_check(self) -> None:
        """定时检查 — 扫描需重调度的任务"""
        if not self._schedule_service:
            return

        try:
            metrics = await self._schedule_service.get_metrics_async()
            pending = metrics.get("pending_tasks", 0)
            if pending > 0:
                logger.debug("Periodic check: %d pending tasks, triggering dispatch", pending)
                await self._dispatch()
        except Exception as e:
            logger.debug("Periodic check error: %s", e)

    async def _handle_redispatch(self) -> None:
        """处理重调度 (AGV 空闲)"""
        logger.info("Handling redispatch (AGV idle)")
        self._stats["total_redispatches"] += 1
        await self._dispatch()

    async def _handle_failure_recovery(self) -> None:
        """处理故障恢复"""
        logger.info("Handling failure recovery")
        self._stats["total_failures_handled"] += 1
        # 故障恢复也触发重调度
        await self._dispatch()

    async def _dispatch(self) -> None:
        """执行调度分派"""
        if not self._schedule_service:
            return

        try:
            self._stats["last_dispatch_time"] = datetime.now().isoformat()
            result = await self._schedule_service.run_scheduling_async()
            self._stats["total_dispatches"] += 1
            logger.info(
                "Dispatch completed: %d assignments, %.1fms",
                len(result.assignments), result.algorithm_runtime_ms
            )

            # 触发事件总线
            try:
                from app.core.event_bus import event_bus, Event, EventType
                event_bus.publish(Event(
                    event_type=EventType.SCHEDULE_COMPLETED,
                    payload={
                        "result_id": result.id,
                        "assignments": len(result.assignments),
                        "makespan": result.makespan,
                    },
                    source="scheduler_loop",
                ))
            except Exception:
                pass

        except Exception as e:
            logger.error("Dispatch failed: %s", e)
            try:
                from app.core.event_bus import event_bus, Event, EventType
                event_bus.publish(Event(
                    event_type=EventType.SCHEDULE_FAILED,
                    payload={"error": str(e)},
                    source="scheduler_loop",
                ))
            except Exception:
                pass


# ==================== 全局单例 (延迟初始化) ====================

_scheduler_loop: Optional[SchedulerLoop] = None


def get_scheduler_loop() -> SchedulerLoop:
    """获取全局调度循环实例"""
    global _scheduler_loop
    if _scheduler_loop is None:
        _scheduler_loop = SchedulerLoop()
    return _scheduler_loop
