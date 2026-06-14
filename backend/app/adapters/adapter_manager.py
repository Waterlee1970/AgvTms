"""
适配器注册表与管理器 — 参考 openTCS SPI 自动发现机制.

功能:
  1. AdapterRegistry: 注册/查找适配器类 (类级)
  2. AdapterManager: 管理运行中的适配器实例, 按 AGV ID 路由

支持多品牌混合调度:
  - AGV1 (海康) → OpcUaVehicleAdapter
  - AGV2 (仙工) → Vda5050VehicleAdapter
  - AGV3 (自研) → MqttVehicleAdapter

用法:
    # 注册适配器类
    AdapterRegistry.register("opcua", OpcUaVehicleAdapter)
    AdapterRegistry.register("vda5050", Vda5050VehicleAdapter)

    # 管理器创建实例并路由
    mgr = AdapterManager()
    await mgr.start_adapter("opcua", {"mode": "simulation"})
    result = await mgr.send_command("sim_agv_001", VehicleCommand.MOVE, {"target": "N042"})
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from .base_adapter import BaseVehicleAdapter, CommandResult, VehicleCommand, VehicleStatus

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """
    适配器类注册表 (静态).

    参考 openTCS SPI: 通过名称查找适配器实现类。
    """

    _registry: Dict[str, Type[BaseVehicleAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[BaseVehicleAdapter]) -> None:
        """注册适配器类"""
        if not issubclass(adapter_class, BaseVehicleAdapter):
            raise TypeError(f"{adapter_class} must inherit BaseVehicleAdapter")
        cls._registry[name] = adapter_class
        logger.info("Registered vehicle adapter: %s → %s", name, adapter_class.__name__)

    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseVehicleAdapter]]:
        """获取已注册的适配器类"""
        return cls._registry.get(name)

    @classmethod
    def list_adapters(cls) -> List[str]:
        """列出所有已注册的适配器名称"""
        return list(cls._registry.keys())

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseVehicleAdapter:
        """创建适配器实例"""
        adapter_class = cls._registry.get(name)
        if not adapter_class:
            raise KeyError(f"Adapter '{name}' not registered. Available: {cls.list_adapters()}")
        return adapter_class(**kwargs)


class AdapterManager:
    """
    适配器实例管理器 — 统一入口, 支持多品牌混合.

    职责:
      1. 启动/停止适配器实例
      2. 维护 AGV ID → 适配器实例 的路由表
      3. 统一指令下发接口 (自动路由到正确适配器)
    """

    def __init__(self):
        self._adapters: Dict[str, BaseVehicleAdapter] = {}  # name → instance
        self._vehicle_routes: Dict[str, str] = {}  # vehicle_id → adapter_name

    async def start_adapter(self, name: str, config: Dict[str, Any]) -> BaseVehicleAdapter:
        """创建并启动适配器"""
        if name in self._adapters and self._adapters[name].is_connected:
            raise RuntimeError(f"Adapter '{name}' already running")

        adapter = AdapterRegistry.create(name, **config)
        await adapter.connect()
        self._adapters[name] = adapter

        # 自动注册该适配器下的所有车辆到路由表
        statuses = await adapter.get_all_statuses()
        for status in statuses:
            self._vehicle_routes[status.vehicle_id] = name

        logger.info(
            "Started adapter '%s' (protocol=%s, vehicles=%d)",
            name, adapter.protocol, len(statuses)
        )
        return adapter

    async def stop_adapter(self, name: str) -> None:
        """停止适配器"""
        adapter = self._adapters.get(name)
        if adapter:
            await adapter.disconnect()
            # 清理路由表
            self._vehicle_routes = {
                vid: aname for vid, aname in self._vehicle_routes.items() if aname != name
            }
            del self._adapters[name]
            logger.info("Stopped adapter '%s'", name)

    async def stop_all(self) -> None:
        """停止所有适配器"""
        names = list(self._adapters.keys())
        for name in names:
            await self.stop_adapter(name)

    def register_vehicle_route(self, vehicle_id: str, adapter_name: str) -> None:
        """手动注册车辆路由 (用于跨品牌混合调度)"""
        self._vehicle_routes[vehicle_id] = adapter_name
        logger.debug("Routed vehicle %s → adapter %s", vehicle_id, adapter_name)

    def get_adapter_for_vehicle(self, vehicle_id: str) -> Optional[BaseVehicleAdapter]:
        """获取车辆对应的适配器实例"""
        adapter_name = self._vehicle_routes.get(vehicle_id)
        if adapter_name:
            return self._adapters.get(adapter_name)
        return None

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """统一指令下发 (自动路由)"""
        adapter = self.get_adapter_for_vehicle(vehicle_id)
        if not adapter:
            return CommandResult(
                success=False,
                vehicle_id=vehicle_id,
                command=command.value,
                message=f"No adapter found for vehicle {vehicle_id}",
            )
        return await adapter.send_command(vehicle_id, command, params)

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取车辆状态 (自动路由)"""
        adapter = self.get_adapter_for_vehicle(vehicle_id)
        if adapter:
            return await adapter.get_status(vehicle_id)
        return None

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有适配器下所有车辆状态"""
        all_statuses: List[VehicleStatus] = []
        for adapter in self._adapters.values():
            all_statuses.extend(await adapter.get_all_statuses())
        return all_statuses

    async def emergency_stop(self, vehicle_id: Optional[str] = None) -> Dict[str, CommandResult]:
        """紧急停止"""
        if vehicle_id:
            adapter = self.get_adapter_for_vehicle(vehicle_id)
            if adapter:
                return await adapter.emergency_stop(vehicle_id)
            return {}
        # 全部紧急停止
        results: Dict[str, CommandResult] = {}
        for adapter in self._adapters.values():
            results.update(await adapter.emergency_stop())
        return results

    def get_info(self) -> Dict[str, Any]:
        """获取管理器信息"""
        return {
            "adapters": {
                name: {
                    "protocol": adapter.protocol,
                    "connected": adapter.is_connected,
                    "vehicle_count": adapter.vehicle_count,
                }
                for name, adapter in self._adapters.items()
            },
            "vehicle_routes": dict(self._vehicle_routes),
            "total_vehicles": len(self._vehicle_routes),
        }


# ==================== 全局单例 ====================

adapter_manager = AdapterManager()
