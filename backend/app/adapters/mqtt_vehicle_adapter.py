"""
MQTT 消息总线适配器 — Phase 6 (P0-6.2).

实现 MQTT 协议的车辆适配器, 补齐 Phase 1 遗留的 P0-1.5。

主题设计 (参考 VDA5050 + 海康 RCS MQTT 接口):
  - agv/{id}/status     — AGV 状态上报 (订阅)
  - agv/{id}/command    — AGV 指令下发 (发布)
  - agv/{id}/alert      — AGV 告警 (订阅)
  - system/alert        — 系统级告警 (订阅)
  - system/heartbeat    — 系统心跳 (发布)

依赖: paho-mqtt (可选, 未安装时降级为模拟模式)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .base_adapter import (
    BaseVehicleAdapter,
    CommandResult,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
)

logger = logging.getLogger(__name__)

# 检查 paho-mqtt 是否可用
try:
    import paho.mqtt.client as mqtt_client
    HAS_PAHO = True
except ImportError:
    HAS_PAHO = False
    logger.info("paho-mqtt not installed, MQTT adapter will run in simulation mode")


class MqttVehicleAdapter(BaseVehicleAdapter):
    """
    MQTT 车辆适配器.

    模式:
      - live: 连接真实 MQTT Broker (需 paho-mqtt)
      - simulation: 内存模拟, 无需外部依赖
    """

    def __init__(
        self,
        mode: str = "simulation",
        broker_host: str = "localhost",
        broker_port: int = 1883,
        topic_prefix: str = "agv",
        num_sim_agvs: int = 10,
        **kwargs,
    ):
        super().__init__(name="mqtt", protocol="mqtt")
        self.mode = mode
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.topic_prefix = topic_prefix
        self.num_sim_agvs = num_sim_agvs

        self._client: Optional[Any] = None
        self._sim_agvs: Dict[str, VehicleStatus] = {}
        self._command_results: Dict[str, CommandResult] = {}

    async def connect(self) -> bool:
        """连接 MQTT Broker 或初始化模拟器"""
        if self.mode == "live" and HAS_PAHO:
            try:
                self._client = mqtt_client.Client(client_id=f"agv-tms-{int(time.time())}")
                self._client.on_connect = self._on_connect
                self._client.on_message = self._on_message
                self._client.connect(self.broker_host, self.broker_port, 60)
                self._client.loop_start()
                self._connected = True
                logger.info("MQTT connected to %s:%d", self.broker_host, self.broker_port)
            except Exception as e:
                logger.error("MQTT connect failed: %s, falling back to simulation", e)
                self.mode = "simulation"
                self._init_simulation()
        else:
            self._init_simulation()

        self._vehicle_count = len(self._sim_agvs) if self._sim_agvs else self.num_sim_agvs
        return True

    def _init_simulation(self):
        """初始化模拟 AGV"""
        self._connected = True
        for i in range(self.num_sim_agvs):
            vid = f"mqtt_agv_{i+1:03d}"
            self._sim_agvs[vid] = VehicleStatus(
                vehicle_id=vid,
                state=VehicleState.IDLE,
                battery_level=80.0 + (i % 20),
                x=float(i * 10),
                y=float(i * 5),
            )
        logger.info("MQTT simulation mode: %d AGVs", len(self._sim_agvs))

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调"""
        if rc == 0:
            logger.info("MQTT connected, subscribing to topics")
            client.subscribe(f"{self.topic_prefix}/+/status")
            client.subscribe(f"{self.topic_prefix}/+/alert")
            client.subscribe("system/alert")
        else:
            logger.error("MQTT connect failed with code %d", rc)

    def _on_message(self, client, userdata, msg):
        """MQTT 消息回调"""
        try:
            payload = json.loads(msg.payload.decode())
            topic_parts = msg.topic.split("/")
            if len(topic_parts) >= 3 and topic_parts[2] == "status":
                agv_id = topic_parts[1]
                self._update_sim_status(agv_id, payload)
        except Exception as e:
            logger.debug("MQTT message parse error: %s", e)

    def _update_sim_status(self, agv_id: str, data: Dict):
        """更新模拟 AGV 状态"""
        if agv_id not in self._sim_agvs:
            self._sim_agvs[agv_id] = VehicleStatus(vehicle_id=agv_id)
        status = self._sim_agvs[agv_id]
        if "battery" in data:
            status.battery_level = data["battery"]
        if "x" in data:
            status.x = data["x"]
        if "y" in data:
            status.y = data["y"]
        if "state" in data:
            try:
                status.state = VehicleState(data["state"])
            except ValueError:
                pass

    async def disconnect(self) -> None:
        """断开连接"""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        self._sim_agvs.clear()
        logger.info("MQTT adapter disconnected")

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """下发控制指令 via MQTT"""
        params = params or {}
        ts = time.time()

        if self.mode == "live" and self._client:
            topic = f"{self.topic_prefix}/{vehicle_id}/command"
            payload = json.dumps({
                "command": command.value,
                "params": params,
                "timestamp": ts,
            })
            self._client.publish(topic, payload)
            return CommandResult(
                success=True, vehicle_id=vehicle_id,
                command=command.value, message="Command published via MQTT",
                timestamp=ts,
            )

        # 模拟模式
        status = self._sim_agvs.get(vehicle_id)
        if not status:
            return CommandResult(
                success=False, vehicle_id=vehicle_id,
                command=command.value, message="AGV not found",
            )

        if command == VehicleCommand.MOVE:
            status.state = VehicleState.MOVING
            status.target_node = params.get("target", "")
            msg = f"Moving to {params.get('target', 'unknown')}"
        elif command == VehicleCommand.STOP:
            status.state = VehicleState.IDLE
            status.speed = 0.0
            msg = "Stopped"
        elif command == VehicleCommand.CHARGE:
            status.state = VehicleState.CHARGING
            msg = "Charging"
        elif command == VehicleCommand.LOAD:
            status.load_status = True
            msg = "Loaded"
        elif command == VehicleCommand.UNLOAD:
            status.load_status = False
            msg = "Unloaded"
        else:
            msg = f"Command {command.value} executed"

        return CommandResult(
            success=True, vehicle_id=vehicle_id,
            command=command.value, message=msg, timestamp=ts,
        )

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取单个车辆状态"""
        return self._sim_agvs.get(vehicle_id)

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有车辆状态"""
        return list(self._sim_agvs.values())
