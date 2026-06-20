"""
EventBus 事件总线单元测试 — Phase D+ 补充

覆盖 app/core/event_bus.py:
  - EventType 枚举完整性
  - Event dataclass 创建和序列化
  - EventBus 订阅/取消订阅/发布
  - 同步 + 异步订阅者混合
  - 全局订阅者 (subscribe_all)
  - 容错设计 (单个订阅者异常不影响其他)
  - 历史记录管理
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.event_bus import (
    EventBus, Event, EventType,
    SyncSubscriber, AsyncSubscriber, event_bus,
)


# ==================== EventType 枚举 ====================

class TestEventType:
    """事件类型枚举验证."""

    def test_task_lifecycle_events(self):
        """任务生命周期事件完整."""
        task_events = [
            EventType.TASK_CREATED, EventType.TASK_ASSIGNED,
            EventType.TASK_DISPATCHED, EventType.TASK_PROCESSING,
            EventType.TASK_COMPLETED, EventType.TASK_FAILED,
            EventType.TASK_CANCELLED,
        ]
        for evt in task_events:
            assert isinstance(evt.value, str)
            assert len(evt.value) > 0

    def test_agv_state_events(self):
        """AGV 状态事件."""
        assert EventType.AGV_STATE_CHANGED.value == "agv_state_changed"
        assert EventType.AGV_IDLE.value == "agv_idle"
        assert EventType.AGV_ERROR.value == "agv_error"

    def test_schedule_events(self):
        """调度事件."""
        schedule_events = [
            EventType.SCHEDULE_STARTED, EventType.SCHEDULE_COMPLETED,
            EventType.SCHEDULE_FAILED,
        ]
        values = [e.value for e in schedule_events]
        assert "schedule_started" in values
        assert "schedule_failed" in values

    def test_traffic_and_resource_events(self):
        """交通管制和资源事件."""
        traffic_events = [
            EventType.PATH_BLOCKED, EventType.PATH_UNBLOCKED,
            EventType.DEADLOCK_DETECTED, EventType.DEADLOCK_RESOLVED,
            EventType.COLLISION_WARNING,
        ]
        resource_events = [
           EventType.RESOURCE_LOCKED, EventType.RESOURCE_RELEASED,
        ]
        assert len(traffic_events) >= 5
        assert len(resource_events) >= 2

    def test_object_lifecycle_events(self):
        """对象生命周期 (openTCS 风格)."""
        assert EventType.OBJECT_CREATED.value == "object_created"
        assert EventType.OBJECT_MODIFIED.value == "object_modified"
        assert EventType.OBJECT_REMOVED.value == "object_removed"

    def test_total_event_type_count(self):
        """事件类型总数合理 (至少 20 个)."""
        all_types = list(EventType)
        assert len(all_types) >= 20


# ==================== Event Dataclass ====================

class TestEvent:
    """事件对象测试."""

    def test_basic_creation(self):
        """基本创建."""
        event = Event(event_type=EventType.TASK_CREATED, payload={"id": "T001"})
        
        assert event.event_type == EventType.TASK_CREATED
        assert event.payload == {"id": "T001"}
        assert event.source == ""
        assert event.metadata == {}
        assert event.timestamp is not None

    def test_full_event(self):
        """完整字段."""
        event = Event(
            event_type=EventType.AGV_STATE_CHANGED,
            payload={"agv_id": "AGV1", "old": "idle", "new": "moving"},
            source="scheduler",
            metadata={"version": 2},
        )
        
        assert event.source == "scheduler"
        assert event.metadata["version"] == 2
        assert event.payload["agv_id"] == "AGV1"

    def test_str_representation(self):
        """__str__ 格式."""
        event = Event(
            event_type=EventType.TASK_COMPLETED,
            source="worker-01",
        )
        s = str(event)
        # __str__ 使用 event_type.value (小写) 而非枚举名
        assert "task_completed" in s
        assert "worker-01" in s

    def test_default_timestamp_is_recent(self):
        """默认时间戳应是最近的."""
        import time
        event = Event(event_type=EventType.PATH_BLOCKED)
        
        # 时间戳应在过去 2 秒内
        diff = abs(time.time() - event.timestamp.timestamp())
        assert diff < 2.0


# ==================== EventBus 核心 ====================

class TestEventBusBasics:
    """EventBus 基本功能."""

    def test_subscribe_and_publish_sync(self):
        """同步订阅 + 发布."""
        bus = EventBus()
        received = []
        
        bus.subscribe(EventType.TASK_CREATED, lambda e: received.append(e.payload))
        bus.publish(Event(EventType.TASK_CREATED, payload={"id": "T001"}))
        
        assert len(received) == 1
        assert received[0]["id"] == "T001"

    def test_multiple_subscribers_same_event(self):
        """同一事件的多个订阅者."""
        bus = EventBus()
        results = []
        
        bus.subscribe(EventType.TASK_CREATED, lambda e: results.append("sub1"))
        bus.subscribe(EventType.TASK_CREATED, lambda e: results.append("sub2"))
        bus.subscribe(EventType.TASK_CREATED, lambda e: results.append("sub3"))
        bus.publish(Event(EventType.TASK_CREATED))
        
        assert results == ["sub1", "sub2", "sub3"]

    def test_no_subscriber_no_error(self):
        """无订阅者时不报错."""
        bus = EventBus()
        bus.publish(Event(EventType.TASK_CREATED))  # 不崩溃

    def test_wrong_event_type_not_triggered(self):
        """错误的事件类型不触发订阅者."""
        bus = EventBus()
        received = []
        
        bus.subscribe(EventType.TASK_CREATED, lambda e: received.append("got"))
        bus.publish(Event(EventType.TASK_FAILED))  # 不是 TASK_CREATED
        
        assert len(received) == 0


# ==================== 取消订阅 ====================

class TestUnsubscribe:
    """取消订阅."""

    def test_unsubscribe_single(self):
        """取消单个订阅者."""
        bus = EventBus()
        received = []
        
        cb = lambda e: received.append("active")
        bus.subscribe(EventType.TASK_CREATED, cb)
        bus.publish(Event(EventType.TASK_CREATED))
        assert len(received) == 1
        
        bus.unsubscribe(EventType.TASK_CREATED, cb)
        bus.publish(Event(EventType.TASK_CREATED))
        assert len(received) == 1  # 不再增加

    def test_unsubscribe_nonexistent(self):
        """取消不存在的订阅者不报错."""
        bus = EventBus()
        cb = lambda e: None
        
        # 未订阅就取消
        bus.unsubscribe(EventType.TASK_CREATED, cb)  # 不崩溃


# ==================== 全局订阅者 ====================

class TestGlobalSubscribers:
    """subscribe_all 全局监听."""

    def test_global_receives_all_events(self):
        """全局订阅者接收所有事件."""
        bus = EventBus()
        received_types = []
        
        bus.subscribe_all(lambda e: received_types.append(e.event_type.value))
        
        bus.publish(Event(EventType.TASK_CREATED))
        bus.publish(Event(EventType.AGV_ERROR))
        bus.publish(Event(EventType.DEADLOCK_DETECTED))
        
        assert len(received_types) == 3
        assert "task_created" in received_types
        assert "agv_error" in received_types
        assert "deadlock_detected" in received_types

    def test_global_with_specific(self):
        """全局 + 特定类型同时触发."""
        bus = EventBus()
        specific = []
        global_r = []
        
        bus.subscribe(EventType.TASK_CREATED, lambda e: specific.append("spec"))
        bus.subscribe_all(lambda e: global_r.append("glob"))
        
        bus.publish(Event(EventType.TASK_CREATED))
        
        assert specific == ["spec"]
        assert global_r == ["glob"]  # 全局也收到了


# ==================== 异步发布 ====================

class TestAsyncPublish:
    """异步发布测试."""

    @pytest.mark.asyncio
    async def test_publish_async_calls_async_subscribers(self):
        """异步发布调用异步订阅者."""
        bus = EventBus()
        received = []
        
        async def on_task(event):
            received.append(event.payload["id"])
        
        bus.subscribe_async(EventType.TASK_CREATED, on_task)
        await bus.publish_async(Event(EventType.TASK_CREATED, payload={"id": "T001"}))
        
        assert len(received) == 1
        assert received[0] == "T001"

    @pytest.mark.asyncio
    async def test_publish_async_also_calls_sync(self):
        """异步发布也调用同步订阅者."""
        bus = EventBus()
        sync_received = []
        async_received = []
        
        bus.subscribe(EventType.TASK_CREATED, lambda e: sync_received.append("sync"))
        bus.subscribe_async(EventType.TASK_CREATED, lambda e: async_received.append("async"))
        
        await bus.publish_async(Event(EventType.TASK_CREATED))
        
        assert sync_received == ["sync"]
        assert async_received == ["async"]

    @pytest.mark.asyncio
    async def test_multiple_async_subscribers_order(self):
        """多个异步订阅者按顺序执行."""
        bus = EventBus()
        order = []
        
        async def sub_a(event): order.append("A")
        async def sub_b(event): order.append("B")
        async def sub_c(event): order.append("C")
        
        bus.subscribe_async(EventType.TASK_ASSIGNED, sub_a)
        bus.subscribe_async(EventType.TASK_ASSIGNED, sub_b)
        bus.subscribe_async(EventType.TASK_ASSIGNED, sub_c)
        
        await bus.publish_async(Event(EventType.TASK_ASSIGNED))
        
        assert order == ["A", "B", "C"]


# ==================== 容错设计 ====================

class TestFaultTolerance:
    """容错设计 — 单个异常不影响其他."""

    def test_sync_subscriber_exception_doesnt_block_others(self):
        """同步订阅者异常不影响后续订阅者."""
        bus = EventBus()
        results = []
        
        def bad_subscriber(event):
            raise ValueError("Intentional error")
        
        def good_subscriber(event):
            results.append("survived")
        
        bus.subscribe(EventType.TASK_CREATED, bad_subscriber)
        bus.subscribe(EventType.TASK_CREATED, good_subscriber)
        
        bus.publish(Event(EventType.TASK_CREATED))  # 不崩溃
        
        assert results == ["survived"]

    @pytest.mark.asyncio
    async def test_async_subscriber_exception_doesnt_block_others(self):
        """异步订阅者异常不影响后续订阅者."""
        bus = EventBus()
        results = []
        
        async def bad_async(event):
            raise RuntimeError("Async error")
        
        async def good_async(event):
            results.append("async_survived")
        
        bus.subscribe_async(EventType.TASK_CREATED, bad_async)
        bus.subscribe_async(EventType.TASK_CREATED, good_async)
        
        await bus.publish_async(Event(EventType.TASK_CREATED))
        
        assert results == ["async_survived"]

    @pytest.mark.asyncio
    async def test_global_subscriber_exception_ignored(self):
        """全局订阅者异常被忽略."""
        bus = EventBus()
        
        def bad_global(event):
            raise Exception("Global error")
        
        bus.subscribe_all(bad_global)
        # 不崩溃
        await bus.publish_async(Event(EventType.TASK_CREATED))


# ==================== 历史记录 ====================

class TestEventHistory:
    """历史记录管理."""

    def test_history_records_published_events(self):
        """发布后自动记录到历史."""
        bus = EventBus()
        
        bus.publish(Event(EventType.TASK_CREATED))
        bus.publish(Event(EventType.TASK_ASSIGNED))
        bus.publish(Event(EventType.TASK_COMPLETED))
        
        history = bus.get_history()
        assert len(history) == 3
        assert history[0].event_type == EventType.TASK_CREATED
        assert history[2].event_type == EventType.TASK_COMPLETED

    def test_history_filter_by_type(self):
        """按类型过滤历史."""
        bus = EventBus()
        
        bus.publish(Event(EventType.TASK_CREATED))
        bus.publish(Event(EventType.AGV_STATE_CHANGED))
        bus.publish(Event(EventType.TASK_ASSIGNED))
        bus.publish(Event(EventType.AGV_ERROR))
        
        task_events = bus.get_history(event_type=EventType.TASK_CREATED)
        assert len(task_events) == 1
        
        agv_events = bus.get_history(event_type=EventType.AGV_STATE_CHANGED)
        # AGV_STATE_CHANGED 和 AGV_ERROR 都不是 TASK_CREATED
        task_all = bus.get_history(event_type=None)
        assert len(task_all) == 4

    def test_history_limit_param(self):
        """limit 参数限制返回数量."""
        bus = EventBus()
        
        for i in range(10):
            bus.publish(Event(EventType.TASK_CREATED, payload={"i": i}))
        
        last_3 = bus.get_history(limit=3)
        assert len(last_3) == 3
        assert last_3[-1].payload["i"] == 9  # 最新的是最后一条

    def test_max_history_cap(self):
        """历史记录超过上限时自动淘汰旧记录."""
        bus = EventBus()
        bus._max_history = 5  # 修改属性而非构造参数
        
        for i in range(8):
            bus.publish(Event(EventType.TASK_CREATED, payload={"i": i}))
        
        history = bus.get_history()
        assert len(history) <= 5
        # 最早的应该已被淘汰
        payloads = [e.payload["i"] for e in history]
        assert 0 not in payloads  # 第 0 条已淘汰

    def test_clear_history(self):
        """清空历史."""
        bus = EventBus()
        
        bus.publish(Event(EventType.TASK_CREATED))
        bus.publish(Event(EventType.TASK_COMPLETED))
        assert len(bus.get_history()) == 2
        
        bus.clear_history()
        assert len(bus.get_history()) == 0

    def test_default_max_history(self):
        """默认最大历史 1000."""
        bus = EventBus()
        assert bus._max_history == 1000


# ==================== 全局单例 ====================

class TestGlobalSingleton:
    """全局单例 event_bus."""

    def test_global_instance_exists(self):
        """全局单例存在且可用."""
        from app.core.event_bus import event_bus as _bus
        
        assert isinstance(_bus, EventBus)

    def test_global_singleton_is_usable(self):
        """全局单例可正常使用."""
        received = []
        event_bus.subscribe(EventType.TASK_CREATED, lambda e: received.append("global"))
        event_bus.publish(Event(EventType.TASK_CREATED))
        
        assert received == ["global"]
        # 清理
        event_bus.unsubscribe(EventType.TASK_CREATED, received.pop if received else lambda x: None)
        try:
            event_bus.unsubscribe(EventType.TASK_CREATED, list(event_bus._sync_subscribers.get(EventType.TASK_CREATED, []))[0])
        except (IndexError, ValueError):
            pass


# ==================== 高级场景 ====================

class TestAdvancedScenarios:
    """高级使用场景."""

    def test_high_frequency_publishing(self):
        """高频发布不丢失事件."""
        bus = EventBus()
        bus._max_history = 10000
        count = [0]
        
        bus.subscribe(EventType.AGV_STATE_CHANGED, lambda e: count.__setitem__(0, count[0] + 1))
        
        for i in range(100):
            bus.publish(Event(EventType.AGV_STATE_CHANGED))
        
        assert count[0] == 100
        assert len(bus.get_history()) == 100

    def test_payload_with_complex_data(self):
        """复杂数据负载."""
        bus = EventBus()
        received = []
        
        complex_payload = {
            "nested": {"a": [1, 2, 3], "b": {"c": "d"}},
            "list_of_dicts": [{"id": 1}, {"id": 2}],
            "number": 42.5,
            "flag": True,
        }
        
        bus.subscribe(EventType.SCHEDULE_COMPLETED, lambda e: received.append(e.payload))
        bus.publish(Event(EventType.SCHEDULE_COMPLETED, payload=complex_payload))
        
        assert received[0] == complex_payload
        assert received[0]["list_of_dicts"][1]["id"] == 2

    def test_source_tracking(self):
        """来源追踪."""
        bus = EventBus()
        sources = []
        
        bus.subscribe(EventType.DEADLOCK_DETECTED, lambda e: sources.append(e.source))
        
        bus.publish(Event(EventType.DEADLOCK_DETECTED, source="traffic_manager"))
        bus.publish(Event(EventType.DEADLOCK_DETECTED, source="monitoring"))
        bus.publish(Event(EventType.DEADLOCK_DETECTED, source="resolver"))
        
        assert sources == ["traffic_manager", "monitoring", "resolver"]
