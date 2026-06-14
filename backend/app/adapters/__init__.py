"""
AGV-TMS 适配器层 — 统一车辆适配器接口.

参考 openTCS CommAdapter + SPI 机制:
  - base_adapter: 统一抽象接口
  - adapter_manager: 多品牌混合调度管理
  - opcua_vehicle_adapter: OPC UA 协议适配
  - mqtt_vehicle_adapter: MQTT 消息总线适配 (Phase 6)
  - modbus_vehicle_adapter: Modbus TCP PLC 适配 (Phase 7)
  - vda5050_vehicle_adapter: VDA5050 标准协议适配 (Phase 7)
"""

from .base_adapter import (
    BaseVehicleAdapter,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
    CommandResult,
    TransportOrderMessage,
)
from .adapter_manager import AdapterRegistry, AdapterManager, adapter_manager
from .opcua_vehicle_adapter import OpcUaVehicleAdapter
from .mqtt_vehicle_adapter import MqttVehicleAdapter
from .modbus_vehicle_adapter import ModbusVehicleAdapter
from .vda5050_vehicle_adapter import Vda5050VehicleAdapter

# 自动注册内置适配器
AdapterRegistry.register("opcua", OpcUaVehicleAdapter)
AdapterRegistry.register("mqtt", MqttVehicleAdapter)
AdapterRegistry.register("modbus", ModbusVehicleAdapter)
AdapterRegistry.register("vda5050", Vda5050VehicleAdapter)

__all__ = [
    "BaseVehicleAdapter",
    "VehicleCommand",
    "VehicleState",
    "VehicleStatus",
    "CommandResult",
    "TransportOrderMessage",
    "AdapterRegistry",
    "AdapterManager",
    "adapter_manager",
    "OpcUaVehicleAdapter",
    "MqttVehicleAdapter",
    "ModbusVehicleAdapter",
    "Vda5050VehicleAdapter",
]
