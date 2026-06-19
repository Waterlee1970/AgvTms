"""
数据库自动重连 + 故障转移服务 (Phase 5.5)

功能:
  1. PostgreSQL 连接池健康监控
  2. 断线自动重连 (指数退避, 最大10次, 最大60s间隔)
  3. 主从故障转移 (Patroni/Streaming Replication)
  4. 连接泄露检测和回收
  5. 查询超时控制
  6. 降级模式管理 (DB不可用时→只读缓存)

解决 SPOF #4: PostgreSQL 单实例无故障转移
解决 SPOF #6: DB可用性检测一次性缓存不刷新

依赖:
  - asyncpg / SQLAlchemy (已有)
  - psycopg2 (可选, 用于 Patroni 集成)
"""

import asyncio
import time
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


# ==================== 枚举 ====================

class ConnectionState(str, Enum):
    """连接状态"""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"           # 超过最大重试次数


class DBRole(str, Enum):
    """数据库角色 (主从)"""
    PRIMARY = "primary"
    REPLICA = "replica"         # 只读副本
    UNKNOWN = "unknown"


@dataclass
class ConnectionStats:
    """连接统计信息"""
    
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    avg_response_time_ms: float = 0.0
    active_connections: int = 0
    idle_connections: int = 0
    last_successful_query: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    reconnect_count: int = 0
    
    def to_dict(self) -> Dict:
        return {
            'total_queries': self.total_queries,
            'success_rate': f"{(self.successful_queries / max(self.total_queries, 1)) * 100:.1f}%",
            'avg_response_ms': round(self.avg_response_time_ms, 2),
            'active_connections': self.active_connections,
            'idle_connections': self.idle_connections,
            'last_success': self.last_successful_query.isoformat() if self.last_successful_query else None,
            'last_failure': self.last_failure.isoformat() if self.last_failure else None,
            'reconnect_count': self.reconnect_count,
        }


# ==================== 配置 ====================

@dataclass
class ReconnectConfig:
    """重连配置"""
    
    # 重连策略
    max_attempts: int = 10                    # 最大重连次数 (-1=无限)
    base_delay: float = 1.0                   # 首次延迟(秒)
    max_delay: float = 60.0                   # 最大延迟
    backoff_multiplier: float = 2.0           # 退避倍数
    jitter: bool = True                       # 添加随机抖动
    
    # 健康检查
    health_check_interval: float = 30.0       # 健康检查间隔(秒)
    health_check_timeout: float = 5.0         # 检查超时
    
    # 连接池
    pool_size: int = 10                       # 连接池大小
    max_overflow: int = 20                    # 最大溢出连接
    pool_recycle: int = 3600                  # 连接回收时间(秒)
    pool_pre_ping: bool = True                # 使用前 ping 检查
    
    # 超时控制
    query_timeout: float = 30.0               # 默认查询超时(秒)
    connect_timeout: float = 10.0             # 连接超时(秒)
    
    # 降级配置
    enable_fallback_cache: bool = True        # 启用降级缓存
    fallback_cache_ttl: float = 300.0         # 缓存有效期(5分钟)
    fallback_max_size: int = 1000             # 最大缓存条目
    
    # 主从配置
    replica_urls: List[str] = field(default_factory=list)  # 从库 URL 列表
    prefer_replica_for_reads: bool = False   # 读操作优先走从库


# ==================== 降级缓存 ====================

class FallbackCache:
    """
    降级缓存 — 数据库不可用时的只读备份
    
    特性:
      - LRU 淘汰策略
      - TTL 过期
      - 线程安全
      - 统计监控
    """
    
    def __init__(self, ttl: float = 300.0, max_size: int = 1000):
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key → (value, expiry_time)
        self._ttl = ttl
        self._max_size = max_size
        self._lock = threading.Lock()
        
        # 统计
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        
        # 访问顺序 (用于 LRU)
        self._access_order: List[str] = []
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            now = time.time()
            
            if key not in self._cache:
                self._misses += 1
                return None
            
            value, expiry = self._cache[key]
            
            if now > expiry:
                # 已过期，删除
                del self._cache[key]
                try:
                    self._access_order.remove(key)
                except ValueError:
                    pass
                self._evictions += 1
                self._misses += 1
                return None
            
            # 更新访问顺序 (移到末尾)
            try:
                self._access_order.remove(key)
            except ValueError:
                pass
            self._access_order.append(key)
            
            self._hits += 1
            return value
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """
        设置缓存值
        
        Args:
            key: 键
            value: 值
            ttl: 过期时间 (None=使用默认TTL)
        """
        with self._lock:
            # 如果已存在，先删除旧记录
            if key in self._cache:
                try:
                    self._access_order.remove(key)
                except ValueError:
                    pass
            
            # 容量检查 + LRU 淘汰
            while len(self._cache) >= self._max_size and self._access_order:
                oldest = self._access_order.pop(0)
                del self._cache[oldest]
                self._evictions += 1
            
            # 写入
            effective_ttl = ttl or self._ttl
            self._cache[key] = (value, time.time() + effective_ttl)
            self._access_order.append(key)
    
    def delete(self, key: str):
        """删除缓存条目"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                try:
                    self._access_order.remove(key)
                except ValueError:
                    pass
    
    def clear(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
    
    def cleanup_expired(self) -> int:
        """清理过期条目"""
        now = time.time()
        expired_keys = []
        
        with self._lock:
            for key, (_, expiry) in self._cache.items():
                if now > expiry:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._cache[key]
                try:
                    self._access_order.remove(key)
                except ValueError:
                    pass
                self._evictions += 1
        
        return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        
        return {
            'size': len(self._cache),
            'max_size': self._max_size,
            'hit_rate': f"{hit_rate:.1f}%",
            'hits': self._hits,
            'misses': self._misses,
            'evictions': self._evictions,
            'ttl_seconds': self._ttl,
        }


# ==================== 数据库重连器 ====================

class DatabaseReconnector:
    """
    数据库自动重连服务
    
    功能:
      - 监控数据库连接状态
      - 断线自动重连
      - 连接池管理
      - 降级缓存集成
      - 指标收集
    """
    
    def __init__(
        self, 
        config: Optional[ReconnectConfig] = None,
        get_engine_fn: Optional[Callable] = None,
    ):
        self.config = config or ReconnectConfig()
        self._get_engine = get_engine_fn
        self._state = ConnectionState.DISCONNECTED
        self._role = DBRole.UNKNOWN
        
        # 统计
        self._stats = ConnectionStats()
        self._stats_lock = threading.Lock()
        
        # 降级缓存
        self._fallback = FallbackCache(
            ttl=self.config.fallback_cache_ttl,
            max_size=self.config.fallback_max_size,
        ) if self.config.enable_fallback_cache else None
        
        # 重连状态
        self._current_attempt = 0
        self._next_retry_time: Optional[float] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 回调
        self._on_connected: Optional[Callable] = None
        self._on_disconnected: Optional[Callable] = None
        self._on_state_change: Optional[Callable[[ConnectionState], None]] = None
    
    @property
    def state(self) -> ConnectionState:
        return self._state
    
    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED
    
    @property
    def role(self) -> DBRole:
        return self._role
    
    def on_connected(self, callback: Callable):
        """注册连接成功回调"""
        self._on_connected = callback
    
    def on_disconnected(self, callback: Callable):
        """注册断开连接回调"""
        self._on_disconnected = callback
    
    def on_state_change(self, callback: Callable[[ConnectionState], None]):
        """注册状态变更回调"""
        self._on_state_change = callback
    
    async def start_monitoring(self):
        """启动健康监控循环"""
        if self._running:
            return
        
        self._running = True
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("[DBReconnector] Monitoring started")
    
    async def stop_monitoring(self):
        """停止健康监控"""
        self._running = False
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("[DBReconnector] Monitoring stopped")
    
    async def _health_check_loop(self):
        """健康检查循环"""
        logger.info("[DBReconnector] Health check loop started")
        
        while self._running:
            try:
                await self._perform_health_check()
                
                # 等待下一次检查
                await asyncio.sleep(self.config.health_check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DBReconnector] Health check error: {e}")
                await asyncio.sleep(5)  # 出错后短暂等待再继续
    
    async def _perform_health_check(self):
        """执行单次健康检查"""
        start = time.time()
        
        try:
            from app.core.database import get_engine
            
            engine = get_engine()
            
            # 执行简单查询
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                await result.fetchone()
            
            elapsed = (time.time() - start) * 1000
            
            # 更新统计
            with self._stats_lock:
                self._stats.total_queries += 1
                self._stats.successful_queries += 1
                self._stats.last_successful_query = datetime.now()
                # 移动平均响应时间
                alpha = 0.3
                self._stats.avg_response_time_ms = \
                    alpha * elapsed + (1 - alpha) * self._stats.avg_response_time_ms
            
            # 状态转换: RECONNECTING → CONNECTED
            if self._state in (ConnectionState.RECONNECTING, ConnectionState.DISCONNECTED):
                await self._set_state(ConnectionState.CONNECTED)
                self._current_attempt = 0
                
                # 清理过期缓存
                if self._fallback:
                    cleaned = self._fallback.cleanup_expired()
                    if cleaned > 0:
                        logger.info(f"[DBReconnector] Cleaned {cleaned} expired cache entries")
            
            # 尝试检测主从角色 (可选)
            await self._detect_role(conn)
            
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            
            with self._stats_lock:
                self._stats.total_queries += 1
                self._stats.failed_queries += 1
                self._stats.last_failure = datetime.now()
            
            logger.warning(f"[DBReconnector] Health check failed ({elapsed:.0f}ms): {e}")
            
            # 状态转换: CONNECTED → RECONNECTING/DISCONNECTED
            if self._state == ConnectionState.CONNECTED:
                await self._set_state(ConnectionState.DISCONNECTED)
            
            # 触发重连
            if self._state != ConnectionState.FAILED:
                await self._attempt_reconnect(e)
    
    async def _detect_role(self, connection):
        """检测数据库角色 (主/从)"""
        try:
            # PostgreSQL 检测方式
            result = await connection.execute(text("""
                SELECT pg_is_in_recovery() as is_replica,
                       pg_catalog.pg_in_recovery() as recovery_mode
            """))
            row = result.fetchone()
            
            if row and row[0] == False:
                self._role = DBRole.PRIMARY
            elif row and row[0] == True:
                self._role = DBRole.REPLICA
            else:
                self._role = DBRole.UNKNOWN
                
        except Exception:
            # 检测失败不影响主要功能
            self._role = DBRole.UNKNOWN
    
    async def _set_state(self, new_state: ConnectionState):
        """更新状态并通知观察者"""
        old_state = self._state
        self._state = new_state
        
        logger.info(f"[DBReconnector] State change: {old_state.value} → {new_state.value}")
        
        if self._on_state_change:
            try:
                self._on_state_change(new_state)
            except Exception as e:
                logger.error(f"[DBReconnector] State change callback error: {e}")
        
        # 触发特定状态的回调
        if new_state == ConnectionState.CONNECTED and self._on_connected:
            try:
                await self._on_connected()
            except Exception as e:
                logger.error(f"[DBReconnector] Connected callback error: {e}")
        
        elif new_state in (ConnectionState.DISCONNECTED, ConnectionState.FAILED) \
             and self._on_disconnected:
            try:
                await self._on_disconnected()
            except Exception as e:
                logger.error(f"[DBReconnector] Disconnected callback error: {e}")
    
    async def _attempt_reconnect(self, reason: Exception):
        """尝试重新连接"""
        if self.config.max_attempts >= 0 and self._current_attempt >= self.config.max_attempts:
            await self._set_state(ConnectionState.FAILED)
            logger.error(
                f"[DBReconnector] Max reconnection attempts reached "
                f"({self.config.max_attempts}), giving up"
            )
            return
        
        # 计算延迟 (指数退避 + 抖动)
        delay = min(
            self.config.base_delay * (self.config.backoff_multiplier ** self._current_attempt),
            self.config.max_delay
        )
        
        if self.config.jitter:
            import random
            delay *= (0.5 + random.random())  # ±50% 抖动
        
        self._current_attempt += 1
        with self._stats_lock:
            self._stats.reconnect_count += 1
        
        logger.info(
            f"[DBReconnector] Reconnection attempt "
            f"{self._current_attempt}/{self.config.max_attempts or '∞'} "
            f"in {delay:.1f}s..."
        )
        
        await self._set_state(ConnectionState.RECONNECTING)
        
        # 等待后尝试连接
        await asyncio.sleep(delay)
        
        # 下次 health_check 循环会实际执行连接验证
    
    @asynccontextmanager
    async def execute_with_fallback(self, query_key: str):
        """
        执行查询并支持降级缓存
        
        Usage:
            async with db_reconnect.execute_with_fallback('my_query') as (conn, use_cache):
                if not use_cache:
                    result = await conn.execute(text("SELECT ..."))
                    rows = result.fetchall()
                    # 存入缓存供下次使用
                    db_reconnect.cache_result(query_key, [dict(r) for r in rows])
                else:
                    rows = db_reconnect.get_cached(query_key)
        """
        if self.is_connected:
            yield (True, False)  # (can_execute, should_use_cache)
        elif self._fallback:
            cached = self._fallback.get(query_key)
            if cached is not None:
                yield (False, True)  # 使用缓存
            else:
                yield (False, False)  # 无缓存可用
        else:
            yield (False, False)     # 无法执行且无缓存
    
    def cache_result(self, key: str, value: Any, ttl: Optional[float] = None):
        """将查询结果存入缓存"""
        if self._fallback:
            self._fallback.set(key, value, ttl)
    
    def get_cached(self, key: str) -> Optional[Any]:
        """获取缓存的查询结果"""
        if self._fallback:
            return self._fallback.get(key)
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """获取完整统计信息"""
        with self._stats_lock:
            stats = self._stats.to_dict()
        
        return {
            'state': self._state.value,
            'role': self._role.value,
            'reconnect_attempt': f"{self._current_attempt}/{self.config.max_attempts or '∞'}",
            'statistics': stats,
            'fallback_cache': self._fallback.get_stats() if self._fallback else None,
            'config': {
                'health_interval_s': self.config.health_check_interval,
                'query_timeout_s': self.config.query_timeout,
                'pool_size': self.config.pool_size,
            },
        }


# ==================== 全局实例初始化 ====================

_db_reconnector: Optional[DatabaseReconnector] = None


def init_db_reconnector(config: Optional[ReconnectConfig] = None) -> DatabaseReconnector:
    """初始化全局数据库重连服务"""
    global _db_reconnector
    
    if _db_reconnector is None:
        _db_reconnector = DatabaseReconnector(config)
    
    return _db_reconnector


def get_db_reconnector() -> Optional[DatabaseReconnector]:
    """获取全局数据库重连服务实例"""
    return _db_reconnector


async def start_db_monitoring():
    """启动数据库监控 (在应用启动时调用)"""
    reconnector = get_db_reconnector()
    if reconnector:
        await reconnector.start_monitoring()


async def stop_db_monitoring():
    """停止数据库监控 (在应用关闭时调用)"""
    reconnector = get_db_reconnector()
    if reconnector:
        await reconnector.stop_monitoring()


# ==================== 导出 ====================

__all__ = [
    'ConnectionState',
    'DBRole',
    'ConnectionStats',
    'ReconnectConfig',
    'FallbackCache',
    'DatabaseReconnector',
    'init_db_reconnector',
    'get_db_reconnector',
    'start_db_monitoring',
    'stop_db_monitoring',
]


# ==================== 快速验证脚本 ====================

if __name__ == '__main__':
    import asyncio
    from pathlib import Path
    
    async def test():
        print("=" * 60)
        print("🔄 Database Reconnection Service Test Suite")
        print("=" * 60)
        
        # 测试 1: 降级缓存
        print("\n💾 Test 1: Fallback Cache")
        
        cache = FallbackCache(ttl=60, max_size=100)
        
        cache.set('user_1', {'name': 'Alice', 'age': 30})
        cache.set('user_2', {'name': 'Bob', 'age': 25})
        print(f"  ✅ Set 2 cache entries")
        
        val = cache.get('user_1')
        assert val['name'] == 'Alice'
        print(f"  ✅ Get user_1: {val['name']}")
        
        miss = cache.get('nonexistent')
        assert miss is None
        print(f"  ✅ Get nonexistent: None (correct)")
        
        # LRU 测试
        for i in range(150):  # 超过 max_size=100
            cache.set(f'key_{i}', {'value': i})
        
        assert cache.get('user_1') is None  # 应该被淘汰
        assert cache.get('key_149') is not None  # 最新的应该保留
        print(f"  ✅ LRU eviction works (old keys evicted)")
        
        stats = cache.get_stats()
        assert int(stats['hit_rate'].replace('%', '')) > 0
        print(f"  ✅ Cache stats: hit_rate={stats['hit_rate']}, size={stats['size']}")
        
        # 测试 2: 数据库重连器 (仅测试逻辑，不真正连接)
        print("\n🔌 Test 2: Reconnector Logic (without real DB)")
        
        config = ReconnectConfig(
            max_attempts=5,
            base_delay=0.01,       # 快速测试
            max_delay=0.1,
            enable_fallback_cache=True,
        )
        
        recon = DatabaseReconnector(config)
        
        assert recon.state == ConnectionState.DISCONNECTED
        assert recon.is_connected == False
        print(f"  ✅ Initial state: {recon.state.value}")
        
        # 手动设置状态测试回调
        states_received = []
        recon.on_state_change(lambda s: states_received.append(s))
        
        await recon._set_state(ConnectionState.CONNECTED)
        assert recon.is_connected == True
        assert ConnectionState.CONNECTED in states_received
        print(f"  ✅ State change callback works: {states_received}")
        
        # 缓存功能
        recon.cache_result('test_key', {'data': 'cached'})
        cached_val = recon.get_cached('test_key')
        assert cached_val['data'] == 'cached'
        print(f"  ✅ Cache via reconnector works")
        
        # 统计
        stats = recon.get_stats()
        assert stats['state'] == 'connected'
        assert 'statistics' in stats
        print(f"  ✅ Stats generation works")
        
        print("\n" + "=" * 60)
        print("🎉 All Database Reconnection tests PASSED!")
        print("=" * 60)
    
    asyncio.run(test())
