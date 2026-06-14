"""
OPC UA 车辆适配器 — 继承 BaseVehicleAdapter, 封装现有 OpcUaAdapter.

将已有的 OpcUaAdapter (450+ 行, 含模拟器) 适配为统一 BaseVehicleAdapter 接口。
不修改原有 OpcUaAdapter 代码, 仅做薄封装。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_adapter import (
    BaseVehicleAdapter,
    CommandResult,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
)
from .opcua_adapter import (
    AgvCommand,
    AgvState,
    OpcUaAdapter,
    OpcUaCommandResult,
)

logger = logging.getLogger(__name__)


# ==================== 状态/指令映射 ====================

_OPCUA_STATE_TO_VEHICLE: Dict[AgvState, VehicleState] = {
    AgvState.IDLE: VehicleState.IDLE,
    AgvState.MOVING: VehicleState.MOVING,
    AgvState.LOADING: VehicleState.LOADING,
    AgvState.UNLOADING: VehicleState.UNLOADING,
    AgvState.CHARGING: VehicleState.CHARGING,
    AgvState.ERROR: VehicleState.ERROR,
    AgvState.MAINTENANCE: VehicleState.MAINTENANCE,
    AgvState.OFFLINE: VehicleState.OFFLINE,
}

_VEHICLE_CMD_TO_OPCUA: Dict[VehicleCommand, AgvCommand] = {
    VehicleCommand.MOVE: AgvCommand.MOVE,
    VehicleCommand.STOP: AgvCommand.STOP,
    VehicleCommand.RESUME: AgvCommand.RESUME,
    VehicleCommand.CHARGE: AgvCommand.CHARGE,
    VehicleCommand.LOAD: AgvCommand.LOAD,
    VehicleCommand.UNLOAD: AgvCommand.UNLOAD,
    VehicleCommand.CANCEL_TASK: AgvCommand.CANCEL_TASK,
}


class OpcUaVehicleAdapter(BaseVehicleAdapter):
    """
    OPC UA 车辆适配器 — 统一接口封装.

    内部委托给 OpcUaAdapter 实现, 对外暴露 BaseVehicleAdapter 接口。
    """

    def __init__(
        self,
        mode: str = "simulation",
        num_sim_agvs: int = 10,
        move_speed: float = 1.5,
        server_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(name="opcua", protocol="opc-ua")
        self._inner = OpcUaAdapter(
            mode=mode,
            num_sim_agvs=num_sim_agvs,
            move_speed=move_speed,
            server_url=server_url,
        )

    async def connect(self) -> bool:
        """连接 (初始化 + 启动)"""
        try:
            await self._inner.initialize()
            await self._inner.start()
            self._connected = True
            self._vehicle_count = self._inner.connected_agv_count
            return True
        except Exception as e:
            logger.error("OPC UA connect failed: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        await self._inner.stop()
        self._connected = False
        self._vehicle_count = 0

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """下发控制指令"""
        opcua_cmd = _VEHICLE_CMD_TO_OPCUA.get(command)
        if opcua_cmd is None:
            # VDA5050 专有指令, OPC UA 不支持
            return CommandResult(
                success=False,
                vehicle_id=vehicle_id,
                command=command.value,
                message=f"OPC UA adapter does not support command '{command.value}'",
            )

        result: OpcUaCommandResult = await self._inner.send_command(
            vehicle_id, opcua_cmd, params or {}
        )
        return CommandResult(
            success=result.success,
            vehicle_id=result.agv_id,
            command=result.command,
            message=result.message,
            timestamp=result.timestamp,
        )

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取单个车辆状态"""
        agv_status = await self._inner.get_agv_status(vehicle_id)
        if agv_status is None:
            return None
        return self._convert_status(agv_status)

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有车辆状态"""
        all_agv = await self._inner.get_all_agv_statuses()
        return [self._convert_status(a) for a in all_agv]

    def _convert_status(self, agv_status) -> VehicleStatus:
        """将 OpcUa AgvDeviceStatus 转换为统一 VehicleStatus"""
        vehicle_state = _OPCUA_STATE_TO_VEHICLE.get(
            agv_status.state, VehicleState.IDLE
        )
        return VehicleStatus(
            vehicle_id=agv_status.agv_id,
            state=vehicle_state,
            x=agv_status.x,
            y=agv_status.y,
            angle=agv_status.angle,
            speed=agv_status.speed,
            battery_level=agv_status.battery_level,
            current_node=agv_status.current_node_id,
            target_node=agv_status.target_node_id,
            load_status=agv_status.load_status,
            error_code=agv_status.error_code,
            error_message=agv_status.error_message,
            last_heartbeat=agv_status.last_heartbeat,
            odometer=agv_status.odometer,
            operating_hours=agv_status.operating_hours,
        )

    def to_api_format(self, status: VehicleStatus) -> Dict[str, Any]:
        """转换为 API 兼容格式 (复用 OpcUaAdapter.to_api_format 逻辑)"""
        return status.to_dict()
