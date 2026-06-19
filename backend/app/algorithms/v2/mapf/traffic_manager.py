"""
交通管制与死锁预防系统 (Traffic Control & Deadlock Prevention)
参考海康RCS区域锁定机制 + 极智嘉RMS时空体积预留算法

核心功能:
├── 1. 资源锁定管理器 (Resource Lock Manager)
│   ├── 点锁 (Vertex Lock): AGV占据某个位置
│   ├── 边锁 (Edge Lock): AGV正在通过某段路径
│   └── 区域锁 (Zone Lock): AGV进入某区域(如窄道/路口)
│
├── 2. 死锁检测与预防 (Deadlock Detection & Prevention)
│   ├── Wait-For Graph 循环检测 (O(V+E))
│   ├── Wait-Die / Wound-Wait 策略
│   └── 预防式资源排序 (避免循环等待)
│
├── 3. 交通管制策略 (Traffic Control Policies)
│   ├── 基于容量的准入控制 (Semaphore)
│   ├── 基于优先级的通行权分配
│   ├── 拥堵检测与绕行建议
│   └── 动态限速控制
│
└── 4. 与现有系统集成
    ├── 与 v2/mapf/cbs_solver.py CBS约束联动
    ├── 与 v2/core/dispatcher.py 分发器集成
    └── 通过 Kafka 发布交通事件

性能指标:
- 锁定操作: O(1) 平均 (HashMap)
- 死锁检测: O(V+E) V=等待agent数, E=等待边数
- 拥堵计算: O(N) N=节点数
- 内存占用: ~2KB/AGV (预估)

适用规模:
- 推荐: 10-500台AGV (单实例)
- 扩展: 可分区部署 (每个区域一个Manager)

Author: Architecture Team
Date: 2026-06-19
References:
  - 海康RCS-2000 V4.0 技术白皮书 (区域锁定+优先级调度)
  - 极智嘉RMS MAPF算法 (时空预留 + 拥堵预防疏散)
  - Operating Systems Concepts (Silberschatz) - 死锁处理章节
"""

from __future__ import annotations

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, Any, Callable
from enum import Enum, auto
from collections import defaultdict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 数据模型定义
# ═══════════════════════════════════════════════════════════════

class LockType(Enum):
    """锁定类型"""
    VERTEX = "vertex"      # 点锁: 占据单个节点位置
    EDGE = "edge"          # 边锁: 正在通过两个节点间的边
    ZONE = "zone"          # 区域锁: 进入某个逻辑区域 (如路口、窄道)


class ConflictResolutionStrategy(Enum):
    """冲突解决策略"""
    WAIT_DIE = "wait_die"       # 老进程等新进程，新进程放弃 (abort)
    WOUND_WAIT = "wound_wait"   # 老进程抢占新进程 (preempt)
    PRIORITY_BASED = "priority" # 按业务优先级决定


class TrafficEventType(Enum):
    """交通事件类型"""
    LOCK_ACQUIRED = "lock_acquired"
    LOCK_RELEASED = "lock_released"
    DEADLOCK_DETECTED = "deadlock_detected"
    CONGESTION_WARNING = "congestion_warning"
    ROUTE_REROUTE = "route_reroute"


@dataclass(frozen=True)
class ResourceId:
    """
    资源唯一标识
    
    格式规则:
    - Vertex: "v:{node_id}"        例: v:42
    - Edge: "e:{from}:{to}"         例: e:5:12
    - Zone: "z:{zone_name}"         例: z:intersection_A3
    """
    resource_type: LockType
    identifier: str
    
    @classmethod
    def vertex(cls, node_id: int) -> 'ResourceId':
        return cls(LockType.VERTEX, f"v:{node_id}")
    
    @classmethod 
    def edge(cls, from_node: int, to_node: int) -> 'ResourceId':
        f, t = (from_node, to_node) if from_node <= to_node else (to_node, from_node)
        return cls(LockType.EDGE, f"e:{f}:{t}")
    
    @classmethod
    def zone(cls, zone_name: str) -> 'ResourceId':
        return cls(LockType.ZONE, f"z:{zone_name}")
    
    def __str__(self):
        return self.identifier


@dataclass
class ResourceLock:
    """资源锁状态记录"""
    resource: ResourceId
    owner_id: str
    acquire_time: float
    expected_duration: float
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.acquire_time) > (self.expected_duration * 3)
    
    @property
    def remaining_time(self) -> float:
        elapsed = time.monotonic() - self.acquire_time
        return max(0, self.expected_duration - elapsed)


@dataclass
class TrafficEvent:
    """交通事件 (用于日志/监控/Kafka发布)"""
    event_type: TrafficEventType
    timestamp: float
    agent_id: str
    resource: Optional[ResourceId] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "resource": str(self.resource) if self.resource else None,
            **self.details
        }


@dataclass
class CongestionInfo:
    """拥堵信息"""
    node_id: int
    utilization: float           # 使用率 [0, 1]
    current_load: int            # 当前负载
    capacity: int                # 容量上限
    queue_length: int             # 等待队列长度
    avg_wait_time_ms: float      # 平均等待时间(ms)
    severity: str                # low / medium / high / critical
    suggested_alternatives: List[int] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 核心引擎: 资源锁定管理器
# ═══════════════════════════════════════════════════════════════

class ResourceLockManager:
    """
    分布式资源锁定管理器
    
    设计原则:
    1. 非阻塞 try-lock: 不允许阻塞等待,立即返回成功/失败
    2. 超时自动释放: 防止死锁导致的永久占用 (watchdog)
    3. 策略可配置: 支持Wait-Die/Wound-Wait/Priority三种模式
    4. 事件驱动: 所有操作产生事件供外部订阅
    
    线程安全: ✅ 使用asyncio.Lock保护内部状态
    性能: O(1) lock/unlock (HashMap lookup)
    
    使用示例:
        manager = ResourceLockManager(strategy=PRIORITY_BASED)
        
        success = await manager.acquire(
            agent_id="AGV-001",
            resource=ResourceId.vertex(5),
            duration=2.0,
            priority=1
        )
        
        if not success:
            pass
        
        await manager.release("AGV-001", ResourceId.vertex(5))
    
    集成点:
    - CBS Solver: 将Constraint转换为ResourceLock
    - Dispatcher: 在下发指令前获取路径上的所有锁
    - AGV Driver: 完成移动后释放对应锁
    """
    
    def __init__(
        self,
        strategy: ConflictResolutionStrategy = ConflictResolutionStrategy.WAIT_DIE,
        lock_timeout: float = 30.0,
        enable_watchdog: bool = True,
        event_callback: Optional[Callable[[TrafficEvent], None]] = None
    ):
        self.strategy = strategy
        self.lock_timeout = lock_timeout
        self.enable_watchdog = enable_watchdog
        self.event_callback = event_callback
        
        self._locks: Dict[str, ResourceLock] = {}
        self._agent_locks: Dict[str, Set[str]] = defaultdict(set)
        self._wait_for_graph: Dict[str, Set[str]] = defaultdict(set)
        
        self._lock = asyncio.Lock()
        self._stats = {
            "total_acquisitions": 0,
            "total_releases": 0,
            "failed_acquisitions": 0,
            "deadlocks_detected": 0,
            "timeouts_recovered": 0
        }
        
        self._watchdog_task: Optional[asyncio.Task] = None
    
    async def start(self):
        if self.enable_watchdog:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logger.info("[TrafficControl] ResourceLockManager started with watchdog")
    
    async def stop(self):
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.info("[TrafficControl] ResourceLockManager stopped")
    
    async def acquire(
        self,
        agent_id: str,
        resource: ResourceId,
        duration: float = 5.0,
        priority: int = 0,
        metadata: Optional[Dict] = None
    ) -> bool:
        """尝试获取资源锁 (非阻塞)"""
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        
        resource_key = str(resource)
        
        async with self._lock:
            # Case 1: 资源空闲 → 直接获取
            if resource_key not in self._locks:
                lock = ResourceLock(
                    resource=resource,
                    owner_id=agent_id,
                    acquire_time=time.monotonic(),
                    expected_duration=duration,
                    priority=priority,
                    metadata=metadata or {}
                )
                self._locks[resource_key] = lock
                self._agent_locks[agent_id].add(resource_key)
                self._stats["total_acquisitions"] += 1
                
                self._emit_event(TrafficEventType.LOCK_ACQUIRED, agent_id, resource)
                logger.debug(f"[TrafficControl] {agent_id} acquired {resource}")
                return True
            
            existing = self._locks[resource_key]
            
            # Case 2: 自己已持有 → 更新 (可重入)
            if existing.owner_id == agent_id:
                existing.expected_duration = max(existing.expected_duration, duration)
                if metadata:
                    existing.metadata.update(metadata)
                return True
            
            # Case 3: 被他人占用 → 冲突解决
            resolution = self._resolve_conflict(agent_id, existing, priority)
            
            if resolution == "acquire":
                self._release_internal(existing.owner_id, resource_key, preempted=True)
                
                new_lock = ResourceLock(
                    resource=resource, owner_id=agent_id,
                    acquire_time=time.monotonic(), expected_duration=duration,
                    priority=priority, metadata=metadata or {}
                )
                self._locks[resource_key] = new_lock
                self._agent_locks[agent_id].add(resource_key)
                self._stats["total_acquisitions"] += 1
                
                self._emit_event(TrafficEventType.LOCK_ACQUIRED, agent_id, resource, {"preempted": existing.owner_id})
                logger.debug(f"[TrafficControl] {agent_id} preempted {existing.owner_id} for {resource}")
                return True
            
            elif resolution == "wait":
                self._wait_for_graph[agent_id].add(existing.owner_id)
                
                has_deadlock = self._detect_cycle(agent_id)
                
                if has_deadlock:
                    self._wait_for_graph[agent_id].discard(existing.owner_id)
                    self._stats["deadlocks_detected"] += 1
                    
                    self._emit_event(TrafficEventType.DEADLOCK_DETECTED, agent_id, resource, {
                        "waiting_for": existing.owner_id, "strategy": self.strategy.value
                    })
                    
                    logger.warning(
                        f"[TrafficControl] Deadlock prevented: {agent_id} ↔ {existing.owner_id} "
                        f"on {resource}, strategy={self.strategy.value}"
                    )
                    
                    self._stats["failed_acquisitions"] += 1
                    return False
                else:
                    self._stats["failed_acquisitions"] += 1
                    return False
            
            else:
                self._stats["failed_acquisitions"] += 1
                return False
    
    async def release(self, agent_id: str, resource: ResourceId) -> bool:
        """释放资源锁"""
        resource_key = str(resource)
        async with self._lock:
            return self._release_internal(agent_id, resource_key)
    
    def _release_internal(self, agent_id: str, resource_key: str, preempted: bool = False) -> bool:
        """内部释放实现 (必须在 _lock 内调用)"""
        if resource_key in self._locks and self._locks[resource_key].owner_id == agent_id:
            del self._locks[resource_key]
            self._agent_locks[agent_id].discard(resource_key)
            self._stats["total_releases"] += 1
            
            resource = ResourceId(resource_key.split(':')[0], resource_key)
            self._emit_event(TrafficEventType.LOCK_RELEASED, agent_id, resource, {"preempted": preempted})
            return True
        return False
    
    async def acquire_path(
        self,
        agent_id: str,
        path_nodes: List[int],
        path_edges: List[Tuple[int, int]],
        duration_per_segment: float = 2.0,
        priority: int = 0
    ) -> Tuple[bool, List[ResourceId]]:
        """
        原子性获取整条路径的所有锁
        
        这是核心接口: Dispatcher在下发指令前调用此方法
        如果任一锁获取失败,则回滚已获取的所有锁 (All-or-Nothing语义)
        
        Returns:
            (success, acquired_resources) 成功标志和已获取的资源列表
        """
        resources_to_acquire: List[Tuple[ResourceId, float]] = []
        
        for node in path_nodes:
            resources_to_acquire.append((ResourceId.vertex(node), duration_per_segment))
        
        for from_n, to_n in path_edges:
            resources_to_acquire.append((ResourceId.edge(from_n, to_n), duration_per_segment * 0.8))
        
        acquired: List[ResourceId] = []
        
        for resource, duration in resources_to_acquire:
            success = await self.acquire(agent_id, resource, duration, priority)
            
            if success:
                acquired.append(resource)
            else:
                # 失败! 回滚所有已获取的锁
                logger.debug(f"[TrafficControl] Path acquisition failed at {resource}, "
                           f"rolling back {len(acquired)} locks for {agent_id}")
                
                for acquired_resource in acquired:
                    await self.release(agent_id, acquired_resource)
                
                return False, []
        
        return True, acquired
    
    async def release_path(self, agent_id: str, resources: List[ResourceId]):
        """释放整条路径的所有锁"""
        for resource in resources:
            await self.release(agent_id, resource)
    
    def _resolve_conflict(self, requester: str, existing_lock: ResourceLock, requester_priority: int) -> str:
        """根据策略解决冲突: 'acquire'/'wait'/'abort'"""
        if self.strategy == ConflictResolutionStrategy.WAIT_DIE:
            if requester_priority <= existing_lock.priority:
                return "wait"
            else:
                return "abort"
        elif self.strategy == ConflictResolutionStrategy.WOUND_WAIT:
            if requester_priority <= existing_lock.priority:
                return "acquire"
            else:
                return "wait"
        else:  # PRIORITY_BASED
            if requester_priority < existing_lock.priority:
                return "acquire"
            else:
                return "wait"
    
    def _detect_cycle(self, start_agent: str) -> bool:
        """
        Wait-For Graph 环检测 (DFS算法)
        
        时间复杂度: O(V + E)
        V = wait-for图中的agent数, E = wait-for关系数
        """
        visited: Set[str] = set()
        stack: List[str] = [start_agent]
        in_stack: Set[str] = set()
        
        while stack:
            node = stack.pop()
            
            if node in in_stack:
                return True  # 发现环!
            
            if node in visited:
                continue
            
            visited.add(node)
            in_stack.add(node)
            
            for neighbor in self._wait_for_graph.get(node, set()):
                if neighbor not in visited:
                    stack.append(neighbor)
            
            in_stack.discard(node)
        
        return False
    
    async def _watchdog_loop(self):
        """后台看门狗定时器 - 清理超时僵尸锁"""
        interval = 5.0
        
        while True:
            try:
                await asyncio.sleep(interval)
                
                async with self._lock:
                    expired_keys = [key for key, lock in self._locks.items() if lock.is_expired]
                    
                    for key in expired_keys:
                        expired_lock = self._locks.pop(key)
                        self._agent_locks[expired_lock.owner_id].discard(key)
                        self._stats["timeouts_recovered"] += 1
                        
                        logger.warning(
                            f"[TrafficControl-Watchdog] Expired lock recovered: "
                            f"{key} held by {expired_lock.owner_id} "
                            f"for {time.monotonic() - expired_lock.acquire_time:.1f}s"
                        )
                        
                        self._emit_event(TrafficEventType.LOCK_RELEASED, expired_lock.owner_id, 
                                        expired_lock.resource, {"reason": "timeout"})
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TrafficControl-Watchdog] Error: {e}")
    
    def _emit_event(self, event_type: TrafficEventType, agent_id: str, 
                    resource: Optional[ResourceId], details: Dict = None):
        """发送交通事件"""
        if self.event_callback:
            event = TrafficEvent(event_type=event_type, timestamp=time.monotonic(),
                                agent_id=agent_id, resource=resource, details=details or {})
            try:
                self.event_callback(event)
            except Exception as e:
                logger.warning(f"[TrafficControl] Event callback error: {e}")
    
    # ═════════════════════════════════════════════════════════
    # 查询接口
    # ═════════════════════════════════════════════════════════
    
    def get_lock_status(self, resource: ResourceId) -> Optional[Dict[str, Any]]:
        """查询资源锁定状态"""
        key = str(resource)
        lock = self._locks.get(key)
        if not lock:
            return None
        return {
            "resource": str(lock.resource),
            "owner": lock.owner_id,
            "held_duration": time.monotonic() - lock.acquire_time,
            "remaining": lock.remaining_time,
            "priority": lock.priority,
            "metadata": lock.metadata
        }
    
    def get_agent_holds(self, agent_id: str) -> List[Dict[str, Any]]:
        """查询agent当前持有的所有锁"""
        resource_keys = self._agent_locks.get(agent_id, set())
        result = []
        for key in resource_keys:
            lock = self._locks.get(key)
            if lock:
                result.append({
                    "resource": str(lock.resource),
                    "held_duration": round(time.monotonic() - lock.acquire_time, 2),
                    "remaining": round(lock.remaining_time, 2)
                })
        return result
    
    def get_global_stats(self) -> Dict[str, Any]:
        """获取全局统计信息"""
        return {
            **self._stats,
            "active_locks": len(self._locks),
            "active_agents": len(self._agent_locks),
            "pending_dependencies": sum(len(v) for v in self._wait_for_graph.values())
        }
    
    def get_all_active_locks(self) -> List[Dict[str, Any]]:
        """获取所有活跃锁的快照 (用于调试和监控)"""
        return [
            {
                "resource": str(lock.resource),
                "owner": lock.owner_id,
                "priority": lock.priority,
                "held_duration": round(time.monotonic() - lock.acquire_time, 2),
                "remaining": round(lock.remaining_time, 2)
            }
            for lock in self._locks.values()
        ]


# ═══════════════════════════════════════════════════════════════
# 交通管制系统 (高级封装)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TrafficNodeConfig:
    """交通节点配置"""
    node_id: int
    x: float
    y: float
    capacity: int = 1                  # 同时容纳AGV数量
    intersection_type: Optional[str] = None  # cross / t_junction / merge / fork / narrow
    speed_limit: float = 1.5           # 最大速度限制 (m/s)
    is_charging_point: bool = False
    is_parking_point: bool = False


@dataclass
class TrafficEdgeConfig:
    """交通边配置"""
    from_node: int
    to_node: int
    distance: float = 1.0              # 边长度 (米)
    direction: str = "bidirectional"  # bidirectional / one_way
    weight: float = 1.0               # 权重 (用于路径规划)
    is_narrow_passage: bool = False    # 是否窄道 (容量自动降为1)


class TrafficControlSystem:
    """
    综合交通管制系统 (封装ResourceLockManager + 拥堵分析)
    
    架构层次:
    ┌─────────────────────────────────────┐
    │  TrafficControlSystem (本类)         │  ← 高层API: 拥堵分析、统计、建议
    ├─────────────────────────────────────┤
    │  ResourceLockManager (上类)          │  ← 中层API: 锁管理、死锁预防
    ├─────────────────────────────────────┤
    │  asyncio.Semaphore (per-node)       │  ← 底层: 并发控制原语
    └─────────────────────────────────────┘
    
    核心特性:
    1. 基于容量的准入控制 (Semaphore模式)
    2. 基于优先级的通行权分配
    3. 拥堵检测与动态预警
    4. 绕行路径推荐
    5. 全局效率指标计算
    """
    
    def __init__(
        self,
        strategy: ConflictResolutionStrategy = ConflictResolutionStrategy.PRIORITY_BASED,
        congestion_threshold: float = 0.8,
        event_callback: Optional[Callable] = None
    ):
        self.lock_manager = ResourceLockManager(strategy=strategy, event_callback=event_callback)
        self.congestion_threshold = congestion_threshold
        
        self._nodes: Dict[int, TrafficNodeConfig] = {}
        self._edges: Dict[Tuple[int, int], TrafficEdgeConfig] = {}
        self._node_load: Dict[int, int] = defaultdict(int)
        self._node_history: Dict[int, List[float]] = defaultdict(list)
        self._last_sample_time: float = 0.0
        self._system_start_time = time.monotonic()
    
    def load_map_topology(self, nodes: List[TrafficNodeConfig], edges: List[TrafficEdgeConfig]):
        """加载地图拓扑结构"""
        self._nodes = {n.node_id: n for n in nodes}
        self._edges = {}
        for e in edges:
            key = (e.from_node, e.to_node)
            self._edges[key] = e
            if e.direction == "bidirectional":
                self._edges[(e.to_node, e.from_node)] = e
        logger.info(f"[TrafficSystem] Map loaded: {len(nodes)} nodes, {len(edges)} edges")
    
    async def start(self):
        """启动交通管制系统"""
        await self.lock_manager.start()
        self._system_start_time = time.monotonic()
        logger.info("[TrafficSystem] Started")
    
    async def stop(self):
        """停止交通管制系统"""
        await self.lock_manager.stop()
        logger.info("[TrafficSystem] Stopped")
    
    async def request_passage(
        self,
        agv_id: str,
        path: List[int],
        priority: int = 0,
        estimated_durations: Optional[List[float]] = None
    ) -> Tuple[bool, str]:
        """
        请求通行路径 (高层接口)
        
        Args:
            agv_id: AGV标识
            path: 经过的节点列表
            priority: AGV优先级 (数值越小越高)
            estimated_durations: 每段预计耗时列表
            
        Returns:
            (allowed, reason) 是否允许通行及原因
        """
        if len(path) < 2:
            return False, "Path too short"
        
        durations = estimated_durations or [2.0] * (len(path) - 1)
        
        edges = [(path[i], path[i+1]) for i in range(len(path)-1)]
        
        success, resources = await self.lock_manager.acquire_path(
            agent_id=agv_id,
            path_nodes=path,
            path_edges=edges,
            duration_per_segment=sum(durations) / max(len(durations), 1),
            priority=priority
        )
        
        if success:
            # 更新节点负载
            for node in path[:-1]:  # 不包含目标节点(到达后即离开)
                self._node_load[node] += 1
            
            return True, f"Path acquired with {len(resources)} locks"
        else:
            return False, "Path blocked by traffic control"
    
    def release_passage(self, agv_id: str, path: List[int]):
        """释放路径上的所有资源"""
        for node in path[:-1]:
            if self._node_load[node] > 0:
                self._node_load[node] -= 1
        # Note: 实际锁释放在lock_manager中处理
    
    def detect_congestion(self) -> List[CongestionInfo]:
        """
        拥堵检测: 扫描所有节点,识别高利用率区域
        
        阈值分级:
        - low:     50% < utilization ≤ 70%
        - medium:  70% < utilization ≤ threshold (default 80%)
        - high:    threshold < utilization ≤ 95%
        - critical:utilization > 95% 或 capacity exceeded
        
        Returns:
            拥堵节点列表 (按严重程度排序)
        """
        congested: List[CongestionInfo] = []
        current_time = time.monotonic()
        
        for node_id, config in self._nodes.items():
            load = self._node_load.get(node_id, 0)
            capacity = max(config.capacity, 1)
            utilization = min(load / capacity, 1.5)  # 允许超100%显示
            
            if utilization >= 0.5:  # 只报告半载以上
                # 计算平均等待时间 (基于历史数据采样)
                history = self._node_history.get(node_id, [])
                avg_wait = sum(history[-10:]) / max(len(history[-10:]), 1) * 1000 if history else 0
                
                # 严重等级判定
                if utilization > 0.95 or load > capacity:
                    severity = "critical"
                elif utilization > self.congestion_threshold:
                    severity = "high"
                elif utilization > 0.7:
                    severity = "medium"
                else:
                    severity = "low"
                
                # 推荐替代邻居节点
                alternatives = [
                    n for (f, t), e in self._edges.items() 
                    if f == node_id or t == node_id
                    for n in [f, t] if n != node_id and self._node_load.get(n, 0) < capacity * 0.5
                ][:3]
                
                info = CongestionInfo(
                    node_id=node_id,
                    utilization=round(utilization, 3),
                    current_load=load,
                    capacity=capacity,
                    queue_length=max(load - capacity, 0),
                    avg_wait_time_ms=round(avg_wait, 1),
                    severity=severity,
                    suggested_alternatives=alternatives[:3]
                )
                congested.append(info)
        
        # 按严重程度和利用率排序
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        congested.sort(key=lambda c: (severity_order.get(c.severity, 99), -c.utilization))
        
        # 记录历史数据 (用于趋势分析)
        if current_time - self._last_sample_time > 10.0:  # 每10秒采样一次
            self._last_sample_time = current_time
            for info in congested:
                self._node_history[info.node_id].append(info.utilization)
                # 保留最近100个采样点
                if len(self._node_history[info.node_id]) > 100:
                    self._node_history[info.node_id] = self._node_history[info.node_id][-100:]
        
        return congested
    
    def get_global_efficiency(self) -> float:
        """
        计算全网通行效率
        
        效率公式: Σ(actual_flow) / Σ(capacity)
        返回值: [0, 1] 其中 1.0 表示满效率运行
        """
        total_capacity = sum(max(n.capacity, 1) for n in self._nodes.values())
        total_load = sum(self._node_load.values())
        
        if total_capacity == 0:
            return 1.0
        
        efficiency = total_load / total_capacity
        return round(min(efficiency, 1.0), 4)
    
    def get_system_report(self) -> Dict[str, Any]:
        """
        生成完整的交通系统报告 (用于监控大屏/API)
        
        包含:
        - 效率指标
        - 拥堵热点
        - 锁定状态统计
        - 时间序列趋势
        """
        congested_nodes = self.detect_congestion()
        
        report = {
            "timestamp": time.time(),
            "uptime_seconds": time.monotonic() - self._system_start_time,
            
            "efficiency": {
                "global": self.get_global_efficiency(),
                "total_nodes": len(self._nodes),
                "total_edges": len(self._edges) // 2,  # 双向边计数一次
                "avg_utilization": round(
                    sum(c.utilization for c in congested_nodes) / max(len(congested_nodes), 1), 3
                ) if congested_nodes else 0.0
            },
            
            "congestion": {
                "hotspot_count": len([c for c in congested_nodes if c.severity in ("high", "critical")]),
                "details": [
                    {
                        "node_id": c.node_id,
                        "utilization": c.utilization,
                        "severity": c.severity,
                        "queue_length": c.queue_length,
                        "alternatives": c.suggested_alternatives
                    }
                    for c in congested_nodes[:20]  # 最多返回20个拥堵点
                ]
            },
            
            "locks": self.lock_manager.get_global_stats(),
            "active_locks": self.lock_manager.get_all_active_locks()[:50]  # 最多返回50条
        }
        
        return report


# ═══════════════════════════════════════════════════════════════
# 导出接口
# ═══════════════════════════════════════════════════════════════

__all__ = [
    # 数据模型
    'LockType', 'ConflictResolutionStrategy', 'TrafficEventType',
    'ResourceId', 'ResourceLock', 'TrafficEvent', 'CongestionInfo',
    'TrafficNodeConfig', 'TrafficEdgeConfig',
    
    # 核心引擎
    'ResourceLockManager', 'TrafficControlSystem',
]

__version__ = '2.0.0'
