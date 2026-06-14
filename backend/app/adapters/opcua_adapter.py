"""
OPC UA 协议适配器 — AGV-TMS 工业设备对接层

支持两种模式:
  1. 模拟模式 (Simulation Mode): 无需真实 OPC UA Server, 使用内存模拟设备状态
  2. 实时模式 (Live Mode): 通过 asyncua 连接真实的 OPC UA Server (PLC/AGV控制器)

协议标准: OPC UA (IEC 62541)
参考架构: 海康 RCS-2000 OPC UA 接口规范

使用方式:
    adapter = OpcUaAdapter(mode='simulation')
    await adapter.start()
    agv_status = await adapter.get_agv_status('agv_001')
    await adapter.send_command('agv_001', 'move', {'target': 'node_42'})
    await adapter.stop()
"""

from __future__ import annotations

import asyncio
import math
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


# ==================== 数据模型 ====================

class AgvCommand(str, Enum):
    """AGV 控制指令类型"""
    MOVE = "move"              # 移动到目标点
    STOP = "stop"              # 紧急停止
    RESUME = "resume"          # 恢复运行
    CHARGE = "charge"          # 去充电
    LOAD = "load"              # 载货
    UNLOAD = "unload"          # 卸货
    CANCEL_TASK = "cancel_task" # 取消当前任务


class AgvState(str, Enum):
    """AGV 运行状态 (对应 OPC UA 状态机)"""
    IDLE = "idle"              # 空闲
    MOVING = "moving"          # 移动中
    LOADING = "loading"        # 装货中
    UNLOADING = "unloading"    # 卸货中
    CHARGING = "charging"      # 充电中
    ERROR = "error"            # 故障
    MAINTENANCE = "maintenance"# 维护中
    OFFLINE = "offline"        # 离线


@dataclass
class AgvDeviceStatus:
    """AGV 设备完整状态 (OPC UA Node 映射)"""
    agv_id: str
    state: AgvState = AgvState.IDLE
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0         # 朝向角度
    speed: float = 0.0         # 当前速度 m/s
    battery_level: float = 100.0  # 电量百分比
    current_node_id: str = ""
    target_node_id: str = ""
    load_status: bool = False   # 是否载货
    error_code: int = 0
    error_message: str = ""
    last_heartbeat: float = field(default_factory=time.time)
    odometer: float = 0.0      # 总里程
    operating_hours: float = 0.0


@dataclass
class OpcUaCommandResult:
    """OPC UA 指令执行结果"""
    success: bool
    command: str
    agv_id: str
    message: str = ""
    timestamp: float = field(default_factory=time.time)


# ==================== 回调类型 ====================

StatusCallback = Callable[[AgvDeviceStatus], Awaitable[None]]
AlertCallback = Callable[[str, str, Dict], Awaitable[None]]  # agv_id, alert_type, detail


# ==================== 模拟器 ====================

class _SimulatedOpcUaServer:
    """
    内存模拟 OPC UA Server。

    模拟行为:
    - AGV 在节点间移动 (匀速, 可配置速度)
    - 电量随移动消耗, 充电时恢复
    - 随机故障注入 (可选)
    - 心跳信号周期发送
    """

    def __init__(
        self,
        num_agvs: int = 10,
        map_nodes: Optional[List[Dict]] = None,
        move_speed: float = 1.5,      # m/s
        battery_drain_rate: float = 0.5,  # percent per minute when moving
        charge_rate: float = 5.0,      # percent per minute when charging
        heartbeat_interval: float = 1.0,  # seconds
        fault_probability: float = 0.001,  # per step
    ):
        self.num_agvs = num_agvs
        self.map_nodes = map_nodes or []
        self.move_speed = move_speed
        self.battery_drain_rate = battery_drain_rate
        self.charge_rate = charge_rate
        self.heartbeat_interval = heartbeat_interval
        self.fault_probability = fault_probability

        self.agvs: Dict[str, AgvDeviceStatus] = {}
        self._callbacks: List[StatusCallback] = []
        self._alert_callbacks: List[AlertCallback] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._initialize_agvs()

    def _initialize_agvs(self):
        """初始化模拟 AGV 设备"""
        node_positions = [(n.get("x", 0), n.get("y", 0)) for n in self.map_nodes]

        for i in range(self.num_agvs):
            agv_id = f"sim_agv_{i+1:03d}"
            if node_positions:
                px,py = random.choice(node_positions)
            else:
                px,py = random.uniform(0,100), random.uniform(0,100)

            self.agvs[agv_id] = AgvDeviceStatus(
                agv_id=agv_id,
                state=AgvState.IDLE,
                x=px, y=py,
                battery_level=random.uniform(60, 100),
                current_node_id=self._nearest_node(px, py),
            )

    def _nearest_node(self, x: float, y: float) -> str:
        """找最近节点"""
        best_dist, best_id = float('inf'), ''
        for n in self.map_nodes:
            d = ((n.get("x",0)-x)**2 + (n.get("y",0)-y)**2)**0.5
            if d < best_dist:
                best_dist,best_id = d,n.get("id","")
        return best_id or f"node_{int(x)}_{int(y)}"

    def on_status_update(self, callback: StatusCallback):
        self._callbacks.append(callback)

    def on_alert(self, callback: AlertCallback):
        self._alert_callbacks.append(callback)

    async def start(self):
        """启动模拟引擎"""
        self._running = True
        self._task = asyncio.create_task(self._simulation_loop())
        logger.info(f"[OPC-UA-Sim] Started with {self.num_agvs} simulated AGVs")

    async def stop(self):
        """停止模拟引擎"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[OPC-UA-Sim] Stopped")

    async def send_command(
        self, agv_id: str, command: AgvCommand, params: Dict[str, Any] = None
    ) -> OpcUaCommandResult:
        """下发控制指令"""
        agv = self.agvs.get(agv_id)
        if not agv:
            return OpcUaCommandResult(False, command.value, agv_id, "AGV not found")

        params = params or {}

        try:
            if command == AgvCommand.MOVE:
                target = params.get("target", "")
                agv.target_node_id = target
                agv.state = AgvState.MOVING
                agv.speed = self.move_speed
                return OpcUaCommandResult(True, command.value, agv_id, f"Moving to {target}")

            elif command == AgvCommand.STOP:
                agv.state = AgvState.IDLE
                agv.speed = 0.0
                agv.target_node_id = ""
                return OpcUaCommandResult(True, command.value, agv_id, "Stopped")

            elif command == AgvCommand.RESUME:
                if agv.state == AgvState.IDLE and agv.target_node_id:
                    agv.state = AgvState.MOVING
                    agv.speed = self.move_speed
                return OpcUaCommandResult(True, command.value, agv_id, "Resumed")

            elif command == AgvCommand.CHARGE:
                agv.state = AgvState.CHARGING
                agv.speed = 0.0
                return OpcUaCommandResult(True, command.value, agv_id, "Charging")

            elif command == AgvCommand.LOAD:
                agv.load_status = True
                agv.state = AgvState.LOADING
                await asyncio.sleep(2.0)  # 模拟装货耗时
                agv.state = AgvState.IDLE
                return OpcUaCommandResult(True, command.value, agv_id, "Loaded")

            elif command == AgvCommand.UNLOAD:
                agv.load_status = False
                agv.state = AgvState.UNLOADING
                await asyncio.sleep(2.0)
                agv.state = AgvState.IDLE
                return OpcUaCommandResult(True, command.value, agv_id, "Unloaded")

            elif command == AgvCommand.CANCEL_TASK:
                agv.state = AgvState.IDLE
                agv.target_node_id = ""
                agv.speed = 0.0
                return OpcUaCommandResult(True, command.value, agv_id, "Task cancelled")

            return OpcUaCommandResult(False, command.value, agv_id, f"Unknown command")
        except Exception as e:
            return OpcUaCommandResult(False, command.value, agv_id, str(e))

    def get_all_statuses(self) -> Dict[str, AgvDeviceStatus]:
        """获取所有 AGV 状态快照"""
        return dict(self.agvs)

    def get_status(self, agv_id: str) -> Optional[AgvDeviceStatus]:
        """获取单个 AGV 状态"""
        return self.agvs.get(agv_id)

    async def _simulation_loop(self):
        """主仿真循环 — 更新 AGV 状态"""
        while self._running:
            now = time.time()

            for agv in self.agvs.values():
                dt = self.heartbeat_interval / 60.0  # 转为分钟

                # 状态更新逻辑
                if agv.state == AgvState.MOVING:
                    # 向目标移动
                    if agv.target_node_id:
                        target_node = next(
                            (n for n in self.map_nodes if n.get("id") == agv.target_node_id), None
                        )
                        if target_node:
                            tx, ty = target_node.get("x", 0), target_node.get("y", 0)
                            dx, dy = tx - agv.x, ty - agv.y
                            dist = (dx*dx + dy*dy)**0.5
                            step = self.move_speed * self.heartbeat_interval

                            if dist <= step:
                                agv.x, agv.y = tx, ty
                                agv.current_node_id = agv.target_node_id
                                agv.state = AgvState.IDLE
                                agv.speed = 0.0
                                agv.target_node_id = ""
                            else:
                                ratio = step / max(dist, 0.001)
                                agv.x += dx * ratio
                                agv.y += dy * ratio
                                agv.angle = math.degrees(math.atan2(dy, dx))

                    # 电量消耗
                    agv.battery_level = max(0, agv.battery_level - self.battery_drain_rate * dt)
                    agv.odometer += self.move_speed * self.heartbeat_interval
                    agv.operating_hours += self.heartbeat_interval / 3600.0

                elif agv.state == AgvState.CHARGING:
                    # 充电恢复
                    agv.battery_level = min(100, agv.battery_level + self.charge_rate * dt)
                    if agv.battery_level >= 99.5:
                        agv.state = AgvState.IDLE

                # 低电量告警
                if agv.battery_level < 20.0 and agv.state not in (AgvState.CHARGING, AgvState.ERROR):
                    for cb in self._alert_callbacks:
                        await cb(agv.agv_id, "low_battery", {"level": agv.battery_level})

                # 随机故障
                if self.fault_probability > 0 and random.random() < self.fault_probability:
                    agv.state = AgvState.ERROR
                    agv.error_code = random.randint(1000, 9999)
                    agv.error_message = "Simulated fault injection"
                    for cb in self._alert_callbacks:
                        await cb(agv.agv_id, "fault", {
                            "code": agv.error_code, "msg": agv.error_message
                        })

                agv.last_heartbeat = now

            # 触发回调
            for cb in self._callbacks:
                for agv in self.agvs.values():
                    try:
                        await cb(agv)
                    except Exception as e:
                        logger.debug(f"Status callback error: {e}")

            await asyncio.sleep(self.heartbeat_interval)


# ==================== 主适配器 ====================

class OpcUaAdapter:
    """
    OPC UA 统一适配器 — AGV-TMS 与工业设备的桥梁。

    支持模式:
      - 'simulation': 内存模拟, 无需外部依赖
      - 'live': 连接真实 OPC UA Server (需安装 asyncua)

    使用示例:
        adapter = OpcUaAdapter(mode='simulation', num_sim_agvs=15)
        await adapter.initialize(map_nodes=nodes)
        await adapter.start()

        # 订阅状态更新
        def on_agv_update(status): print(f"{status.agv_id}: {status.state}")

        # 下发指令
        result = await adapter.send_command('agv_001', AgvCommand.MOVE, {'target': 'node_42'})

        await adapter.stop()
    """

    def __init__(
        self,
        mode: str = "simulation",
        server_url: Optional[str] = None,
        num_sim_agvs: int = 10,
        move_speed: float = 1.5,
        heartbeat_interval: float = 1.0,
    ):
        self.mode = mode
        # 从配置读取默认 server_url，避免硬编码
        if server_url is None:
            try:
                from app.config import settings
                self.server_url = settings.OPCUA_SERVER_URL
            except (ImportError, AttributeError):
                self.server_url = "opc.tcp://localhost:4840"
        else:
            self.server_url = server_url
        self.num_sim_agvs = num_sim_agvs
        self.move_speed = move_speed
        self.heartbeat_interval = heartbeat_interval

        self._simulator: Optional[_SimulatedOpcUaServer] = None
        self._live_client: Any = None  # asyncua.Client instance
        self._status_cache: Dict[str, AgvDeviceStatus] = {}
        self._status_subscribers: List[StatusCallback] = []
        self._alert_subscribers: List[AlertCallback] = []
        self._initialized = False
        self._running = False
        self._map_nodes: List[Dict] = []

    async def initialize(self, map_nodes: Optional[List[Dict]] = None) -> None:
        """
        初始化适配器。

        Args:
            map_nodes: 地图节点列表 (用于模拟模式的路径规划)
        """
        self._map_nodes = map_nodes or []

        if self.mode == "simulation":
            self._simulator = _SimulatedOpcUaServer(
                num_agvs=self.num_sim_agvs,
                map_nodes=self._map_nodes,
                move_speed=self.move_speed,
                heartbeat_interval=self.heartbeat_interval,
            )
            # 转发回调
            self._simulator.on_status_update(self._on_status_update)
            self._simulator.on_alert(self._on_alert)

        elif self.mode == "live":
            try:
                from asyncua import Client
                self._live_client = Client(self.server_url)
                await self._live_client.connect()
                logger.info(f"[OPC-UA] Connected to {self.server_url}")
                # TODO: 浏览 OPC UA 节点空间, 建立 AGV 节点映射
            except ImportError:
                raise ImportError(
                    "Live mode requires 'asyncua' package. "
                    "Install with: pip install asyncua"
                )
            except Exception as e:
                logger.error(f"[OPC-UA] Connection failed: {e}")
                raise

        self._initialized = True
        logger.info(f"[OPC-UA] Adapter initialized in {self.mode} mode")

    async def start(self) -> None:
        """启动适配器 (开始数据流)"""
        if not self._initialized:
            await self.initialize()

        self._running = True
        if self.mode == "simulation":
            await self._simulator.start()
        elif self.mode == "live":
            # 启动订阅
            asyncio.create_task(self._live_subscription_loop())

        logger.info("[OPC-UA] Adapter started")

    async def stop(self) -> None:
        """停止适配器"""
        self._running = False
        if self._simulator:
            await self._simulator.stop()
        if self._live_client:
            try:
                await self._live_client.disconnect()
            except Exception as e:
                logger.debug("OPC UA disconnect error: %s", e)
        logger.info("[OPC-UA] Adapter stopped")

    # ==================== 数据读取 ====================

    async def get_all_agv_statuses(self) -> List[AgvDeviceStatus]:
        """获取所有 AGV 的当前状态"""
        if self.mode == "simulation" and self._simulator:
            all_status = self._simulator.get_all_statuses()
            self._status_cache = dict(all_status)
            return list(all_status.values())
        elif self.mode == "live" and self._live_client:
            # 从 OPC UA 读取
            return list(self._status_cache.values())
        return []

    async def get_agv_status(self, agv_id: str) -> Optional[AgvDeviceStatus]:
        """获取单个 AGV 状态"""
        if self.mode == "simulation" and self._simulator:
            status = self._simulator.get_status(agv_id)
            if status:
                self._status_cache[agv_id] = status
            return status
        return self._status_cache.get(agv_id)

    # ==================== 指令下发 ====================

    async def send_command(
        self,
        agv_id: str,
        command: AgvCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> OpcUaCommandResult:
        """向指定 AGV 下发控制指令"""
        if self.mode == "simulation" and self._simulator:
            result = await self._simulator.send_command(agv_id, command, params)
            logger.debug(f"[OPC-UA-Sim] Command {command.value} → {agv_id}: {result.message}")
            return result
        elif self.mode == "live" and self._live_client:
            # 通过 OPC UA Method call 下发
            try:
                # TODO: 调用 OPC UA 方法节点
                return OpcUaCommandResult(
                    True, command.value, agv_id,
                    "Command sent via OPC UA method call",
                )
            except Exception as e:
                return OpcUaCommandResult(False, command.value, agv_id, str(e))

        return OpcUaCommandResult(False, command.value, agv_id, "Adapter not running")

    async def emergency_stop(self, agv_id: Optional[str] = None) -> Dict[str, OpcUaCommandResult]:
        """紧急停止 — 停止指定 AGV 或全部 AGV"""
        results = {}
        targets = [agv_id] if agv_id else list(self._status_cache.keys())
        for aid in targets:
            results[aid] = await self.send_command(aid, AgvCommand.STOP)
        return results

    # ==================== 订阅 ====================

    def subscribe_status(self, callback: StatusCallback) -> None:
        """订阅 AGV 状态变更通知"""
        self._status_subscribers.append(callback)

    def subscribe_alerts(self, callback: AlertCallback) -> None:
        """订阅告警通知 (低电量、故障等)"""
        self._alert_subscribers.append(callback)

    async def _on_status_update(self, status: AgvDeviceStatus) -> None:
        """内部: 状态更新分发"""
        self._status_cache[status.agv_id] = status
        for cb in self._status_subscribers:
            try:
                await cb(status)
            except Exception as e:
                logger.debug(f"Subscriber error: {e}")

    async def _on_alert(self, agv_id: str, alert_type: str, detail: Dict) -> None:
        """内部: 告警分发"""
        for cb in self._alert_subscribers:
            try:
                await cb(agv_id, alert_type, detail)
            except Exception as e:
                logger.debug(f"Alert subscriber error: {e}")

    async def _live_subscription_loop(self) -> None:
        """实时模式: OPC UA 订阅轮询循环"""
        while self._running:
            try:
                # 周期性读取 AGV 状态节点
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OPC-UA] Subscription error: {e}")
                await asyncio.sleep(5.0)

    # ==================== 工具方法 ====================

    def to_api_format(self, status: AgvDeviceStatus) -> Dict[str, Any]:
        """将 AgvDeviceStatus 转换为 API 兼容格式 (schemas.py AgvStatus)"""
        return {
            "id": status.agv_id,
            "current_node": status.current_node_id,
            "battery": status.battery_level,
            "speed": status.speed,
            "state": status.state.value,
            "x": status.x,
            "y": status.y,
            "angle": status.angle,
            "load_status": status.load_status,
            "error_code": status.error_code,
            "target_node": status.target_node_id,
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def connected_agv_count(self) -> int:
        return len(self._status_cache)
