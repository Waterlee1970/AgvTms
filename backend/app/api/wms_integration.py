"""
WMS/MES 系统集成接口层 — 标准 ERP/WMS/MES 对接适配器

支持的接口规范:
  - WMS (Warehouse Management System): 入库/出库/盘点/移库
  - MES (Manufacturing Execution System): 生产工单→搬运任务转换
  - ERP 对接: 通用 REST Webhook

数据格式: JSON over REST API (异步队列解耦)
参考标准: 
  - GB/T 34389-2017 (物流信息系统数据交换)
  - VDA 4998 (汽车工业物料管理接口)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Awaitable

from fastapi import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ==================== 枚举与常量 ====================

class WmsOrderType(str, Enum):
    """WMS 订单类型"""
    INBOUND = "inbound"           # 入库 (收货上架)
    OUTBOUND = "outbound"         # 出库 (拣选下架)
    INVENTORY = "inventory"       # 盘点
    MOVE = "move"                 # 移库 (库位调整)
    PICK = "pick"                 # 拣货
    REPLENISH = "replenish"       # 补货
    RETURN = "return"             # 退料


class WmsOrderStatus(str, Enum):
    """WMS 订单状态"""
    PENDING = "pending"           # 待处理
    CONFIRMED = "confirmed"       # 已确认
    PROCESSING = "processing"     # 执行中
    COMPLETED = "completed"       # 已完成
    CANCELLED = "cancelled"       # 已取消
    PARTIAL = "partial"           # 部分完成
    EXCEPTION = "exception"       # 异常


class PriorityLevel(str, Enum):
    """优先级等级"""
    URGENT = 1      # 紧急 (立即执行)
    HIGH = 2        # 高优先
    NORMAL = 3      # 普通
    LOW = 4         # 低优先 (空闲时执行)
    BATCH = 5       # 批量 (可合并)


# ==================== Pydantic 请求/响应模型 ====================

class WmsLineItem(BaseModel):
    """WMS 订单明细行"""
    line_no: int = Field(description="行号")
    sku: str = Field(description="SKU / 物料编码")
    quantity: float = Field(gt=0, description="数量")
    source_location: str = Field(description="源位置/库位")
    target_location: str = Field(description="目标位置/库位")
    weight_kg: Optional[float] = Field(None, description="重量 kg")
    priority: int = Field(default=3, ge=1, le=5, description="优先级 1-5")


class WmsOrderRequest(BaseModel):
    """WMS 入站订单请求"""
    order_type: WmsOrderType
    external_order_id: str = Field(description="上游系统订单号 (唯一)")
    warehouse_zone: str = Field(default="default", description="库区")
    priority: int = Field(default=3, ge=1, le=5)
    items: List[WmsLineItem] = Field(min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    requested_at: Optional[str] = Field(None, description="ISO8601 时间")


class MesWorkOrder(BaseModel):
    """MES 生产工单"""
    work_order_id: str
    product_sku: str
    production_line: str
    quantity: int
    source_station: str          # 原材料出库点
    target_station: str          # 产线上料点
    priority: int = Field(default=3, ge=1, le=5)
    start_time: Optional[str] = None
    due_time: Optional[str] = None


# ==================== 内部数据模型 ====================

@dataclass
class InternalTask:
    """系统内部任务 (从 WMS/MES 转换而来)"""
    task_id: str
    external_id: str            # 上游系统单号
    source: str                  # 来源: wms_inbound / wms_outbound / mes_production
    pickup_node: str             # 取货节点
    dropoff_node: str            # 卸货节点
    priority: int
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    status: str = "pending"      # pending / assigned / executing / completed / failed
    assigned_agv: Optional[str] = None


@dataclass
class OrderAckResponse:
    """订单确认响应"""
    success: bool
    internal_task_ids: List[str] = field(default_factory=list)
    external_order_id: str = ""
    message: str = ""
    timestamp: float = field(default_factory=time.time)


# ==================== 位置解析器 ====================

class LocationResolver:
    """
    将 WMS/MES 的位置标识 (库位/工位/站点) 映射为地图节点 ID。

    支持规则:
    1. 直接匹配: location_id == node_id
    2. 前缀匹配: "A-01-03" → "zone_A_rack_01_03"
    3. 区域查找: "charging_area" → 类型为 charge 的最近节点
    4. 自定义映射表
    """

    def __init__(self):
        self._mapping: Dict[str, str] = {}  # external_loc → internal_node_id
        self._reverse_mapping: Dict[str, str] = {}

    def load_from_nodes(self, nodes: List[Dict[str, Any]]) -> int:
        """从地图节点加载映射关系"""
        count = 0
        for n in nodes:
            nid = n.get("id", "")
            name = n.get("name", "")
            raw_type = n.get("type", "path")
            ntype = getattr(raw_type, "value", "") if hasattr(raw_type, "value") else str(raw_type)

            self._mapping[nid] = nid
            if name:
                self._mapping[name] = nid

            # 库位规则: A-01-03 → node
            if "-" in nid:
                self._mapping[nid] = nid

            # 按类型的快捷映射
            type_aliases = {
                "pickup": ["pickup", "inbound", "receive"],
                "dropoff": ["dropoff", "outbound", "ship"],
                "charge": ["charge", "charging", "dock"],
            }
            for alias_list in type_aliases.get(ntype, []):
                self._mapping[alias_list] = nid

            count += 1

        self._reverse_mapping = {v: k for k, v in self._mapping.items()}
        logger.info(f"[LocationResolver] Loaded {count} node mappings")
        return count

    def add_mapping(self, external: str, internal: str):
        self._mapping[external] = internal
        self._reverse_mapping[internal] = external

    def resolve(self, location_str: str) -> Optional[str]:
        """解析外部位置为内部节点 ID"""
        if not location_str:
            return None
        # 精确匹配
        if location_str in self._mapping:
            return self._mapping[location_str]
        # 忽略大小写
        loc_lower = location_str.lower()
        for k, v in self._mapping.items():
            if k.lower() == loc_lower:
                return v
        # 包含匹配
        for k, v in self._mapping.items():
            if loc_lower in k.lower() or k.lower() in loc_lower:
                return v
        logger.warning(f"[LocationResolver] Cannot resolve location: {location_str}")
        return None

    def reverse_resolve(self, node_id: str) -> str:
        """反向查找: 节点ID → 外部位置名"""
        return self._reverse_mapping.get(node_id, node_id)


# ==================== 主集成服务 ====================

class WmsIntegrationService:
    """
    WMS/MES 集成服务。

    功能:
    1. 接收 WMS 订单 → 转换为内部 AGV 任务
    2. 接收 MES 工单 → 转换为搬运任务
    3. 任务生命周期管理
    4. 状态回写至上游系统
    5. 异常处理和补偿
    """

    def __init__(self):
        self.location_resolver = LocationResolver()
        self._tasks: Dict[str, InternalTask] = {}
        self._order_history: List[Dict[str, Any]] = []
        self._status_callbacks: List[Callable[[InternalTask], Awaitable[None]]] = []
        self._max_history = 1000

        # 统计
        self._stats = {
            "orders_received": 0,
            "tasks_created": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
        }

    def initialize(self, nodes: List[Dict[str, Any]]) -> None:
        """用地图节点初始化位置解析器"""
        self.location_resolver.load_from_nodes(nodes)

    def on_task_completed(self, callback: Callable[[InternalTask], Awaitable[None]]):
        """注册任务完成回调"""
        self._status_callbacks.append(callback)

    # ==================== WMS 订单接收 ====================

    def receive_wms_order(self, req: WmsOrderRequest) -> OrderAckResponse:
        """
        接收 WMS 订单并转换为内部任务.

        转换规则:
          - inbound:  source=收货口 → target=上架库位
          - outbound: source=存储库位 → target=拣选区/发货口
          - move:     source=当前库位 → target=目标库位
          - inventory: source=盘点起点 → target=终点
        """
        self._stats["orders_received"] += 1
        task_ids = []

        try:
            for item in req.items:
                task_id = f"wms_{uuid.uuid4().hex[:12]}"

                # 位置解析
                pickup = self.location_resolver.resolve(item.source_location)
                dropoff = self.location_resolver.resolve(item.target_location)

                if not pickup or not dropoff:
                    logger.warning(
                        f"[WMS] Location resolution failed: "
                        f"'{item.source_location}'→{pickup}, '{item.target_location}'→{dropoff}"
                    )

                task = InternalTask(
                    task_id=task_id,
                    external_id=req.external_order_id,
                    source=f"wms_{req.order_type.value}",
                    pickup_node=pickup or item.source_location,
                    dropoff_node=dropoff or item.target_location,
                    priority=item.priority or req.priority,
                    payload={
                        "sku": item.sku,
                        "quantity": item.quantity,
                        "weight_kg": item.weight_kg,
                        "line_no": item.line_no,
                        "order_type": req.order_type.value,
                        "warehouse_zone": req.warehouse_zone,
                        **req.metadata,
                    },
                )
                self._tasks[task_id] = task
                task_ids.append(task_id)
                self._stats["tasks_created"] += 1

            self._record_order({
                "external_id": req.external_order_id,
                "order_type": req.order_type.value,
                "item_count": len(req.items),
                "task_ids": task_ids,
                "received_at": datetime.now().isoformat(),
                "status": "accepted",
            })

            logger.info(
                f"[WMS] Order {req.external_order_id} accepted: "
                f"{len(req.items)} items → {len(task_ids)} tasks"
            )

            return OrderAckResponse(
                success=True,
                internal_task_ids=task_ids,
                external_order_id=req.external_order_id,
                message=f"Accepted, created {len(task_ids)} tasks",
            )

        except Exception as e:
            logger.error(f"[WMS] Order processing error: {e}", exc_info=True)
            return OrderAckResponse(
                success=False,
                external_order_id=req.external_order_id,
                message=f"Processing error: {str(e)}",
            )

    def receive_mes_work_order(self, wo: MesWorkOrder) -> OrderAckResponse:
        """
        接收 MES 生产工单并转换为搬运任务.
        
        一个生产工单可能产生多个搬运任务:
          1. 原材料库 → 产线上料点 (供料)
          2. 产线下料点 → 成品库/质检区 (成品转运)
        """
        self._stats["orders_received"] += 1
        task_ids = []

        try:
            task_id = f"mes_{uuid.uuid4().hex[:12]}"

            pickup = self.location_resolver.resolve(wo.source_station)
            dropoff = self.location_resolver.resolve(wo.target_station)

            task = InternalTask(
                task_id=task_id,
                external_id=wo.work_order_id,
                source="mes_production",
                pickup_node=pickup or wo.source_station,
                dropoff_node=dropoff or wo.target_station,
                priority=wo.priority,
                payload={
                    "product_sku": wo.product_sku,
                    "quantity": wo.quantity,
                    "production_line": wo.production_line,
                    "start_time": wo.start_time,
                    "due_time": wo.due_time,
                },
            )
            self._tasks[task_id] = task
            task_ids.append(task_id)
            self._stats["tasks_created"] += 1

            self._record_order({
                "external_id": wo.work_order_id,
                "order_type": "mes_work_order",
                "product_sku": wo.product_sku,
                "task_ids": task_ids,
                "received_at": datetime.now().isoformat(),
                "status": "accepted",
            })

            logger.info(f"[MES] Work order {wo.work_order_id} accepted → {task_id}")
            return OrderAckResponse(
                success=True, internal_task_ids=task_ids,
                external_order_id=wo.work_order_id,
                message=f"MES work order converted to {len(task_ids)} tasks",
            )

        except Exception as e:
            logger.error(f"[MES] Processing error: {e}", exc_info=True)
            return OrderAckResponse(
                success=False, external_order_id=wo.work_order_id,
                message=str(e),
            )

    # ==================== 任务查询与管理 ====================

    def get_pending_tasks(self, limit: int = 50) -> List[InternalTask]:
        """获取待处理的任务列表 (按优先级排序)"""
        pending = [t for t in self._tasks.values() if t.status == "pending"]
        pending.sort(key=lambda t: (t.priority, t.created_at))
        return pending[:limit]

    def get_task_by_external_id(self, external_id: str) -> List[InternalTask]:
        """根据上游订单号查找关联任务"""
        return [t for t in self._tasks.values() if t.external_id == external_id]

    def mark_task_assigned(self, task_id: str, agv_id: str) -> bool:
        """标记任务已分配给 AGV"""
        task = self._tasks.get(task_id)
        if task and task.status == "pending":
            task.status = "assigned"
            task.assigned_agv = agv_id
            return True
        return False

    def mark_task_started(self, task_id: str) -> bool:
        """标记任务开始执行"""
        task = self._tasks.get(task_id)
        if task and task.status in ("assigned", "pending"):
            task.status = "executing"
            return True
        return False

    def mark_task_completed(self, task_id: str) -> bool:
        """标记任务完成"""
        task = self._tasks.get(task_id)
        if task:
            task.status = "completed"
            self._stats["tasks_completed"] += 1
            # 触发回调
            for cb in self._status_callbacks:
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(cb):
                        asyncio.create_task(cb(task))
                    else:
                        cb(task)
                except Exception as e:
                    logger.debug(f"Completion callback error: {e}")
            return True
        return False

    def mark_task_failed(self, task_id: str, reason: str = "") -> bool:
        """标记任务失败"""
        task = self._tasks.get(task_id)
        if task:
            task.status = "failed"
            task.payload["failure_reason"] = reason
            self._stats["tasks_failed"] += 1
            return True
        return False

    def convert_to_scenario_tasks(self, task_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        将内部任务转换为评测框架兼容的场景任务格式.

        用于直接喂给 AlgorithmBenchmark 的 evaluate_single API.
        """
        source = task_ids or list(self._tasks.keys())
        scenario_tasks = []

        for tid in source:
            task = self._tasks.get(tid)
            if not task or task.status in ("completed", "failed"):
                continue

            scenario_tasks.append({
                "id": task.task_id,
                "task_id": task.external_id or task.task_id,
                "pickup_node": task.pickup_node,
                "pickup_node_id": task.pickup_node,
                "dropoff_node": task.dropoff_node,
                "dropoff_node_id": task.dropoff_node,
                "priority": task.priority,
                "status": task.status,
                **{k: v for k, v in task.payload.items()
                   if k in ("sku", "quantity", "weight_kg")},
            })

        return scenario_tasks

    # ==================== 状态回写 ====================

    def generate_completion_report(self, task_id: str) -> Dict[str, Any]:
        """生成任务完成报告 (用于回写给 WMS/MES)"""
        task = self._tasks.get(task_id)
        if not task:
            return {"error": "Task not found"}

        return {
            "task_id": task.task_id,
            "external_order_id": task.external_id,
            "source": task.source,
            "status": task.status,
            "assigned_agv": task.assigned_agv,
            "completed_at": datetime.now().isoformat() if task.status == "completed" else None,
            "payload": task.payload,
        }

    # ==================== 统计与历史 ====================

    @property
    def stats(self) -> Dict[str, Any]:
        """获取集成服务统计信息"""
        return {
            **self._stats,
            "active_tasks": sum(1 for t in self._tasks.values() if t.status in ("pending", "assigned", "executing")),
            "total_tasks": len(self._tasks),
            "completion_rate": (
                round(self._stats["tasks_completed"] / max(self._stats["tasks_created"], 1) * 100, 1)
                if self._stats["tasks_created"] > 0 else 0
            ),
        }

    def get_recent_orders(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近的订单历史"""
        return self._order_history[-limit:]

    def _record_order(self, record: Dict[str, Any]):
        """记录订单到历史"""
        self._order_history.append(record)
        if len(self._order_history) > self._max_history:
            self._order_history = self._order_history[-self._max_history:]
