"""
TCSObject 基类 — 参考 openTCS TCSObject<T> 继承体系.

openTCS 中所有核心实体 (Point/Path/Location/Vehicle) 继承自 TCSObject,
状态变更自动触发 ENTITY_CREATED / ENTITY_MODIFIED / ENTITY_REMOVED 事件。

AGV-TMS 适配: 为 Pydantic BaseModel 提供混入 (Mixin), 而非强制继承,
保持与现有 schemas.py 的兼容性。

用法:
    class MapNode(TCSObjectMixin, BaseModel):
        ...

    node = MapNode(id="N001", ...)
    node.touch()  # 触发 OBJECT_MODIFIED 事件
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TCSObjectMixin:
    """
    TCSObject 混入 — 为 Pydantic BaseModel 添加 openTCS 风格的事件通知.

    不破坏现有 BaseModel 继承链, 仅添加:
      - object_id / object_name / properties 通用属性 (可选)
      - touch() / create() / remove() 事件触发方法
      - 参考时间戳

    注意: 这是一个轻量级混入, 不强制要求子类实现特定接口。
    """

    # 子类可选覆盖
    _tcs_object_type: str = "TCSObject"

    @property
    def tcs_object_id(self) -> str:
        """对象唯一标识 (子类应覆盖, 默认尝试 id 字段)"""
        return getattr(self, "id", "") or getattr(self, "name", "") or str(id(self))

    @property
    def tcs_object_type(self) -> str:
        return self._tcs_object_type

    def to_tcs_dict(self) -> Dict[str, Any]:
        """转换为 TCSObject 风格的字典 (用于事件 payload)"""
        try:
            data = self.model_dump(mode="json")  # Pydantic v2
        except AttributeError:
            try:
                data = self.dict()  # Pydantic v1
            except AttributeError:
                data = {"repr": repr(self)}
        return {
            "object_type": self.tcs_object_type,
            "object_id": self.tcs_object_id,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

    def fire_created(self, source: str = "") -> None:
        """触发 OBJECT_CREATED 事件"""
        self._emit_event("object_created", source)

    def fire_modified(self, source: str = "") -> None:
        """触发 OBJECT_MODIFIED 事件"""
        self._emit_event("object_modified", source)

    def fire_removed(self, source: str = "") -> None:
        """触发 OBJECT_REMOVED 事件"""
        self._emit_event("object_removed", source)

    def touch(self, source: str = "") -> None:
        """标记对象已修改并触发事件 (别名)"""
        self.fire_modified(source)

    def _emit_event(self, event_type_str: str, source: str) -> None:
        """内部: 发出事件到事件总线"""
        try:
            from .event_bus import event_bus, Event, EventType

            event_type = EventType(event_type_str)
            event_bus.publish(Event(
                event_type=event_type,
                payload=self.to_tcs_dict(),
                source=source or f"{self.tcs_object_type}:{self.tcs_object_id}",
            ))
        except Exception as e:
            logger.debug("TCSObject event emit failed: %s", e)


# ==================== 对象类型常量 ====================

class TCSObjectType:
    """TCS 对象类型常量 (参考 openTCS)"""
    POINT = "Point"           # 地图节点
    PATH = "Path"             # 地图边/路径
    LOCATION = "Location"     # 目的地
    VEHICLE = "Vehicle"       # AGV
    TRANSPORT_ORDER = "TransportOrder"  # 运输订单
    BLOCK = "Block"           # 区域块
    ZONE = "Zone"             # 交通区域


# ==================== 辅助函数 ====================

def emit_object_event(
    object_type: str,
    object_id: str,
    event_type_str: str,
    data: Optional[Dict[str, Any]] = None,
    source: str = "",
) -> None:
    """
    独立函数: 为非 TCSObjectMixin 对象发出事件.

    用于普通 dict / dataclass 等无法混入的场景。
    """
    try:
        from .event_bus import event_bus, Event, EventType

        event_type = EventType(event_type_str)
        event_bus.publish(Event(
            event_type=event_type,
            payload={
                "object_type": object_type,
                "object_id": object_id,
                "data": data or {},
                "timestamp": datetime.now().isoformat(),
            },
            source=source or f"{object_type}:{object_id}",
        ))
    except Exception as e:
        logger.debug("emit_object_event failed: %s", e)
