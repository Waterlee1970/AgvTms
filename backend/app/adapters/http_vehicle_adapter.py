"""
HTTP/Webhook AGV 适配器 — Phase 3 协议深化 (新增协议 #7).

支持场景:
  - 海康 RCS HTTP API
  - 国产AGV Web接口
  - 第三方系统 WebHook 回调
  - RESTful AGV 控制器

特性:
  - 同步/异步请求模式
  - 自动重试与超时控制
  - Webhook 签名验证
  - 心跳/健康检测
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from .base_adapter import (
    BaseVehicleAdapter,
    CommandResult,
    TransportOrderMessage,
    VehicleCommand,
    VehicleState,
    VehicleStatus,
)

logger = logging.getLogger(__name__)


# ==================== 配置模型 ====================

class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


@dataclass 
class HttpAdapterConfig:
    """HTTP 适配器配置"""
    base_url: str = "http://localhost:8080/api"
    api_key: Optional[str] = None          # API Key 认证
    api_key_header: str = "X-API-Key"     # API Key 头名称
    
    timeout: float = 10.0                  # 请求超时 (秒)
    max_retries: int = 3                   # 最大重试次数
    retry_delay: float = 1.0               # 重试延迟 (秒)
    
    webhook_secret: Optional[str] = None   # Webhook 签名密钥
    webhook_path: str = "/webhook/agv"     # Webhook 接收路径
    
    heartbeat_interval: float = 30.0       # 心跳间隔 (秒)


@dataclass 
class HttpHealthMetrics:
    """HTTP 健康指标"""
    requests_sent: int = 0
    requests_succeeded: int = 0
    requests_failed: int = 0
    avg_response_ms: float = 0.0
    webhooks_received: int = 0
    last_webhook_time: float = 0.0
    _latency_samples: List[float] = field(default_factory=list)
    
    def record_request(self, success: bool, latency_ms: float = 0):
        self.requests_sent += 1
        if success:
            self.requests_succeeded += 1
            self._record_latency(latency_ms)
        else:
            self.requests_failed += 1
            
    def record_webhook(self):
        self.webhooks_received += 1
        self.last_webhook_time = time.time()
    
    def _record_latency(self, ms: float):
        self._latency_samples.append(ms)
        if len(self._latency_samples) > 50:
            self._latency_samples.pop(0)
        self.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)
    
    def to_dict(self) -> Dict:
        total = self.requests_sent or 1
        return {
            "requests_sent": self.requests_sent,
            "success_rate": round(self.requests_succeeded / total, 4),
            "failure_rate": round(self.requests_failed / total, 4),
            "avg_response_ms": round(self.avg_latency_ms, 2),
            "webhooks_received": self.webhooks_received,
            "last_webhook_time": self.last_webhook_time,
        }


# ==================== 主适配器类 ====================

class HttpVehicleAdapter(BaseVehicleAdapter):
    """
    HTTP/REST 车辆适配器.
    
    支持模式:
      - live: 通过 HTTP 与真实 AGV 控制器通信
      - simulation: 内存模拟
    """
    
    def __init__(
        self,
        mode: str = "simulation",
        config: Optional[HttpAdapterConfig] = None,
        num_sim_agvs: int = 5,
        **kwargs,
    ):
        super().__init__(name="http", protocol="http")
        self.mode = mode
        self.config = config or HttpAdapterConfig()
        self.num_sim_agvs = num_sim_agvs
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._sim_agvs: Dict[str, VehicleStatus] = {}
        self._webhook_handlers: Dict[str, Callable] = {}
        self.metrics = HttpHealthMetrics()
        
        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """创建 HTTP 会话或初始化模拟"""
        if self.mode == "live":
            try:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    headers=self._build_headers(),
                )
                self._connected = True
                
                # 启动心跳
                if self.config.heartbeat_interval > 0:
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    
                logger.info("HTTP adapter connected to %s", self.config.base_url)
                
                # 测试连通性
                await self._test_connection()
                return True
                
            except Exception as e:
                logger.error("HTTP connect failed: %s, falling back to simulation", e)
                self.mode = "simulation"
                self._init_simulation()
                return True
        else:
            self._init_simulation()
            return True

    def _build_headers(self) -> Dict[str, str]:
        """构建认证头"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AGV-TMS-HTTP-Adapter/2.0",
        }
        if self.config.api_key:
            headers[self.config.api_key_header] = self.config.api_key
        return headers

    async def _test_connection(self):
        """测试连接可用性"""
        try:
            async with self._session.get(
                f"{self.config.base_url}/health",
                headers=self._build_headers(),
            ) as resp:
                logger.info("HTTP health check: %d %s", resp.status, resp.reason)
        except Exception as e:
            logger.warning("HTTP connection test failed: %s", e)

    def _init_simulation(self):
        """初始化模拟 AGV"""
        self._connected = True
        for i in range(self.num_sim_agvs):
            vid = f"http_agv_{i+1:03d}"
            self._sim_agvs[vid] = VehicleStatus(
                vehicle_id=vid,
                state=VehicleState.IDLE,
                battery_level=90.0 + (i % 10),
                x=float(i * 20),
                y=float(i * 12),
            )
        logger.info("HTTP simulation mode: %d AGVs", len(self._sim_agvs))

    async def disconnect(self) -> None:
        """断开连接"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
            
        if self._session:
            await self._session.close()
            self._session = None
            
        self._connected = False
        self._sim_agvs.clear()
        logger.info("HTTP adapter disconnected")

    async def send_command(
        self,
        vehicle_id: str,
        command: VehicleCommand,
        params: Optional[Dict[str, Any]] = None,
    ) -> CommandResult:
        """通过 HTTP 发送指令"""
        params = params or {}
        ts = time.time()

        if self.mode == "live" and self._session:
            return await self._send_http_command(vehicle_id, command, params, ts)
            
        return self._simulate_command(vehicle_id, command, params, ts)

    async def _send_http_command(
        self, vehicle_id: str, command: VehicleCommand, params: Dict, ts: float
    ) -> CommandResult:
        """发送 HTTP 指令请求"""
        endpoint = f"{self.config.base_url}/agv/{vehicle_id}/command"
        payload = {
            "command": command.value,
            "parameters": params,
            "timestamp": ts,
        }
        
        last_error = None
        
        for attempt in range(self.config.max_retries + 1):
            try:
                start = time.time()
                
                async with self._session.post(
                    endpoint,
                    json=payload,
                    headers=self._build_headers(),
                ) as resp:
                    latency = (time.time() - start) * 1000
                    
                    if resp.status == 200:
                        data = await resp.json()
                        self.metrics.record_request(True, latency)
                        
                        return CommandResult(
                            success=data.get("success", True),
                            vehicle_id=vehicle_id,
                            command=command.value,
                            message=data.get("message", "OK"),
                            timestamp=ts,
                            data={"status_code": resp.status, **data},
                        )
                    elif resp.status == 404:
                        return CommandResult(
                            success=False, vehicle_id=vehicle_id,
                            command=command.value, message=f"AGV {vehicle_id} not found",
                            timestamp=ts,
                        )
                    else:
                        last_error = f"HTTP {resp.status}: {resp.reason}"
                        
            except asyncio.TimeoutError:
                last_error = "Request timeout"
            except Exception as e:
                last_error = str(e)
                
            if attempt < self.config.max_retries:
                await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                
        self.metrics.record_request(False)
        return CommandResult(
            success=False, vehicle_id=vehicle_id,
            command=command.value, message=f"Failed after retries: {last_error}",
            timestamp=ts,
        )

    def _simulate_command(
        self, vehicle_id: str, command: VehicleCommand, params: Dict, ts: float
    ) -> CommandResult:
        """模拟指令执行"""
        status = self._sim_agvs.get(vehicle_id)
        if not status:
            return CommandResult(success=False, vehicle_id=vehicle_id, command=command.value, message="AGV not found")

        transitions = {
            VehicleCommand.MOVE: (VehicleState.MOVING, f"Moving to {params.get('target')}"),
            VehicleCommand.STOP: (VehicleState.IDLE, "Stopped"),
            VehicleCommand.CHARGE: (VehicleState.CHARGING, "Charging"),
            VehicleCommand.LOAD: (None, "Loaded"),
            VehicleCommand.UNLOAD: (None, "Unloaded"),
            VehicleCommand.CANCEL_TASK: (VehicleState.IDLE, "Cancelled"),
        }
        
        new_state, msg = transitions.get(command, (None, command.value))
        if new_state:
            status.state = new_state
        if command == VehicleCommand.LOAD:
            status.load_status = True
        if command == VehicleCommand.UNLOAD:
            status.load_status = False

        self.metrics.record_request(True, 5.0)  # 模拟延迟
        
        return CommandResult(
            success=True, vehicle_id=vehicle_id,
            command=command.value, message=msg, timestamp=ts,
        )

    async def get_status(self, vehicle_id: str) -> Optional[VehicleStatus]:
        """获取单个 AGV 状态 via HTTP"""
        if self.mode == "live" and self._session:
            try:
                endpoint = f"{self.config.base_url}/agv/{vehicle_id}/status"
                async with self._session.get(endpoint, headers=self._build_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return VehicleStatus(
                            vehicle_id=vehicle_id,
                            state=VehicleState(data.get("state", "idle")),
                            x=float(data.get("x", 0)),
                            y=float(data.get("y", 0)),
                            battery_level=float(data.get("battery", 100)),
                            speed=float(data.get("speed", 0)),
                            current_node=str(data.get("current_node", "")),
                            target_node=str(data.get("target_node", "")),
                        )
            except Exception as e:
                logger.debug("HTTP get_status error: %s", e)
        return self._sim_agvs.get(vehicle_id)

    async def get_all_statuses(self) -> List[VehicleStatus]:
        """获取所有车辆状态"""
        if self.mode == "live" and self._session:
            try:
                endpoint = f"{self.config.base_url}/agv/statuses"
                async with self._session.get(endpoint, headers=self._build_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        agvs = data.get("agvs", [])
                        return [
                            VehicleStatus(
                                vehicle_id=a.get("id", ""),
                                state=VehicleState(a.get("state", "idle")),
                                x=float(a.get("x", 0)), y=float(a.get("y", 0)),
                                battery_level=float(a.get("battery", 100)),
                            ) for a in agvs
                        ]
            except Exception as e:
                logger.debug("HTTP get_all_statuses error: %s", e)
        return list(self._sim_agvs.values())

    # ==================== Webhook 支持 ====================

    def register_webhook_handler(self, event_type: str, handler: Callable[[Dict], None]):
        """注册 Webhook 事件处理器"""
        self._webhook_handlers[event_type] = handler
        logger.info("Registered webhook handler for event: %s", event_type)

    async def handle_webhook(self, payload: bytes, signature: Optional[str] = None) -> Dict[str, Any]:
        """
        处理接收到的 Webhook 回调.
        
        Args:
            payload: 原始请求体
            signature: HMAC 签名头 (可选)
            
        Returns:
            处理结果响应
        """
        self.metrics.record_webhook()
        
        # 验证签名 (如果配置了 secret)
        if self.config.webhook_secret and signature:
            expected = hmac.new(
                self.config.webhook_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return {"error": "Invalid signature"}, 401
                
        try:
            data = json.loads(payload)
            event_type = data.get("event_type", "unknown")
            
            # 分发到对应处理器
            handler = self._webhook_handlers.get(event_type)
            if handler:
                # 异步执行处理器
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
                    
                return {"status": "ok", "event": event_type}, 200
            else:
                logger.debug("No handler for webhook event: %s", event_type)
                return {"status": "ignored", "event": event_type}, 200
                
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON: {e}"}, 400
        except Exception as e:
            logger.error("Webhook processing error: %s", e)
            return {"error": str(e)}, 500

    # ==================== 心跳循环 ====================

    async def _heartbeat_loop(self):
        """定期发送心跳检测"""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                if not self._connected or not self._session:
                    continue
                    
                # 发送心跳请求
                endpoint = f"{self.config.base_url}/health"
                async with self._session.get(endpoint, headers=self._build_headers()) as resp:
                    if resp.status != 200:
                        logger.warning("HTTP heartbeat failed: %d", resp.status)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

    # ==================== 高级功能 ====================

    async def send_transport_order(
        self, vehicle_id: str, order: TransportOrderMessage
    ) -> CommandResult:
        """下发运输订单 via HTTP POST"""
        ts = time.time()
        
        endpoint = f"{self.config.base_url}/agv/{vehicle_id}/order"
        payload = {
            "order_id": order.order_id,
            "nodes": order.nodes,
            "edges": order.edges,
            "actions": order.actions or [],
            "timestamp": ts,
        }
        
        if self.mode == "live" and self._session:
            try:
                async with self._session.post(
                    endpoint, json=payload, headers=self._build_headers()
                ) as resp:
                    data = await resp.json() if resp.content_length else {}
                    self.metrics.record_request(resp.status == 200)
                    
                    return CommandResult(
                        success=resp.status in (200, 201),
                        vehicle_id=vehicle_id,
                        command="transport_order",
                        message=data.get("message", f"HTTP {resp.status}"),
                        timestamp=ts,
                    )
            except Exception as e:
                return CommandResult(success=False, vehicle_id=vehicle_id, message=str(e), timestamp=ts)
        
        # 模拟模式
        return await super().send_transport_order(vehicle_id, order)

    async def health_check(self) -> Dict[str, Any]:
        """详细健康检查"""
        session_active = self._session and not self._session.closed
        
        return {
            "connected": self._connected and session_active,
            "protocol": "http/rest",
            "mode": self.mode,
            "config": {
                "base_url": self.config.base_url,
                "timeout": self.config.timeout,
                "max_retries": self.config.max_retries,
                "has_api_key": bool(self.config.api_key),
                "webhook_enabled": bool(self.config.webhook_secret),
            },
            "metrics": self.metrics.to_dict(),
            "webhook_handlers": list(self._webhook_handlers.keys()),
            "sim_agvs": len(self._sim_agvs),
        }

    def get_supported_protocols(self) -> List[str]:
        return ["http", "rest", "webhook"]
