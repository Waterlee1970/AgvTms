"""
AGV-TMS 适配器层 — 统一车辆适配器接口 (Phase 3 协议深化版).

支持 9 种工业通信协议:
  1. REST API (HTTP)     — FastAPI 路由
  2. WebSocket           — 实时数据推送
  3. OPC UA              — 工业自动化标准 (asyncua)
  4. MQTT                — 消息总线 (paho-mqtt)
  5. VDA5050             — AGV 通信国际标准 (v2.0)
  6. Modbus TCP/RTU      — PLC 设备控制 (pymodbus)
  7. HTTP/REST           — AGV Web 接口 (aiohttp)
  8. TCP Socket          — 二进制自定义协议
  9. Redis Pub/Sub       — 分布式消息

参考 openTCS CommAdapter + SPI 机制.
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
from .mqtt_vehicle_adapter import MqttVehicleAdapter, MqttConnectionConfig, MqttQoS, Vda5050Topics
from .modbus_vehicle_adapter import ModbusVehicleAdapter, ModbusMode, RegisterDefinition
from .vda5050_vehicle_adapter import Vda5050VehicleAdapter
from .http_vehicle_adapter import HttpVehicleAdapter, HttpAdapterConfig
from .tcp_vehicle_adapter import TcpVehicleAdapter, TcpFrameType, TcpFrame, TcpAdapterConfig

# 自动注册内置适配器 (9种协议)
AdapterRegistry.register("opcua", OpcUaVehicleAdapter)
AdapterRegistry.register("mqtt", MqttVehicleAdapter)
AdapterRegistry.register("modbus", ModbusVehicleAdapter)
AdapterRegistry.register("vda5050", Vda5050VehicleAdapter)
AdapterRegistry.register("http", HttpVehicleAdapter)        # 新增 #7
AdapterRegistry.register("tcp", TcpVehicleAdapter)          # 新增 #8

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
    # 协议适配器
    "OpcUaVehicleAdapter",
    "MqttVehicleAdapter", 
    "ModbusVehicleAdapter",
    "Vda5050VehicleAdapter",
    "HttpVehicleAdapter",          # 新增
    "TcpVehicleAdapter",            # 新增
    # 配置类
    "MqttConnectionConfig",
    "MqttQoS",
    "Vda5050Topics",
    "HttpAdapterConfig",
    "TcpAdapterConfig",
    "ModbusMode",
    "RegisterDefinition",
    "TcpFrameType",
    "TcpFrame",
]
