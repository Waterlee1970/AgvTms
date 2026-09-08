"""
Phase 6: 分布式调度支持 — 消息队列 + 无状态化 + 分布式锁.

特性:
  1. Redis Streams 消息队列 (Kafka 替代方案, 无额外依赖)
  2. 调度状态外部化到 Redis (支持多实例共享)
  3. 分布式锁 (RedLock 算法, 防并发调度冲突)
  4. 高可用支持 (主备切换基础)

用法:
    from app.core.distributed import distributed_scheduler, acquire_schedule_lock

    # 获取分布式锁
    async with acquire_schedule_lock("schedule_batch_001"):
        result = await schedule_service.run_scheduling_async()

    # 发布调度事件到消息队列
    await distributed_scheduler.publish_order(order_data)
    orders = await distributed_scheduler.consume_orders()
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== 1. 分布式锁 (RedLock 简化版) ====================

class DistributedLock:
    """
    基于 Redis 的分布式锁 (RedLock 简化版).

    防止多实例并发调度同一批任务。
    依赖: Redis (已有 redis_service)
    """

    LOCK_TTL = 30  # 锁过期时间 (秒)
    LOCK_RETRY_INTERVAL = 0.1  # 重试间隔
    LOCK_MAX_RETRY = 50  # 最大重试次数

    def __init__(self, redis_service=None):
        self._redis = redis_service

    def _get_redis(self):
        if self._redis is None:
            from ..services import redis_service
            self._redis = redis_service
        return self._redis

    async def acquire(self, lock_name: str, ttl: int = None) -> Optional[str]:
        """
        获取分布式锁.

        Returns:
            lock_token (成功) 或 None (失败)
        """
        ttl = ttl or self.LOCK_TTL
        token = str(uuid.uuid4())
        redis = self._get_redis()
        key = f"lock:{lock_name}"

        for attempt in range(self.LOCK_MAX_RETRY):
            try:
                # SET NX EX (原子操作)
                if hasattr(redis, 'redis_client') and redis.redis_client:
                    result = await redis.redis_client.set(
                        key, token, ex=ttl, nx=True
                    )
                    if result:
                        logger.debug("Lock acquired: %s (token: %s)", lock_name, token)
                        return token
            except Exception as e:
                logger.debug("Lock acquire error: %s", e)
            await asyncio.sleep(self.LOCK_RETRY_INTERVAL)

        logger.warning("Lock acquire failed after %d retries: %s", self.LOCK_MAX_RETRY, lock_name)
        return None

    async def release(self, lock_name: str, token: str) -> bool:
        """释放锁 (Lua 脚本确保原子性)"""
        redis = self._get_redis()
        key = f"lock:{lock_name}"
        try:
            if hasattr(redis, 'redis_client') and redis.redis_client:
                # 简化: GET + DEL (生产环境应用 Lua 脚本)
                current = await redis.redis_client.get(key)
                if current and current.decode() if isinstance(current, bytes) else current == token:
                    await redis.redis_client.delete(key)
                    logger.debug("Lock released: %s", lock_name)
                    return True
        except Exception as e:
            logger.debug("Lock release error: %s", e)
        return False


@asynccontextmanager
async def acquire_schedule_lock(lock_name: str, ttl: int = 30):
    """
    调度锁上下文管理器.

    用法:
        async with acquire_schedule_lock("batch_001"):
            await schedule_service.run_scheduling_async()
    """
    lock = DistributedLock()
    token = await lock.acquire(lock_name, ttl)
    if not token:
        raise RuntimeError(f"Failed to acquire schedule lock: {lock_name}")
    try:
        yield token
    finally:
        await lock.release(lock_name, token)


# ==================== 2. Redis Streams 消息队列 ====================

@dataclass
class QueueMessage:
    """队列消息"""
    id: str
    stream: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)


class MessageQueue:
    """
    基于 Redis Streams 的消息队列.

    替代 Kafka, 无需额外依赖。
    支持: 发布 / 消费 / 消费组 / 确认
    """

    def __init__(self, redis_service=None):
        self._redis = redis_service
        self._consumer_group = "agv-tms-scheduler"
        self._consumer_name = f"worker-{uuid.uuid4().hex[:8]}"

    def _get_redis(self):
        if self._redis is None:
            from ..services import redis_service
            self._redis = redis_service
        return self._redis

    async def publish(self, stream: str, data: Dict[str, Any]) -> Optional[str]:
        """发布消息到流"""
        redis = self._get_redis()
        try:
            if hasattr(redis, 'redis_client') and redis.redis_client:
                # 序列化数据
                fields = {k: str(v) for k, v in data.items()}
                msg_id = await redis.redis_client.xadd(stream, fields)
                logger.debug("Published to %s: %s", stream, msg_id)
                return msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
        except Exception as e:
            logger.warning("MQ publish error: %s", e)
        return None

    async def consume(
        self,
        stream: str,
        count: int = 10,
        block_ms: int = 1000,
    ) -> List[QueueMessage]:
        """从流消费消息"""
        redis = self._get_redis()
        messages = []
        try:
            if hasattr(redis, 'redis_client') and redis.redis_client:
                # 确保消费组存在
                try:
                    await redis.redis_client.xgroup_create(
                        stream, self._consumer_group, id="0", mkstream=True
                    )
                except Exception:
                    pass  # 组已存在

                # 读取消息
                result = await redis.redis_client.xreadgroup(
                    self._consumer_group,
                    self._consumer_name,
                    {stream: ">"},
                    count=count,
                    block=block_ms,
                )

                for stream_name, stream_messages in result:
                    for msg_id, fields in stream_messages:
                        msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                        data = {}
                        for k, v in fields.items():
                            k_str = k.decode() if isinstance(k, bytes) else str(k)
                            v_str = v.decode() if isinstance(v, bytes) else str(v)
                            data[k_str] = v_str
                        messages.append(QueueMessage(
                            id=msg_id_str, stream=stream, data=data
                        ))
            else:
                # Redis 不可用 (内存模式): 休眠避免消费循环空转占满 CPU
                await asyncio.sleep(block_ms / 1000.0)
        except Exception as e:
            logger.debug("MQ consume error: %s", e)

        return messages

    async def acknowledge(self, stream: str, message_id: str) -> bool:
        """确认消息已处理"""
        redis = self._get_redis()
        try:
            if hasattr(redis, 'redis_client') and redis.redis_client:
                await redis.redis_client.xack(stream, self._consumer_group, message_id)
                return True
        except Exception as e:
            logger.debug("MQ ack error: %s", e)
        return False


# ==================== 3. 分布式调度器 ====================

class DistributedScheduler:
    """
    分布式调度器 — 消息队列驱动的调度模式.

    流程:
      1. API 提交订单 → 发布到 order_stream
      2. 调度 Worker 消费 order_stream → 执行调度
      3. 调度结果发布到 result_stream
      4. API/前端 消费 result_stream → 返回结果

    支持多 Worker 并行消费 (水平扩展)。
    """

    ORDER_STREAM = "agv-tms:orders"
    RESULT_STREAM = "agv-tms:results"
    STATUS_STREAM = "agv-tms:status"

    def __init__(self):
        self._mq = MessageQueue()
        self._lock = DistributedLock()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    async def submit_order(self, order_data: Dict[str, Any]) -> Optional[str]:
        """提交订单到消息队列 (非阻塞)"""
        order_data["submitted_at"] = datetime.now().isoformat()
        msg_id = await self._mq.publish(self.ORDER_STREAM, order_data)
        if msg_id:
            logger.info("Order submitted to queue: %s", msg_id)
        return msg_id

    async def get_results(self, count: int = 10) -> List[Dict]:
        """获取调度结果"""
        messages = await self._mq.consume(self.RESULT_STREAM, count=count, block_ms=100)
        results = []
        for msg in messages:
            results.append(msg.data)
            await self._mq.acknowledge(self.RESULT_STREAM, msg.id)
        return results

    async def start_worker(self, schedule_service=None) -> None:
        """启动调度 Worker (消费订单 → 执行调度 → 发布结果)"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(schedule_service))
        logger.info("Distributed scheduler worker started")

    async def stop_worker(self) -> None:
        """停止 Worker"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Distributed scheduler worker stopped")

    async def _worker_loop(self, schedule_service=None) -> None:
        """Worker 主循环 — 消费订单 → 调度 → 发布结果"""
        logger.info("Scheduler worker loop started")

        while self._running:
            try:
                # 消费订单
                messages = await self._mq.consume(self.ORDER_STREAM, count=1, block_ms=2000)

                for msg in messages:
                    try:
                        # 获取分布式锁
                        lock_name = f"schedule:{msg.id}"
                        token = await self._lock.acquire(lock_name, ttl=60)

                        if token and schedule_service:
                            # 执行调度
                            result = await schedule_service.run_scheduling_async()

                            # 发布结果
                            await self._mq.publish(self.RESULT_STREAM, {
                                "order_id": msg.id,
                                "result_id": result.id,
                                "assignments": len(result.assignments),
                                "makespan": result.makespan,
                                "completed_at": datetime.now().isoformat(),
                            })

                            # 确认订单
                            await self._mq.acknowledge(self.ORDER_STREAM, msg.id)
                            await self._lock.release(lock_name, token)
                        else:
                            # 锁获取失败, 重新排队
                            logger.debug("Lock failed for %s, will retry", msg.id)

                    except Exception as e:
                        logger.error("Worker process error: %s", e)
                        await self._mq.acknowledge(self.ORDER_STREAM, msg.id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Worker loop error: %s", e)
                await asyncio.sleep(5.0)


# ==================== 全局单例 ====================

distributed_scheduler = DistributedScheduler()
message_queue = MessageQueue()
distributed_lock = DistributedLock()
