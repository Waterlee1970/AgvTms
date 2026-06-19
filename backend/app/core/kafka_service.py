"""
Kafka 消息总线服务 — 异步事件驱动架构 (Phase 5.5)

功能:
  1. Kafka Producer/Consumer 封装 (aiokafka)
  2. 4大 Topic: AGV状态 / 任务调度 / 指令下发 / 系统告警
  3. Consumer Group 并行消费 + 批量写入 DB
  4. 本地内存队列降级 (Kafka 不可用时自动 fallback)
  5. 消息幂等性保证 (消息ID去重)
  6. 死信队列 (DLQ) 失败重试机制
  7. 延迟重试 (指数退避)

依赖:
  - 生产: aiokafka + kafka-python-ng
  - 开发/测试: 自动使用 MemoryQueue 降级

Topic 设计:
  - agv.status.update:   AGV位置/电量/状态变更 (~1000 msg/s, 高频)
  - task.schedule.new:    新任务到达 (中频)  
  - command.dispatch:     下发AGV指令 (低频但关键)
  - system.alert:         告警事件 (突发)
"""

import asyncio
import json
import time
import logging
import threading
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional,
    Set, Tuple, Union, Awaitable
)
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
import hashlib
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ==================== Topic 定义 ====================

class KafkaTopic(str, Enum):
    """Kafka Topic 枚举 — 统一管理所有消息通道"""
    
    # AGV 相关 (高频)
    AGV_STATUS_UPDATE = "agv.status.update"       # AGV位置/电量/状态 ~1000msg/s
    
    # 任务相关 (中频)
    TASK_SCHEDULE_NEW = "task.schedule.new"        # 新任务到达
    TASK_STATUS_CHANGE = "task.status.change"      # 任务状态变更
    
    # 指令相关 (低频但关键)
    COMMAND_DISPATCH = "command.dispatch"          # 下发AGV指令
    COMMAND_ACK = "command.ack"                    # 指令确认/拒绝
    
    # 系统相关 (突发)
    SYSTEM_ALERT = "system.alert"                  # 告警事件
    SYSTEM_METRIC = "system.metric"                # 性能指标上报


# ==================== 数据模型 ====================

@dataclass(frozen=True)
class Message:
    """不可变消息对象 — 保证线程安全"""
    
    topic: KafkaTopic
    key: str                           # 分区键 (通常是 agv_id 或 task_id)
    value: Dict[str, Any]              # 消息体
    message_id: str = field(default_factory=lambda: f"{time.time()*1e6:.0f}")
    timestamp: float = field(default_factory=time.time)
    headers: Dict[str, str] = field(default_factory=dict)
    retry_count: int = 0
    source: str = "unknown"
    
    def __post_init__(self):
        # 冻结后通过 object.__setattr__ 设置
        if not self.message_id:
            object.__setattr__(self, message_id, self._generate_id())
    
    def _generate_id(self) -> str:
        """生成全局唯一消息 ID"""
        raw = f"{self.topic.value}:{self.key}:{time.time()*1e9:.0f}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'topic': self.topic.value,
            'key': self.key,
            'value': self.value,
            'message_id': self.message_id,
            'timestamp': self.timestamp,
            'headers': self.headers,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        return cls(
            topic=KafkaTopic(data['topic']),
            key=data['key'],
            value=data['value'],
            message_id=data.get('message_id'),
            timestamp=data.get('timestamp', time.time()),
            headers=data.get('headers', {}),
            retry_count=data.get('retry_count', 0),
        )


@dataclass
class ConsumerResult:
    """消费者处理结果"""
    success: bool
    message_id: str
    error: Optional[str] = None
    processing_time_ms: float = 0.0


# ==================== 配置 ====================

@dataclass
class KafkaConfig:
    """Kafka 连接配置"""
    
    # Bootstrap servers (逗号分隔)
    bootstrap_servers: str = "localhost:9092"
    
    # Producer 配置
    producer_acks: str = "all"              # all/-1/0/1
    producer_linger_ms: int = 5             # 批量发送延迟
    producer_batch_size: int = 16384        # 16KB 批量大小
    producer_max_retries: int = 3
    producer_retry_backoff_ms: int = 100
    
    # Consumer 配置
    group_id: str = "agv-tms-consumer-group"
    auto_offset_reset: str = "latest"       # earliest/latest
    auto_commit_interval_ms: int = 1000
    session_timeout_ms: int = 30000
    max_poll_records: int = 500             # 单次最大拉取数
    max_poll_interval_ms: int = 300000      # 5分钟最大处理时间
    
    # DLQ (死信队列)
    dlq_enabled: bool = True
    dlq_topic_suffix: str = "-dlq"
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_backoff_max: float = 300.0
    
    # 降级配置
    fallback_enabled: bool = True           # Kafka 不可用时使用内存队列
    fallback_queue_size: int = 10000        # 内存队列最大容量
    
    # 序列化
    serializer: str = "json"                # json / avro / protobuf


# ==================== 文件降级存储 (第三级) ====================

class KafkaFileFallback:
    """
    Kafka 消息文件持久化 — 第三级降级方案
    
    当 MemoryQueue 也接近满时，将消息写入本地文件。
    进程重启后可通过 replay 恢复消息。
    
    特性:
      - JSON Lines 格式 (.jsonl)
      - 按 Topic + 日期分文件
      - 异步写入 (不阻塞生产者)
      - 自动清理过期文件 (默认 7 天)
      - 支持 replay 恢复
    """
    
    def __init__(
        self,
        base_dir: Optional[str] = None,
        max_file_size: int = 50 * 1024 * 1024,   # 50MB per file
        retention_days: int = 7
    ):
        self._base_dir = Path(base_dir or os.path.join(
            os.path.dirname(__file__), '..', '..', 'data', 'kafka_fallback'
        ))
        self._max_file_size = max_file_size
        self._retention_days = retention_days
        self._lock = asyncio.Lock()
        self._write_count: int = 0
        self._error_count: int = 0
        
        # 确保目录存在
        self._base_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(
            f"[KafkaFileFallback] Initialized at {self._base_dir}, "
            f"retention={retention_days}d"
        )
    
    async def write(self, topic: KafkaTopic, message: Message) -> bool:
        """
        写入消息到文件
        
        Args:
            topic: 目标 Topic
            message: 消息对象
            
        Returns:
            是否写入成功
        """
        try:
            date_str = datetime.now().strftime('%Y-%m-%d')
            safe_topic = topic.value.replace('.', '_')
            
            filename = f"{safe_topic}_{date_str}.jsonl"
            filepath = self._base_dir / filename
            
            # 序列化消息
            data = {
                'message_id': message.message_id,
                'topic': message.topic.value,
                'key': message.key,
                'value': message.value,
                'timestamp': message.timestamp,
                'headers': dict(message.headers) if message.headers else {},
                'created_at': datetime.now().isoformat(),
            }
            line = json.dumps(data, ensure_ascii=False, default=str) + '\n'
            
            # 检查文件大小，超过则创建新文件
            if filepath.exists() and filepath.stat().st_size > self._max_file_size:
                filename = f"{safe_topic}_{date_str}_{int(time.time())}.jsonl"
                filepath = self._base_dir / filename
            
            # 异步写文件 (使用线程池避免阻塞事件循环)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sync_write, filepath, line)

            async with self._lock:
                self._write_count += 1

            return True

        except Exception as e:
            async with self._lock:
                self._error_count += 1
            logger.error(f"[KafkaFileFallback] Write failed: {e}")
            return False
    
    def _sync_write(self, filepath: Path, line: str):
        """同步写入文件 (在 executor 中运行)"""
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(line)
    
    async def replay(
        self,
        topic: Optional[KafkaTopic] = None,
        since: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        重放/恢复文件中的消息
        
        Args:
            topic: 可选的 Topic 过滤
            since: 起始时间
            limit: 最大返回数量
            
        Returns:
            恢复的消息列表
        """
        results = []
        pattern = '*.jsonl' if topic is None else f"{topic.value.replace('.', '_')}_*.jsonl"
        
        for filepath in sorted(self._base_dir.glob(pattern), reverse=True):
            if len(results) >= limit:
                break
                
            try:
                loop = asyncio.get_event_loop()
                lines = await loop.run_in_executor(None, self._sync_read, filepath)
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                        
                    try:
                        msg = json.loads(line)
                        
                        # 时间过滤
                        if since:
                            created_at = msg.get('created_at', '')
                            if created_at:
                                msg_time = datetime.fromisoformat(created_at)
                                if msg_time < since:
                                    continue
                        
                        results.append(msg)
                        
                        if len(results) >= limit:
                            break
                            
                    except json.JSONError:
                        continue
                        
            except Exception as e:
                logger.warning(f"[KafkaFileFallback] Failed to read {filepath}: {e}")
        
        logger.info(f"[KafkaFileFallback] Replayed {len(results)} messages")
        return results
    
    def _sync_read(self, filepath: Path) -> List[str]:
        """同步读取文件"""
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.readlines()
    
    async def cleanup_expired(self) -> int:
        """清理过期文件"""
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        removed = 0
        
        for filepath in self._base_dir.glob('*.jsonl'):
            try:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                if mtime < cutoff:
                    filepath.unlink()
                    removed += 1
                    logger.debug(f"[KafkaFileFallback] Removed expired: {filepath.name}")
            except Exception as e:
                logger.warning(f"[KafkaFileFallback] Cleanup error for {filepath}: {e}")
        
        if removed > 0:
            logger.info(f"[KafkaFileFallback] Cleaned up {removed} expired files")
        return removed
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_files = len(list(self._base_dir.glob('*.jsonl')))
        total_size = sum(f.stat().st_size for f in self._base_dir.glob('*.jsonl'))
        
        return {
            'fallback_type': 'file',
            'base_dir': str(self._base_dir),
            'total_files': total_files,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'write_count': self._write_count,
            'error_count': self._error_count,
            'retention_days': self._retention_days,
        }


# ==================== 内存降级队列 (第二级) ====================

class MemoryMessageQueue:
    """
    内存消息队列 — Kafka 不可用时的二级降级方案
    
    三级降级策略:
      Level 1: Kafka (正常路径)
      Level 2: MemoryMessageQueue (快速内存缓冲)
      Level 3: KafkaFileFallback (磁盘持久化)
    
    当内存队列使用率 >80% 时，自动溢写到文件。
    """
    
    def __init__(
        self, 
        max_size_per_topic: int = 10000,
        file_fallback: Optional[KafkaFileFallback] = None,
        spill_threshold: float = 0.8  # 80% 使用率时溢写到文件
    ):
        self._queues: Dict[KafkaTopic, deque] = {}
        self._max_size = max_size_per_topic
        self._lock = asyncio.Lock()
        self._total_messages = int()
        self._dropped_count = int()
        self._spill_to_file_count: int = 0
        self._file_fallback: Optional[KafkaFileFallback] = file_fallback
        self._spill_threshold: float = spill_threshold
        
        # 预创建常用 topic 队列
        for t in KafkaTopic:
            self._queues[t] = deque(maxlen=max_size_per_topic)
    
    async def produce(
        self, 
        topic: KafkaTopic, 
        message: Message,
        block: bool = False,
        timeout: float = 5.0
    ) -> bool:
        """
        发送消息到内存队列
        
        Args:
            topic: 目标 Topic
            message: 消息对象
            block: 队列满时是否阻塞等待
            timeout: 阻塞超时时间
            
        Returns:
            是否成功入队
        """
        if topic not in self._queues:
            async with self._lock:
                self._queues[topic] = deque(maxlen=self._max_size)
        
        queue = self._queues[topic]
        
        if len(queue) >= self._max_size:
            if block:
                # 阻塞等待 (简化实现: 轮询等待空间)
                deadline = time.time() + timeout
                while len(queue) >= self._max_size and time.time() < deadline:
                    await asyncio.sleep(0.01)
            
            if len(queue) >= self._max_size:
                # 队列仍满，丢弃最旧消息
                try:
                    queue.popleft()
                    self._dropped_count += 1
                    logger.warning(
                        f"[MemoryMQ-{topic.value}] Queue full, "
                        f"dropping oldest (total dropped: {self._dropped_count})"
                    )
                except IndexError:
                    pass
        
        queue.append(message)
        self._total_messages += 1
        
        # 三级降级: 检查内存队列使用率，超过阈值时溢写到文件
        if (self._file_fallback is not None and 
            len(queue) >= self._max_size * self._spill_threshold):
            try:
                await self._file_fallback.write(topic, message)
                self._spill_to_file_count += 1
            except Exception as e:
                logger.warning(f"[MemoryMQ] Spill to file failed for {topic}: {e}")
        
        return True
    
    async def consume_batch(
        self,
        topic: KafkaTopic,
        max_messages: int = 100,
        timeout: float = 1.0
    ) -> List[Message]:
        """
        批量消费消息
        
        Args:
            topic: 目标 Topic
            max_messages: 最大消费数量
            timeout: 无消息时的等待时间
            
        Returns:
            消息列表
        """
        if topic not in self._queues or not self._queues[topic]:
            # 等待新消息
            try:
                await asyncio.wait_for(self._wait_for_message(topic), timeout=timeout)
            except asyncio.TimeoutError:
                return []
        
        messages = []
        queue = self._queues[topic]
        
        while len(messages) < max_messages and queue:
            messages.append(queue.popleft())
        
        return messages
    
    async def _wait_for_message(self, topic: KafkaTopic):
        """异步等待新消息到达"""
        while topic not in self._queues or not self._queues[topic]:
            await asyncio.sleep(0.05)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        return {
            'type': 'memory',
            'topics': {t.value: len(q) for t, q in self._queues.items()},
            'total_messages': self._total_messages,
            'dropped_messages': self._dropped_count,
            'max_size_per_topic': self._max_size,
        }
    
    async def clear(self):
        """清空所有队列"""
        async with self._lock:
            for q in self._queues.values():
                q.clear()
            self._total_messages = 0


# ==================== Kafka Producer ====================

class KafkaProducerService:
    """
    Kafka 生产者服务 — 高吞吐量异步发送
    
    特性:
      - 异步批量发送 (linger_ms 批量优化)
      - 自动重试 + 指数退避
      - Kafka 不可用时自动降级到内存队列
      - 消息 ID 幂等性
      - 发送统计监控
    """
    
    def __init__(self, config: Optional[KafkaConfig] = None):
        self.config = config or KafkaConfig()
        self._producer = None
        self._fallback = MemoryMessageQueue(
            max_size_per_topic=self.config.fallback_queue_size
        )
        self._connected = False
        self._using_fallback = False
        self._sent_count = 0
        self._error_count = 0
        self._dedup_cache: Set[str] = set()
        self._dedup_max_size = 10000
        
        # 统计
        self._stats_lock = threading.Lock()
        self._topic_stats: Dict[str, int] = {}
    
    async def start(self):
        """初始化并连接 Kafka"""
        try:
            import aiokafka
            
            self._producer = aiokafka.AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers.split(','),
                acks=self.config.producer_acks,
                linger_ms=self.config.producer_linger_ms,
                batch_size=self.config.producer_batch_size,
                max_request_size=10 * 1024 * 1024,  # 10MB
                compression_type='gzip',            # 压缩节省带宽
                retries=self.config.producer_max_retries,
                retry_backoff_ms=self.config.producer_retry_backoff_ms,
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
                if self.config.serializer == 'json' else None,
            )
            
            await self._producer.start()
            self._connected = True
            self._using_fallback = False
            
            logger.info(
                f"[KafkaProducer] Connected to {self.config.bootstrap_servers} "
                f"(acks={self.config.producer_acks}, "
                f"linger={self.config.producer_limiter_ms}ms)"
            )
            
        except ImportError:
            logger.warning("[KafkaProducer] aiokafka not installed, using memory fallback")
            self._using_fallback = True
        except Exception as e:
            logger.error(f"[KafkaProducer] Connection failed: {e}, using fallback")
            self._using_fallback = True
    
    async def stop(self):
        """优雅关闭"""
        if self._producer:
            try:
                await self._producer.flush()   # 刷缓冲区
                await self._producer.stop()
            except Exception as e:
                logger.error(f"[KafkaProducer] Stop error: {e}")
            finally:
                self._producer = None
                self._connected = False
        
        logger.info(f"[KafkaProducer] Stopped (sent={self._sent_count}, errors={self._error_count})")
    
    async def send(self, message: Message, timeout: float = 10.0) -> bool:
        """
        发送单条消息 (自动选择 Kafka 或降级队列)
        
        Args:
            message: 消息对象
            timeout: 超时时间
            
        Returns:
            是否发送成功
        """
        # 幂等检查
        if message.message_id in self._dedup_cache:
            logger.debug(f"[KafkaProducer] Duplicate message skipped: {message.message_id}")
            return True
        
        # 维护去重缓存大小
        if len(self._dedup_cache) > self._dedup_max_size:
            self._dedup_cache.clear()
        self._dedup_cache.add(message.message_id)
        
        # 选择发送路径
        if self._using_fallback or not self._connected:
            return await self._send_to_fallback(message)
        else:
            return await self._send_to_kafka(message, timeout)
    
    async def _send_to_kafka(self, message: Message, timeout: float) -> bool:
        """实际发送到 Kafka"""
        try:
            if not self._producer:
                raise RuntimeError("Producer not initialized")
            
            await self._producer.send_and_wait(
                topic=message.topic.value,
                value=message.to_dict(),
                key=message.key.encode('utf-8') if isinstance(message.key, str) else message.key,
                headers=[
                    (k, v.encode('utf-8')) for k, v in message.headers.items()
                ],
            )
            
            with self._stats_lock:
                self._sent_count += 1
                self._topic_stats[message.topic.value] = \
                    self._topic_stats.get(message.topic.value, 0) + 1
            
            return True
            
        except Exception as e:
            self._error_count += 1
            logger.error(f"[KafkaProducer] Send failed: {e}")
            
            # 连续错误过多，切换到降级模式
            if self._error_count > 10 and self.config.fallback_enabled:
                logger.warning("[KafkaProducer] Too many errors, switching to fallback mode")
                self._using_fallback = True
                return await self._send_to_fallback(message)
            
            raise
    
    async def _send_to_fallback(self, message: Message) -> bool:
        """降级到内存队列"""
        success = await self._fallback.produce(message.topic, message)
        
        with self._stats_lock:
            if success:
                self._sent_count += 1
        
        return success
    
    async def send_batch(
        self, 
        messages: List[Message], 
        timeout: float = 30.0
    ) -> Tuple[int, int]:
        """
        批量发送消息
        
        Returns:
            (成功数量, 失败数量)
        """
        successes = 0
        failures = 0
        
        # 按 Topic 分组
        by_topic: Dict[KafkaTopic, List[Message]] = {}
        for msg in messages:
            by_topic.setdefault(msg.topic, []).append(msg)
        
        # 并发发送各 Topic
        tasks = []
        for topic, msgs in by_topic.items():
            for msg in msgs:
                tasks.append(self.send(msg, timeout))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for r in results:
            if r is True:
                successes += 1
            else:
                failures += 1
        
        return successes, failures
    
    def get_stats(self) -> Dict[str, Any]:
        """获取生产者统计"""
        return {
            'connected': self._connected,
            'using_fallback': self._using_fallback,
            'sent_total': self._sent_count,
            'errors': self._error_count,
            'topic_breakdown': dict(self._topic_stats),
            'fallback_stats': self._fallback.get_stats(),
        }


# ==================== Kafka Consumer ====================

class KafkaConsumerService:
    """
    Kafka 消费者服务 — Consumer Group 并行消费
    
    特性:
      - 多 Topic 订阅
      - 自动提交 offset (每批次处理后)
      - 批量消费 + 批量 DB 写入
      - 错误处理 + DLQ 重试
      - graceful shutdown 支持
      - 指标监控 (lag, throughput, error_rate)
    """
    
    def __init__(
        self, 
        config: Optional[KafkaConfig] = None,
        producer: Optional[KafkaProducerService] = None
    ):
        self.config = config or KafkaConfig()
        self.producer = producer  # 用于发送 DLQ 消息
        self._consumer = None
        self._consumers: Dict[KafkaTopic, 'aiokafka.AIOKafkaConsumer'] = {}
        self._handlers: Dict[KafkaTopic, List[Callable]] = {}
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._fallback = MemoryMessageQueue()
        
        # 统计
        self._consumed_count = 0
        self._processed_count = 0
        self._error_count = 0
        self._dlq_count = 0
        
        # Shutdown signal
        self._shutdown_event = asyncio.Event()
    
    def register_handler(
        self, 
        topic: KafkaTopic, 
        handler: Callable[[Message], Awaitable[ConsumerResult]]
    ):
        """
        注册消息处理器
        
        Args:
            topic: 订阅的 Topic
            handler: 异步处理函数 (返回 ConsumerResult)
        """
        self._handlers.setdefault(topic, []).append(handler)
        logger.info(f"[KafkaConsumer] Handler registered for {topic.value}")
    
    async def start(self, topics: Optional[List[KafkaTopic]] = None):
        """
        启动消费者 (订阅指定 Topics)
        
        Args:
            topics: 要订阅的 Topic 列表 (None = 全部已注册 handler 的 Topic)
        """
        target_topics = topics or list(self._handlers.keys())
        
        try:
            import aiokafka
            
            # 创建统一 Consumer (Consumer Group)
            self._consumer = aiokafka.AIOKafkaConsumer(
                *[t.value for t in target_topics],
                bootstrap_servers=self.config.bootstrap_servers.split(','),
                group_id=self.config.group_id,
                auto_offset_reset=self.config.auto_offset_reset,
                enable_auto_commit=False,  # 手动提交
                max_poll_records=self.config.max_poll_records,
                session_timeout_ms=self.config.session_timeout_ms,
                max_poll_interval_ms=self.config.max_poll_interval_ms,
                value_deserializer=lambda v: json.loads(v.decode('utf-8'))
                if self.config.serializer == 'json' else None,
            )
            
            await self._consumer.start()
            self._running = True
            
            # 启动消费循环
            self._tasks.append(asyncio.create_task(self._consume_loop()))
            
            logger.info(
                f"[KafkaConsumer] Started consuming {len(target_topics)} topics "
                f"(group={self.config.group_id})"
            )
            
        except ImportError:
            logger.warning("[KafkaConsumer] aiokafka not installed, using memory mode")
            self._running = True
            self._tasks.append(asyncio.create_task(self._fallback_consume_loop(target_topics)))
            
        except Exception as e:
            logger.error(f"[KafkaConsumer] Start failed: {e}")
            raise
    
    async def stop(self):
        """优雅关闭消费者"""
        self._shutdown_event.set()
        self._running = False
        
        # 等待消费任务完成
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        if self._consumer:
            try:
                await self._consumer.commit()  # 最后一次 commit
                await self._consumer.stop()
            except Exception as e:
                logger.error(f"[KafkaConsumer] Stop error: {e}")
        
        logger.info(
            f"[KafkaConsumer] Stopped "
            f"(consumed={self._consumed_count}, "
            f"processed={self._processed_count}, "
            f"errors={self._error_count}, "
            f"dlq={self._dlq_count})"
        )
    
    async def _consume_loop(self):
        """Kafka 主消费循环"""
        logger.info("[KafkaConsumer] Consume loop started")
        
        try:
            while self._running and not self._shutdown_event.is_set():
                # 批量拉取消息
                msg_pack = await self._consumer.getmany(timeout_ms=1000)
                
                if not msg_pack:
                    continue
                
                total_processed = 0
                
                for tp, messages in msg_pack.items():
                    batch = []
                    
                    for raw_msg in messages:
                        try:
                            # 反序列化
                            msg = Message.from_dict(raw_msg.value)
                            batch.append((raw_msg, msg))
                            self._consumed_count += 1
                            
                        except Exception as e:
                            logger.error(f"[KafkaConsumer] Deserialize error: {e}")
                            self._error_count += 1
                    
                    # 批量处理
                    if batch:
                        processed = await self._process_batch(batch)
                        total_processed += processed
                
                # 提交 offset (每批处理后手动提交)
                if total_processed > 0:
                    await self._consumer.commit()
                    self._processed_count += total_processed
                    
        except asyncio.CancelledError:
            logger.info("[KafkaConsumer] Consume loop cancelled")
        except Exception as e:
            logger.error(f"[KafkaConsumer] Consume loop error: {e}", exc_info=True)
    
    async def _process_batch(self, batch: list) -> int:
        """
        处理一批消息
        
        Returns:
            成功处理的数量
        """
        success_count = 0
        
        for raw_msg, msg in batch:
            result = await self._handle_message(msg)
            
            if result.success:
                success_count += 1
            else:
                # 处理失败 → DLQ 或重试
                await self._handle_failure(msg, result.error)
                self._error_count += 1
        
        return success_count
    
    async def _handle_message(self, message: Message) -> ConsumerResult:
        """调用注册的 handlers 处理消息"""
        handlers = self._handlers.get(message.topic, [])
        
        if not handlers:
            logger.warning(f"[KafkaConsumer] No handler for {message.topic.value}")
            return ConsumerResult(success=True, message_id=message.message_id)
        
        last_error = None
        start_time = time.time()
        
        for handler in handlers:
            try:
                result = await handler(message)
                
                if not result.success:
                    last_error = result.error or "Handler returned failure"
                    continue
                
                result.processing_time_ms = (time.time() - start_time) * 1000
                return result
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"[KafkaConsumer] Handler error: {e}", exc_info=True)
        
        return ConsumerResult(
            success=False, 
            message_id=message.message_id, 
            error=last_error
        )
    
    async def _handle_failure(self, message: Message, error: Optional[str]):
        """处理失败的消息 (DLQ + 重试)"""
        if message.retry_count >= self.config.max_retries:
            # 超过最大重试次数 → 死信队列
            if self.config.dlq_enabled and self.producer:
                dlq_msg = Message(
                    topic=KafkaTopic(f"{message.topic.value}{self.config.dlq_topic_suffix}"),
                    key=message.key,
                    value={
                        **message.value,
                        '_original_error': error,
                        '_original_message_id': message.message_id,
                        '_retry_count': message.retry_count,
                        '_failed_at': datetime.utcnow().isoformat(),
                    },
                    source="dlq-router",
                )
                await self.producer.send(dlq_msg)
                self._dlq_count += 1
                
                logger.warning(
                    f"[KafkaConsumer] Message sent to DLQ: {message.message_id} "
                    f"(retries={message.retry_count}, error={error})"
                )
            else:
                logger.error(
                    f"[KafkaConsumer] Message dropped (no DLQ): "
                    f"{message.message_id} - {error}"
                )
        else:
            # 延迟重试
            delay = min(
                self.config.retry_backoff_base ** (message.retry_count + 1),
                self.config.retry_backoff_max
            )
            
            retry_msg = Message(
                topic=message.topic,
                key=message.key,
                value=message.value,
                retry_count=message.retry_count + 1,
                source="retry",
            )
            
            # 延迟后重新发送 (简化: 直接放回队列头)
            await asyncio.sleep(delay)
            if self.producer:
                await self.producer.send(retry_msg)
    
    async def _fallback_consume_loop(self, topics: List[KafkaTopic]):
        """降级: 从内存队列消费"""
        logger.info("[KafkaConsumer] Using memory fallback consumer")
        
        while self._running and not self._shutdown_event.is_set():
            for topic in topics:
                messages = await self._fallback.consume_batch(topic, max_messages=50)
                
                for msg in messages:
                    await self._handle_message(msg)
                    self._consumed_count += 1
            
            await asyncio.sleep(0.1)  # 避免 CPU 空转
    
    def get_stats(self) -> Dict[str, Any]:
        """获取消费者统计"""
        return {
            'running': self._running,
            'consumed_total': self._consumed_count,
            'processed_total': self._processed_count,
            'errors': self._error_count,
            'dlq_messages': self._dlq_count,
            'registered_handlers': {t.value: len(h) for t, h in self._handlers.items()},
            'active_tasks': len([t for t in self._tasks if not t.done()]),
        }


# ==================== 消息总线 (Facade) ====================

class EventBus:
    """
    消息总线门面 — 统一的生产/消费接口
    
    使用方式:
        bus = EventBus(config)
        await bus.initialize()
        
        # 发送消息
        await bus.publish(KafkaTopic.AGV_STATUS_UPDATE, key='agv_001', value={...})
        
        # 注册消费者
        @bus.subscribe(KafkaTopic.AGV_STATUS_UPDATE)
        async def handle_agv_status(msg: Message) -> ConsumerResult:
            ...
        
        # 启动消费
        await bus.start_consuming()
        
        # 关闭
        await bus.shutdown()
    """
    
    _instance: Optional['EventBus'] = None
    
    def __init__(self, config: Optional[KafkaConfig] = None):
        self.config = config or KafkaConfig()
        self._producer: Optional[KafkaProducerService] = None
        self._consumer: Optional[KafkaConsumerService] = None
        self._file_fallback: Optional[KafkaFileFallback] = None
        self._initialized = False
    
    @classmethod
    def get_instance(cls, config: Optional[KafkaConfig] = None) -> 'EventBus':
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance
    
    async def initialize(self):
        """初始化生产者和消费者（含三级降级链）"""
        if self._initialized:
            return
        
        # 初始化三级降级: File → Memory → Kafka
        self._file_fallback = KafkaFileFallback()
        
        # 创建带文件溢写的内存队列
        memory_queue = MemoryMessageQueue(
            max_size_per_topic=self.config.fallback_queue_size,
            file_fallback=self._file_fallback,
            spill_threshold=0.8,
        )

        self._producer = KafkaProducerService(self.config)
        # 将降级队列注入到 producer (用于内部降级)
        if hasattr(self._producer, '_fallback'):
            self._producer._fallback = memory_queue
        self._consumer = KafkaConsumerService(self.config, self._producer)
        
        await self._producer.start()
        self._initialized = True
        
        logger.info("[EventBus] Initialized with 3-tier fallback: Kafka → Memory → File")
    
    async def shutdown(self):
        """关闭所有组件"""
        if self._consumer:
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()
        
        self._initialized = False
        logger.info("[EventBus] Shutdown complete")
    
    async def publish(
        self, 
        topic: KafkaTopic, 
        key: str, 
        value: Dict[str, Any],
        **kwargs
    ) -> bool:
        """
        发布消息 (便捷方法)
        
        Args:
            topic: 目标 Topic
            key: 分区键
            value: 消息体字典
            **kwargs: 额外的 Message 参数
            
        Returns:
            是否发布成功
        """
        if not self._initialized:
            await self.initialize()
        
        message = Message(topic=topic, key=key, value=value, **kwargs)
        return await self._producer.send(message)
    
    async def publish_batch(
        self, 
        messages: List[Tuple[KafkaTopic, str, Dict[str, Any]]]
    ) -> Tuple[int, int]:
        """
        批量发布消息
        
        Args:
            messages: [(topic, key, value), ...] 列表
            
        Returns:
            (成功数, 失败数)
        """
        msg_objects = [
            Message(topic=t, key=k, value=v) 
            for t, k, v in messages
        ]
        return await self._producer.send_batch(msg_objects)
    
    def subscribe(
        self, 
        topic: KafkaTopic
    ):
        """
        装饰器: 注册消息处理器
        
        Usage:
            @bus.subscribe(KafkaTopic.AGV_STATUS_UPDATE)
            async def handler(msg: Message) -> ConsumerResult:
                ...
        """
        def decorator(func: Callable[[Message], Awaitable[ConsumerResult]]):
            if self._consumer:
                self._consumer.register_handler(topic, func)
            else:
                # 延迟注册
                if not hasattr(self, '_pending_handlers'):
                    self._pending_handlers = []
                self._pending_handlers.append((topic, func))
            return func
        return decorator
    
    async def start_consuming(self, topics: Optional[List[KafkaTopic]] = None):
        """启动消费循环"""
        if not self._consumer:
            raise RuntimeError("EventBus not initialized")
        
        # 注册延迟的 handlers
        if hasattr(self, '_pending_handlers'):
            for topic, func in self._pending_handlers:
                self._consumer.register_handler(topic, func)
            delattr(self, '_pending_handlers')
        
        await self._consumer.start(topics)
    
    def get_producer(self) -> KafkaProducerService:
        """获取生产者实例 (用于高级用法)"""
        return self._producer
    
    def get_consumer(self) -> KafkaConsumerService:
        """获取消费者实例 (用于高级用法)"""
        return self._consumer
    
    def get_stats(self) -> Dict[str, Any]:
        """获取完整统计信息"""
        return {
            'initialized': self._initialized,
            'producer': self._producer.get_stats() if self._producer else None,
            'consumer': self._consumer.get_stats() if self._consumer else None,
        }
    
    # ==================== 便捷方法: AGV 状态更新 ====================
    
    async def publish_agv_status(
        self, 
        agv_id: str, 
        x: float, y: float, 
        battery: float,
        speed: float,
        status: str,
        task_id: Optional[str] = None,
    ):
        """发布 AGV 状态更新"""
        return await self.publish(
            KafkaTopic.AGV_STATUS_UPDATE,
            key=agv_id,
            value={
                'agv_id': agv_id,
                'position': {'x': x, 'y': y},
                'battery': battery,
                'speed': speed,
                'status': status,
                'task_id': task_id,
                'timestamp': datetime.utcnow().isoformat(),
            },
            source='agv-telemetry',
        )
    
    # ==================== 便捷方法: 新任务 ====================
    
    async def publish_task_scheduled(
        self,
        task_id: str,
        task_type: str,
        priority: int,
        pickup_node: str,
        dropoff_node: str,
        **extra_fields
    ):
        """发布新任务事件"""
        return await self.publish(
            KafkaTopic.TASK_SCHEDULE_NEW,
            key=task_id,
            value={
                'task_id': task_id,
                'task_type': task_type,
                'priority': priority,
                'pickup_node': pickup_node,
                'dropoff_node': dropoff_node,
                'created_at': datetime.utcnow().isoformat(),
                **extra_fields,
            },
            source='task-service',
        )
    
    # ==================== 便捷方法: 告警 ====================
    
    async def publish_alert(
        self,
        alert_type: str,
        severity: str,  # info/warning/error/critical
        message: str,
        source: str = 'unknown',
        metadata: Optional[Dict] = None,
    ):
        """发布系统告警"""
        return await self.publish(
            KafkaTopic.SYSTEM_ALERT,
            key=f"{alert_type}:{int(time.time())}",
            value={
                'alert_type': alert_type,
                'severity': severity,
                'message': message,
                'source': source,
                'metadata': metadata or {},
                'timestamp': datetime.utcnow().isoformat(),
            },
            source='alert-system',
        )


# ==================== FastAPI 生命周期集成 ====================

def lifespan_kafka(app):
    """FastAPI Lifespan context manager — 自动管理 Kafka 生命周期"""
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def _lifespan(app):
        # 启动
        bus = EventBus.get_instance()
        await bus.initialize()
        app.state.event_bus = bus
        
        logger.info("📨 [Lifespan] Kafka EventBus initialized")
        
        yield
        
        # 关闭
        await bus.shutdown()
        logger.info("📨 [Lifespan] Kafka EventBus shutdown")
    
    return _lifespan(app)


# ==================== 便捷导出 ====================

__all__ = [
    'KafkaTopic',
    'Message',
    'ConsumerResult',
    'KafkaConfig',
    'KafkaProducerService',
    'KafkaConsumerService',
    'MemoryMessageQueue',
    'EventBus',
    'lifespan_kafka',
]

# ==================== 快速验证脚本 ====================

if __name__ == '__main__':
    import asyncio
    
    async def test():
        print("=" * 60)
        print("🧪 Kafka Event Bus Test Suite")
        print("=" * 60)
        
        # 测试 1: 内存队列降级
        print("\n📦 Test 1: Memory Fallback Queue")
        mq = MemoryMessageQueue(max_size_per_topic=100)
        
        msg = Message(
            topic=KafkaTopic.AGV_STATUS_UPDATE,
            key='agv_001',
            value={'x': 10.5, 'y': 20.3, 'battery': 85.0}
        )
        
        assert await mq.produce(msg.topic, msg) == True
        print(f"  ✅ Message produced: {msg.message_id[:12]}...")
        
        consumed = await mq.consume_batch(msg.topic, max_messages=10, timeout=0.5)
        assert len(consumed) == 1
        assert consumed[0].key == 'agv_001'
        print(f"  ✅ Message consumed: key={consumed[0].key}")
        
        stats = mq.get_stats()
        assert stats['total_messages'] == 1
        print(f"  ✅ Stats: {stats['total_messages']} messages tracked")
        
        # 测试 2: Event Bus
        print("\n🔌 Test 2: EventBus (Memory Mode)")
        bus = EventBus()
        await bus.initialize()
        
        # 发布
        ok = await bus.publish_agv_status('agv_002', 15.0, 25.0, 90.0, 1.5, 'moving')
        assert ok == True
        print(f"  ✅ AGV status published via convenience method")
        
        ok = await bus.publish_alert('low_battery', 'warning', 'AGV-003 battery at 10%', source='monitor')
        assert ok == True
        print(f"  ✅ Alert published via convenience method")
        
        # 统计
        stats = bus.get_stats()
        print(f"  ✅ Bus stats: sent={stats['producer']['sent_total']}")
        
        # 关闭
        await bus.shutdown()
        
        print("\n" + "=" * 60)
        print("🎉 All Kafka Event Bus tests PASSED!")
        print("=" * 60)
    
    asyncio.run(test())
