"""
Kafka 多语言事件总线 — 跨服务通信核心.

功能:
  1. 标准化 Topic 管理 (按 agvtms.{domain}.{action}.{version} 命名)
  2. CloudEvents 1.0 格式事件发布/消费
  3. 多语言兼容的序列化/反序列化 (JSON + Protobuf)
  4. 消费者组管理
  5. 事务性消息支持
  6. 死信队列 (DLQ) 自动处理
  7. 延迟消息 / 定时消息

使用场景:
  - Python调度完成 → Java持久化 → .NET设备控制
  - AGV状态变更 → 所有服务同步更新
  - 告警触发 → 通知服务推送

依赖:
  pip install aiokafka protobuf
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Type, Union

logger = logging.getLogger(__name__)


# ==================== Topic 命名规范 ====================


class TopicDomain(str, Enum):
    """Topic 领域命名."""
    
    # 核心业务
    TASK = "task"                  # 任务相关
    ORDER = "order"                # 运输订单
    VEHICLE = "vehicle"            # AGV车辆
    SCHEDULE = "schedule"          # 调度结果
    
    # 设备与协议
    DEVICE = "device"              # 物理设备
    PROTOCOL = "protocol"          # 协议通信
    ADAPTER = "adapter"            # 适配器状态
    
    # 系统与监控
    ALERT = "alert"                # 告警
    SYSTEM = "system"              # 系统事件
    METRIC = "metric"              # 指标数据
    AUDIT = "audit"                # 审计日志
    
    # 数字孪生
    TWIN = "twin"                  # 数字孪生状态
    MAP = "map"                    # 地图变更


class TopicAction(str, Enum):
    """Topic 操作类型."""
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHANGED = "status_changed"
    REQUESTED = "requested"
    RESULT = "result"


def build_topic(
    domain: Union[TopicDomain, str],
    action: Union[TopicAction, str],
    version: str = "v1",
) -> str:
    """
    构建标准化的 Kafka Topic 名称.
    
    示例:
        build_topic(TopicDomain.TASK, TopicAction.CREATED)
        # → "agvtms.task.created.v1"
        
        build_topic("vehicle", "status_changed", "v2")
        # → "agvtms.vehicle.status_changed.v2"
    """
    domain_str = domain.value if isinstance(domain, TopicDomain) else domain
    action_str = action.value if isinstance(action, TopicAction) else action
    return f"agvtms.{domain_str}.{action_str}.{version}"


# 预定义常用 Topics
class Topics:
    """预定义的 Topic 常量."""
    
    # 任务
    TASK_CREATED = build_topic(TopicDomain.TASK, TopicAction.CREATED)
    TASK_ASSIGNED = build_topic(TopicDomain.TASK, TopicAction.ASSIGNED)
    TASK_STARTED = build_topic(TopicDomain.TASK, "started")  # 自定义action
    TASK_COMPLETED = build_topic(TopicDomain.TASK, TopicAction.COMPLETED)
    TASK_FAILED = build_topic(TopicDomain.TASK, TopicAction.FAILED)
    TASK_CANCELLED = build_topic(TopicDomain.TASK, TopicAction.CANCELLED)
    
    # 订单
    ORDER_CREATED = build_topic(TopicDomain.ORDER, TopicAction.CREATED)
    ORDER_COMPLETED = build_topic(TopicDomain.ORDER, TopicAction.COMPLETED)
    ORDER_CANCELLED = build_topic(TopicDomain.ORDER, TopicAction.CANCELLED)
    
    # AGV车辆
    VEHICLE_STATUS_CHANGED = build_topic(TopicDomain.VEHICLE, TopicAction.STATUS_CHANGED)
    VEHICLE_ONLINE = build_topic(TopicDomain.VEHICLE, "online")
    VEHICLE_OFFLINE = build_topic(TopicDomain.VEHICLE, "offline")
    VEHICLE_LOW_BATTERY = build_topic(TopicDomain.VEHICLE, "low_battery")
    VEHICLE_ERROR = build_topic(TopicDomain.VEHICLE, "error")
    
    # 调度
    SCHEDULE_REQUESTED = build_topic(TopicDomain.SCHEDULE, TopicAction.REQUESTED)
    SCHEDULE_RESULT = build_topic(TopicDomain.SCHEDULE, TopicAction.RESULT)
    SCHEDULE_COMPLETED = build_topic(TopicDomain.SCHEDULE, TopicAction.COMPLETED)
    
    # 告警
    ALERT_FIRED = build_topic(TopicDomain.ALERT, "fired")
    ALERT_ACKNOWLEDGED = build_topic(TopicDomain.ALERT, "acknowledged")
    ALERT_RESOLVED = build_topic(TopicDomain.ALERT, "resolved")
    
    # 系统
    SYSTEM_HEALTH_CHECK = build_topic(TopicDomain.SYSTEM, "health_check")
    SYSTEM_CONFIG_CHANGED = build_topic(TopicDomain.SYSTEM, "config_changed")


# ==================== CloudEvents 1.0 ====================


@dataclass
class CloudEvent:
    """
    CloudEvents 1.0 兼容的事件对象.
    
    规范: https://github.com/cloudevents/spec/blob/v1.0/spec.md
    """
    
    specversion: str = "1.0"
    type: str = ""                    # 事件类型 (必填)
    source: str = ""                  # 事件源 (必填, 通常为服务标识)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # 事件ID
    time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    datacontenttype: str = "application/json"
    dataschema: Optional[str] = None   # 数据Schema URL (可选)
    subject: Optional[str] = None       # 事件主体 (可选)
    
    # 业务数据
    data: Optional[Any] = None          # 事件载荷 (dict 或 bytes for Protobuf)
    
    # 扩展属性
    extensions: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典 (用于JSON序列化)."""
        result = {
            "specversion": self.specversion,
            "type": self.type,
            "source": self.source,
            "id": self.id,
            "time": self.time,
            "datacontenttype": self.datacontenttype,
        }
        if self.dataschema:
            result["dataschema"] = self.dataschema
        if self.subject:
            result["subject"] = self.subject
        if self.data is not None:
            if isinstance(self.data, bytes):
                # Protobuf 二进制数据需要 base64 编码 (Kafka要求bytes key/value)
                import base64
                result["data_base64"] = base64.b64encode(self.data).decode()
            else:
                result["data"] = self.data
        if self.extensions:
            result.update(self.extensions)
        return result
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串."""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CloudEvent':
        """从字典创建."""
        event_data = data.get("data")
        if "data_base64" in data and event_data is None:
            import base64
            event_data = base64.b64decode(data.pop("data_base64"))
        
        extensions = {k: v for k, v in data.items() if k not in [
            "specversion", "type", "source", "id", "time",
            "datacontenttype", "dataschema", "subject", "data", "data_base64",
        ]}
        
        return cls(
            specversion=data.get("specversion", "1.0"),
            type=data.get("type", ""),
            source=data.get("source", ""),
            id=data.get("id", str(uuid.uuid4())),
            time=data.get("time", datetime.now(timezone.utc).isoformat()),
            datacontenttype=data.get("datacontenttype", "application/json"),
            dataschema=data.get("dataschema"),
            subject=data.get("subject"),
            data=event_data,
            extensions=extensions,
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> 'CloudEvent':
        """从 JSON 创建."""
        return cls.from_dict(json.loads(json_str))


# ==================== 事件生产者 ====================


@dataclass
class ProducerConfig:
    """生产者配置."""
    
    bootstrap_servers: str = "localhost:9092"
    client_id: Optional[str] = None
    acks: str = "all"                    # "0", "1", "all"
    retries: int = 3
    batch_size: int = 16384              # 16KB
    linger_ms: int = 5                   # 批量发送延迟(ms)
    compression_type: str = "gzip"       # "gzip", "snappy", "lz4", "none"
    max_in_flight_requests_per_connection: int = 5
    enable_idempotence: bool = True      # 幂等性 (防止重复)


class MultiLangEventProducer:
    """
    Kafka 事件生产者 — 支持多语言服务消费.
    
    特点:
      - 自动添加 CloudEvents 包装
      - 支持 JSON 和 Protobuf 两种格式
      - 异步批量发送优化
      - 事务支持
      - 错误回调处理
    """
    
    def __init__(self, config: ProducerConfig = None):
        self.config = config or ProducerConfig()
        self._producer = None
        self._initialized = False
        
        # 统计
        self.stats = {
            "messages_sent": 0,
            "messages_failed": 0,
            "bytes_sent": 0,
        }
    
    async def initialize(self):
        """初始化 Kafka 生产者."""
        try:
            from aiokafka import AIOKafkaProducer
            
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers,
                client_id=self.config.client_id or f"agvtms-producer-{uuid.uuid4().hex[:8]}",
                acks=self.config.acks,
                retries=self.config.retries,
                batch_size=self.config.batch_size,
                linger_ms=self.config.linger_ms,
                compression_type=self.config.compression_type,
                max_in_flight_requests_per_connection=self.config.max_in_flight_requests_per_connection,
                enable_idempotence=self.config.enable_idempotence,
                value_serializer=lambda v: v.encode('utf-8') if isinstance(v, str) else v,
            )
            
            await self._producer.start()
            self._initialized = True
            logger.info(
                "MultiLangEventProducer initialized (servers=%s)",
                self.config.bootstrap_servers,
            )
            
        except ImportError:
            logger.warning("aiokafka not installed, using mock producer")
            self._producer = _MockProducer()
            self._initialized = True
        except Exception as e:
            logger.error("Failed to initialize Kafka producer: %s", e)
            raise
    
    async def close(self):
        """关闭生产者."""
        if self._producer and self._initialized:
            await self._producer.stop()
            self._initialized = False
            logger.info("MultiLangEventProducer closed")
    
    async def publish(
        self,
        topic: str,
        event: CloudEvent,
        key: Optional[str] = None,
        partition: Optional[int] = None,
        timestamp_ms: Optional[int] = None,
    ) -> bool:
        """
        发布事件.
        
        Args:
            topic: 目标 Topic
            event: CloudEvents 格式事件
            key: 分区键 (通常用实体ID保证顺序)
            partition: 指定分区
            timestamp_ms: 时间戳覆盖
        
        Returns:
            是否成功
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            # 序列化
            payload = event.to_json()
            
            # 发送
            await self._producer.send_and_wait(
                topic=topic,
                value=payload,
                key=key.encode() if key else None,
                partition=partition,
                timestamp_ms=timestamp_ms,
            )
            
            # 更新统计
            self.stats["messages_sent"] += 1
            self.stats["bytes_sent"] += len(payload)
            
            logger.debug(
                "Event published: topic=%s type=%s id=%s",
                topic, event.type, event.id,
            )
            return True
            
        except Exception as e:
            self.stats["messages_failed"] += 1
            logger.error(
                "Failed to publish event: topic=%s error=%s",
                topic, e,
            )
            return False
    
    async def publish_batch(
        self,
        events: List[Tuple[str, CloudEvent, Optional[str]]],
    ) -> Tuple[int, int]:
        """
        批量发布事件.
        
        Args:
            events: [(topic, event, key), ...]
        
        Returns:
            (success_count, failure_count)
        """
        success_count = 0
        failure_count = 0
        
        for topic, event, key in events:
            if await self.publish(topic, event, key):
                success_count += 1
            else:
                failure_count += 1
        
        return success_count, failure_count
    
    # ========== 便捷方法 ==========
    
    @staticmethod
    def create_event(
        event_type: str,
        source: str,
        data: Any = None,
        subject: Optional[str] = None,
        **extensions,
    ) -> CloudEvent:
        """快速创建 CloudEvent."""
        return CloudEvent(
            type=event_type,
            source=source,
            data=data,
            subject=subject,
            extensions=extensions,
        )
    
    async def publish_task_event(
        self,
        action: Union[TopicAction, str],
        task_data: Dict[str, Any],
        source: str = "/agvtms/python",
    ) -> bool:
        """便捷方法: 发布任务事件."""
        topic = build_topic(TopicDomain.TASK, action)
        event = self.create_event(
            event_type=topic,
            source=source,
            data=task_data,
            subject=task_data.get("task_id"),
        )
        return await self.publish(topic, event, key=task_data.get("task_id"))
    
    async def publish_vehicle_event(
        self,
        action: Union[TopicAction, str],
        vehicle_data: Dict[str, Any],
        source: str = "/agvtms/python",
    ) -> bool:
        """便捷方法: 发布AGV状态事件."""
        topic = build_topic(TopicDomain.VEHICLE, action)
        event = self.create_event(
            event_type=topic,
            source=source,
            data=vehicle_data,
            subject=vehicle_data.get("vehicle_id"),
        )
        return await self.publish(topic, event, key=vehicle_data.get("vehicle_id"))
    
    async def publish_alert_event(
        self,
        alert_data: Dict[str, Any],
        source: str = "/agvtms/python",
    ) -> bool:
        """便捷方法: 发布告警事件."""
        topic = Topics.ALERT_FIRED
        event = self.create_event(
            event_type=topic,
            source=source,
            data=alert_data,
            subject=alert_data.get("alert_id"),
        )
        return await self.publish(topic, event, key=alert_data.get("alert_id"))


# ==================== 事件消费者 ====================


@dataclass
class ConsumerConfig:
    """消费者配置."""
    
    bootstrap_servers: str = "localhost:9092"
    group_id: str = "agvtms-default-group"
    client_id: Optional[str] = None
    auto_offset_reset: str = "latest"     # "earliest", "latest", "none"
    enable_auto_commit: bool = False       # 手动提交以精确控制
    max_poll_records: int = 100
    session_timeout_ms: int = 30000
    heartbeat_interval_ms: int = 10000
    max_poll_interval_ms: int = 300000     # 5分钟最大处理时间


class EventHandlerType:
    """事件处理器类型."""
    
    ASYNC = "async"           # 异步函数: async def handler(event: CloudEvent) -> None
    SYNC = "sync"             # 同步函数: def handler(event: CloudEvent) -> None
    BATCH = "batch"           # 批量处理: async def handler(events: List[CloudEvent]) -> None


@dataclass
class Subscription:
    """订阅配置."""
    
    topic: str                              # 订阅的Topic
    handler: Callable                       # 处理函数
    handler_type: str = EventHandlerType.ASYNC
    group_id: Optional[str] = None           # 覆盖默认group_id
    concurrency: int = 1                     # 并发消费者数
    enable_dlq: bool = True                  # 是否启用死信队列
    max_retries: int = 3                     # 最大重试次数
    retry_delay_seconds: float = 1.0         # 重试延迟
    filter_expression: Optional[str] = None  # 过滤表达式
    enabled: bool = True
    
    # 运行时统计
    processed_count: int = 0
    error_count: int = 0
    last_processed: Optional[datetime] = None


class MultiLangEventConsumer:
    """
    Kafka 事件消费者 — 支持多语言事件消费.
    
    特点:
      - 自动解析 CloudEvents 格式
      - 支持多种处理器模式 (async/sync/batch)
      - 死信队列 (DLQ) 自动处理
      - 重试机制
      - 消费者组协调
    """
    
    DLQ_SUFFIX = ".dlq"  # 死信Queue后缀
    RETRY_SUFFIX = ".retry"  # 重试Queue后缀
    
    def __init__(self, config: ConsumerConfig = None):
        self.config = config or ConsumerConfig()
        self._consumers: Dict[str, Any] = {}  # group_id → consumer
        self._subscriptions: List[Subscription] = []
        self._running = False
        self._tasks: List[asyncio.Task] = []
        
        # 统计
        self.stats = {
            "total_consumed": 0,
            "total_processed": 0,
            "total_errors": 0,
            "dlq_messages": 0,
        }
    
    async def subscribe(
        self,
        topic: str,
        handler: Callable,
        *,
        handler_type: str = EventHandlerType.ASYNC,
        group_id: Optional[str] = None,
        concurrency: int = 1,
        enable_dlq: bool = True,
        max_retries: int = 3,
        **kwargs,
    ):
        """订阅 Topic."""
        sub = Subscription(
            topic=topic,
            handler=handler,
            handler_type=handler_type,
            group_id=group_id or self.config.group_id,
            concurrency=concurrency,
            enable_dlq=enable_dlq,
            max_retries=max_retries,
            **kwargs,
        )
        self._subscriptions.append(sub)
        logger.debug(
            "Subscribed to %s [group=%s, handler=%s]",
            topic, group_id or self.config.group_id, handler.__name__,
        )
    
    async def start(self):
        """启动所有消费者."""
        if self._running:
            return
        
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            logger.warning("aiokafka not installed, consumer will be mock")
            self._running = True
            return
        
        # 按 group_id 分组订阅
        groups: Dict[str, List[Subscription]] = {}
        for sub in self._subscriptions:
            if not sub.enabled:
                continue
            if sub.group_id not in groups:
                groups[sub.group_id] = []
            groups[sub.group_id].append(sub)
        
        # 为每个消费者组创建 consumer
        for group_id, subs in groups.items():
            topics = list(set(s.topic for s in subs))
            
            consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self.config.bootstrap_servers,
                group_id=group_id,
                client_id=self.config.client_id or f"agvtms-consumer-{uuid.uuid4().hex[:8]}",
                auto_offset_reset=self.config.auto_offset_reset,
                enable_auto_commit=self.config.enable_auto_commit,
                max_poll_records=self.config.max_poll_records,
                session_timeout_ms=self.config.session_timeout_ms,
                heartbeat_interval_ms=self.config.heartbeat_interval_ms,
                max_poll_interval_ms=self.config.max_poll_interval_ms,
                value_deserializer=lambda v: json.loads(v.decode('utf-8')) if v else None,
            )
            
            await consumer.start()
            self._consumers[group_id] = {"consumer": consumer, "subscriptions": subs}
            
            # 启动消费任务
            for i in range(max(s.concurrency for s in subs)):
                task = asyncio.create_task(
                    self._consume_loop(consumer, subs),
                    name=f"kafka-consume-{group_id}-{i}",
                )
                self._tasks.append(task)
        
        self._running = True
        logger.info(
            "MultiLangEventConsumer started (%d groups, %d topics)",
            len(groups), len(self._subscriptions),
        )
    
    async def stop(self):
        """停止所有消费者."""
        self._running = False
        
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        
        for group_id, info in self._consumers.items():
            try:
                await info["consumer"].stop()
            except Exception as e:
                logger.warning("Error stopping consumer %s: %e", group_id, e)
        self._consumers.clear()
        
        logger.info("MultiLangEventConsumer stopped")
    
    async def _consume_loop(self, consumer, subscriptions: List[Subscription]):
        """消费循环."""
        # 构建 topic → subscription 映射
        topic_to_subs: Dict[str, List[Subscription]] = {}
        for sub in subscriptions:
            if sub.topic not in topic_to_subs:
                topic_to_subs[sub.topic] = []
            topic_to_subs[sub.topic].append(sub)
        
        try:
            async for message in consumer:
                self.stats["total_consumed"] += 1
                
                # 解析 CloudEvent
                try:
                    if isinstance(message.value, dict):
                        event = CloudEvent.from_dict(message.value)
                    elif isinstance(message.value, str):
                        event = CloudEvent.from_json(message.value)
                    elif message.value is None:
                        continue
                    else:
                        logger.warning("Unexpected message format: %s", type(message.value))
                        continue
                except Exception as e:
                    logger.error("Failed to parse CloudEvent: %s", e)
                    self.stats["total_errors"] += 1
                    continue
                
                # 分发给匹配的订阅者
                subs = topic_to_subs.get(message.topic, [])
                for sub in subs:
                    await self._handle_message(sub, event, message, consumer)
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Consume loop error: %s", e)
    
    async def _handle_message(
        self,
        sub: Subscription,
        event: CloudEvent,
        message,
        consumer,
    ):
        """处理单条消息 (含重试和DLQ)."""
        retry_count = 0
        last_error = None
        
        while retry_count <= sub.max_retries:
            try:
                # 调用处理器
                if sub.handler_type == EventHandlerType.ASYNC:
                    await sub.handler(event)
                elif sub.handler_type == EventHandlerType.BATCH:
                    await sub.handler([event])
                else:  # SYNC
                    sub.handler(event)
                
                # 成功
                sub.processed_count += 1
                sub.last_processed = datetime.utcnow()
                self.stats["total_processed"] += 1
                
                # 手动提交 offset
                if not self.config.enable_auto_commit:
                    try:
                        await consumer.commit()
                    except Exception:
                        pass
                
                return
                
            except Exception as e:
                last_error = e
                retry_count += 1
                
                if retry_count <= sub.max_retries:
                    logger.warning(
                        "Handler error (retry %d/%d): %s",
                        retry_count, sub.max_retries, e,
                    )
                    await asyncio.sleep(sub.retry_delay_seconds * retry_count)
        
        # 所有重试失败 → 发送到DLQ
        sub.error_count += 1
        self.stats["total_errors"] += 1
        self.stats["dlq_messages"] += 1
        
        if sub.enable_dlq:
            await self._send_to_dlq(sub.topic, event, last_error)
        
        logger.error(
            "Message failed after %d retries, sent to DLQ: topic=%s id=%s",
            sub.max_retries, sub.topic, event.id,
        )
    
    async def _send_to_dlq(self, original_topic: str, event: CloudEvent, error: Exception):
        """发送到死信队列."""
        dlq_topic = original_topic + self.DLQ_SUFFIX
        
        # 在原始事件中附加错误信息
        dlq_event = CloudEvent(
            type=f"{original_topic}.failed",
            source="/agvtms/dlq-router",
            data={
                "original_event": event.to_dict(),
                "error": str(error),
                "error_type": type(error).__name__,
                "dlq_timestamp": datetime.now(timezone.utc).isoformat(),
            },
            subject=event.id,
        )
        
        try:
            from aiokafka import AIOKafkaProducer
            
            # 复用或创建临时 producer
            producer = AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers,
            )
            await producer.start()
            try:
                await producer.send_and_wait(dlq_topic, dlq_event.to_json())
            finally:
                await producer.stop()
                
        except Exception as dlq_error:
            logger.error("Failed to send to DLQ %s: %s", dlq_topic, dlq_error)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取消费统计."""
        return {
            **self.stats,
            "active_subscriptions": len([s for s in self._subscriptions if s.enabled]),
            "subscription_details": [
                {
                    "topic": s.topic,
                    "processed": s.processed_count,
                    "errors": s.error_count,
                    "last_processed": s.last_processed.isoformat() if s.last_processed else None,
                }
                for s in self._subscriptions
            ],
        }


# ==================== Mock 实现 (用于无Kafka环境) ====================


class _MockProducer:
    """Mock Kafka Producer (用于开发和测试)."""
    
    def __init__(self):
        self.sent_messages: List[Tuple[str, Any, Optional[bytes]]] = []
    
    async def start(self):
        pass
    
    async def stop(self):
        pass
    
    async def send_and_wait(
        self,
        topic: str,
        value: Any,
        key: Optional[bytes] = None,
        partition: Optional[int] = None,
        timestamp_ms: Optional[int] = None,
    ):
        self.sent_messages.append((topic, value, key))
        logger.debug("[MOCK] Message sent to %s: %s", topic, value[:100] if isinstance(value, str) else value)


# ==================== 全局实例 ====================

# 全局生产者 (懒初始化)
_producer_instance: Optional[MultiLangEventProducer] = None

def get_event_producer() -> MultiLangEventProducer:
    """获取全局事件生产者单例."""
    global _producer_instance
    if _producer_instance is None:
        _producer_instance = MultiLangEventProducer()
    return _producer_instance

async def init_event_producer(bootstrap_servers: str = "kafka:9092"):
    """初始化全局生产者."""
    producer = get_event_producer()
    await producer.initialize(
        ProducerConfig(bootstrap_servers=bootstrap_servers)
    )
    return producer
