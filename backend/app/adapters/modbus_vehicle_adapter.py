"""
Modbus TCP 协议适配器 — Phase 7 (P0-7.2).

对接 PLC 设备: 输送线/提升机/充电桩。
寄存器映射配置化, 支持线圈/保持寄存器读写。

依赖: pymodbus (可选, 未安装时降级为模拟模式)
"""

from __future__ import annotations

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

try:
    from pymodbus.client import ModbusTcpClient
    HAS_PYMODBUS = True
except ImportError:
    HAS_PYMODBUS = False
    logger.info("pymodbus not installed, Modbus adapter will run in simulation mode")


# 默认寄存器映射 (参考海康 RCS Modbus 接口)
DEFAULT_REGISTER_MAP = {
    "state": {"address": 0, "type": "holding"},        # AGV 状态码
    "position_x": {"address": 1, "type": "holding"},   # X 坐标 (mm)
    "position_y": {"address": 2, "type": "holding"},   # Y 坐标 (mm)
    "battery": {"address": 3, "type": "holding"},      # 电量百分比
    "speed": {"address": 4, "type": "holding"},        # 速度 (mm/s)
    "error_code": {"address": 5, "type": "holding"},   # 错误码
    "command": {"address": 10, "type": "coil"},        # 指令线圈
    "target_node": {"address": 11, "type": "holding"}, # 目标节点
}


class ModbusVehicleAdapter(BaseVehicleAdapter):
    """
    Modbus TCP 车辆适配器.

    对接 PLC 控制的 AGV/输送线设备。
    """

    def __init__(
        self,
        mode: str = "simulation",
        plc_host: str = "192.168.1.100",
        plc_port: int = 502,
        unit_id: int = 1,
        register_map: Optional[Dict] = None,
        num_sim_agvs: int = 5,
        **kwargs,
    ):
        super().__init__(name="modbus", protocol="modbus-tcp")
        self.mode = mode
        self.plc_host = plc_host
        self.plc_port = plc_port
        self.unit_id = unit_id
        self.register_map = register_map or DEFAULT_REGISTER_MAP
        self.num_sim_agvs = num_sim_agvs

        self._client: Optional[Any] = None
        self._sim_agvs: Dict[str, VehicleStatus] = {}

    async def connect(self) -> bool:
        """连接 PLC 或初始化模拟器"""
        if self.mode == "live" and HAS_PYMODBUS:
            try:
                self._client = ModbusTcpClient(self.plc_host, port=self.plc_port)
                if self._client.connect():
                    self._connected = True
                    logger.info("Modbus TCP connected to %s:%d", self.plc_host, self.plc_port)
                else:
                    raise ConnectionError("Modbus connect returned False")
            except Exception as e:
                logger.error("Modbus connect failed: %s, falling back to simulation", e)
                self.mode = "simulation"
                self._init_simulation()
        else:
            self._init_simulation()

        self._vehicle_count = len(self._sim_agvs)
        return True

    def _init_simulation(self):
        """初始化模拟 PLC AGV"""
        self._connected = True
        for i in range(self.num_sim_agvs):
            vid = f"plc_agv_{i+1:03d}"
            self._sim_agvs[vid] = VehicleStatus(
                vehicle_id=vid,
                state=VehicleState.IDLE,
                battery_level=85.0 + (i % 15),
                x=float(i * 15),
                y=float(i * 8),
            )
        logger.info("Modbus simulation mode: %d AGVs", len(self._sim_agvs))

    async def disconnect(self) -> None:
        """断开连接"""
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        self._sim_agvs.clear()

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """下发控制指令 via Modbus"""
        params = params or {}
        ts = time.time()

        # 指令码映射
        cmd_codes = {
            VehicleCommand.MOVE: 1, VehicleCommand.STOP: 2,
            VehicleCommand.CHARGE: 3, VehicleCommand.LOAD: 4,
            VehicleCommand.UNLOAD: 5, VehicleCommand.CANCEL_TASK: 9,
        }
        cmd_code = cmd_codes.get(command, 0)

        if self.mode == "live" and self._client:
            try:
                reg = self.register_map["command"]
                if reg["type"] == "coil":
                    self._client.write_coil(reg["address"], True, slave=self.unit_id)
                else:
                    self._client.write_register(reg["address"], cmd_code, slave=self.unit_id)

                if command == VehicleCommand.MOVE and "target" in params:
                    target_reg = self.register_map["target_node"]
                    self._client.write_register(
                        target_reg["address"], int(params["target"]), slave=self.unit_id
                    )

                return CommandResult(
                    success=True, vehicle_id=vehicle_id,
                    command=command.value, message="Modbus command written",
                    timestamp=ts,
                )
            except Exception as e:
                return CommandResult(
                    success=False, vehicle_id=vehicle_id,
                    command=command.value, message=str(e), timestamp=ts,
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
        elif command == VehicleCommand.STOP:
            status.state = VehicleState.IDLE
            status.speed = 0.0
        elif command == VehicleCommand.CHARGE:
            status.state = VehicleState.CHARGING

        return CommandResult(
            success=True, vehicle_id=vehicle_id,
            command=command.value, message=f"Modbus sim: {command.value}",
            timestamp=ts,
        )

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """读取车辆状态 via Modbus"""
        if self.mode == "live" and self._client:
            try:
                regs = self._client.read_holding_registers(
                    0, 6, slave=self.unit_id
                )
                if regs and not regs.isError():
                    return VehicleStatus(
                        vehicle_id=vehicle_id,
                        state=VehicleState.IDLE,
                        x=float(regs.registers[1]),
                        y=float(regs.registers[2]),
                        battery_level=float(regs.registers[3]),
                        speed=float(regs.registers[4]),
                        error_code=regs.registers[5],
                    )
            except Exception as e:
                logger.debug("Modbus read error: %s", e)

        return self._sim_agvs.get(vehicle_id)

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有车辆状态"""
        return list(self._sim_agvs.values())
