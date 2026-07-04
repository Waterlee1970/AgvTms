"""
MQTT ↔ WebSocket Bridge - 实时数据桥接
对标 Plant Mirror 数据驱动架构

功能:
1. MQTT消息订阅 → WebSocket广播 (实时状态推送)
2. WebSocket命令 → MQTT发布 (反向控制)
3. 消息格式转换 (MQTT payload ↔ WS JSON)
4. 连接管理 (心跳/重连/限流)
5. Topic路由规则配置

数据流:
┌──────────────┐     subscribe      ┌─────────────┐     broadcast    ┌────────────┐
│  MQTT Broker │◀──────────────────│   Bridge     │────────────────▶│ WebSocket  │
│  (AGV设备)   │──────────────────▶│  Core       │◀────────────────│  Clients   │
└──────────────┘     publish        └─────────────┘     command      └────────────┘

Author: Digital Twin Team  
Date: 2026-07-05
"""

import asyncio
import json
import time
import threading
from typing import Dict, List, Optional, Any, Callable, Set, Tuple, Union
from dataclasses import dataclass, field, asdict
from enum import Enum


# ══════════════════════════════════════════════════════════
# 类型定义
# ══════════════════════════════════════════════════════════

class BridgeState(str, Enum):
    """桥接器状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class MessageType(str, Enum):
    """消息类型"""
    AGV_STATUS = "agv_status"           # AGV状态更新
    AGV_POSITION = "agv_position"       # AGV位置变更
    TASK_UPDATE = "task_update"         # 任务状态变更
    ALERT = "alert"                     # 告警事件
    SYSTEM_EVENT = "system_event"       # 系统事件
    COMMAND_RESPONSE = "command_response"  # 命令响应
    RAW_MQTT = "raw_mqtt"              # 原始MQTT透传


@dataclass
class MqttMessage:
    """MQTT 消息封装"""
    topic: str
    payload: Union[bytes, str]
    qos: int = 0
    retain: bool = False
    
    @property
    def payload_str(self) -> str:
        if isinstance(self.payload, bytes):
            return self.payload.decode('utf-8', errors='replace')
        return self.payload
    
    @property
    def payload_json(self) -> dict:
        try:
            return json.loads(self.payload_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"raw": self.payload_str[:500]}
    
    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "payload": self.payload_json if self._is_json() else self.payload_str,
            "qos": self.qos,
            "retain": self.retain,
            "timestamp": time.time(),
        }
    
    def _is_json(self) -> bool:
        try:
            json.loads(self.payload_str)
            return True
        except:
            return False


@dataclass 
class WsMessage:
    """WebSocket 消息封装"""
    msg_type: MessageType
    target: str = ""                   # 目标 (AGV ID / broadcast)
    data: Dict[str, Any] = field(default_factory=dict)
    
    # 元数据
    source_topic: Optional[str] = None # 来源MQTT topic (如果是从MQTT转发)
    timestamp: float = field(default_factory=time.time)
    sequence_id: int = 0              # 序列号
    
    def to_dict(self) -> dict:
        result = {
            "type": self.msg_type.value,
            "target": self.target,
            "data": self.data,
            "timestamp": self.timestamp,
            "seq": self.sequence_id,
        }
        if self.source_topic:
            result["source"] = self.source_topic
        return result
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class SubscriptionRule:
    """
    Topic订阅规则
    
    支持通配符:
    - 单层: agv/+/status
    - 多层: agv/#
    """
    topic_pattern: str                 # MQTT topic模式
    message_type: MessageType          # 转换后的WS消息类型
    extract_agv_id_from: str = "topic"  # 如何提取AGV ID: topic | payload.field
    extract_field: str = ""             # 提取字段名
    transform_fn: Optional[str] = None  # 转换函数名 (可选)
    
    # 统计
    messages_forwarded: int = 0
    last_message_time: float = 0
    
    def matches(self, topic: str) -> bool:
        """检查topic是否匹配此规则"""
        pattern_parts = self.topic_pattern.split("/")
        topic_parts = topic.split("/")
        
        for i, part in enumerate(pattern_parts):
            if part == "#":
                return True  # 多层通配符匹配剩余所有
            if i >= len(topic_parts):
                return False
            if part != "+" and part != topic_parts[i]:
                return False
        
        return len(pattern_parts) == len(topic_parts)


@dataclass
class BridgeStats:
    """桥接器统计信息"""
    state: BridgeState = BridgeState.DISCONNECTED
    
    # MQTT连接
    mqtt_connected: bool = False
    mqtt_reconnect_count: int = 0
    mqtt_last_error: str = ""
    
    # WebSocket连接
    ws_client_count: int = 0
    
    # 消息统计
    mqtt_messages_received: int = 0
    ws_messages_sent: int = 0
    ws_commands_received: int = 0
    mqtt_messages_published: int = 0
    
    # 错误统计
    errors_total: int = 0
    last_error_time: float = 0
    
    # 启动时间
    start_time: float = field(default_factory=time.time)
    
    def uptime_seconds(self) -> float:
        return time.time() - self.start_time
    
    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "uptime_seconds": round(self.uptime_seconds(), 1),
        }


# ══════════════════════════════════════════════════════════
# Bridge 核心
# ══════════════════════════════════════════════════════════

class MqttWsBridge:
    """
    MQTT ↔ WebSocket 双向桥接器
    
    功能:
    - 订阅MQTT topic并转发到WebSocket客户端
    - 接收WebSocket命令并发布到MQTT
    - 自动重连与错误恢复
    - 消息速率限制防止风暴
    - 连接健康检查
    
    使用示例:
        bridge = MqttWsBridge(mqtt_host="localhost", mqtt_port=1883)
        
        # 注册默认规则
        bridge.register_default_rules()
        
        # 启动桥接
        await bridge.start()
        
        # 注册WebSocket连接
        await bridge.add_ws_connection(websocket)
    """
    
    # 默认配置
    DEFAULT_MQTT_HOST = "127.0.0.1"
    DEFAULT_MQTT_PORT = 1883
    DEFAULT_RECONNECT_INTERVAL = 5.0      # 重连间隔(秒)
    DEFAULT_KEEPALIVE = 60              # 心跳间隔(秒)
    MAX_WS_CLIENTS = 100                # 最大WS连接数
    MESSAGE_RATE_LIMIT = 100            # 每秒最大消息数/客户端
    STATS_RESET_INTERVAL = 300          # 统计重置间隔(秒)
    
    def __init__(
        self,
        mqtt_host: str = None,
        mqtt_port: int = None,
        client_id: str = "agvtws_bridge",
        username: str = None,
        password: str = None,
    ):
        # ── 配置 ──
        self._mqtt_host = mqtt_host or self.DEFAULT_MQTT_HOST
        self._mqtt_port = mqtt_port or self.DEFAULT_MQTT_PORT
        self._client_id = client_id
        self._username = username
        self._password = password
        
        # ── 状态 ──
        self._state: BridgeState = BridgeState.DISCONNECTED
        self._stats: BridgeStats = BridgeStats()
        self._sequence_counter: int = 0
        
        # ── MQTT ──
        self._mqtt_client: Optional[Any] = None  # paho.mqtt.client
        self._mqtt_task: Optional[asyncio.Task] = None
        
        # ── WebSocket ──
        self._ws_clients: Set[Any] = set()       # WebSocket连接集合
        self._client_rate_limits: Dict[int, Tuple[float, int]] = {}  # {id: [last_time, count]}
        
        # ── 订阅规则 ──
        self._subscription_rules: List[SubscriptionRule] = []
        
        # ── 回调 ──
        self._on_message_callback: Optional[Callable[[WsMessage], None]] = None
        self._on_error_callback: Optional[Callable[[str, Exception], None]] = None
    
    # ═════════════════════════════════════════════════════
    # 生命周期管理
    # ═════════════════════════════════════════════════════
    
    async def start(self):
        """启动桥接器"""
        self._state = BridgeState.CONNECTING
        self._stats.start_time = time.time()
        
        # 注册默认规则
        if not self._subscription_rules:
            self.register_default_rules()
        
        # 连接MQTT
        try:
            await self._connect_mqtt()
        except Exception as e:
            print(f"[Bridge] Initial MQTT connection failed: {e}")
            self._state = BridgeState.RECONNECTING
            # 后台尝试重连
            self._mqtt_task = asyncio.create_task(self._reconnect_loop())
    
    async def stop(self):
        """停止桥接器"""
        self._state = BridgeState.DISCONNECTED
        
        if self._mqtt_task and not self._mqtt_task.done():
            self._mqtt_task.cancel()
        
        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
            except:
                pass
        
        # 断开所有WS客户端
        for ws in list(self._ws_clients):
            try:
                # 不在这里关闭WS，只是从集合移除
                self._ws_clients.discard(ws)
            except:
                pass
        
        print("[Bridge] Stopped")
    
    # ═════════════════════════════════════════════════════
    # MQTT 连接管理
    # ═════════════════════════════════════════════════════
    
    async def _connect_mqtt(self):
        """建立MQTT连接"""
        import paho.mqtt.client as mqtt
        
        client = mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv311)
        
        if self._username:
            client.username_pw_set(self._username, self._password)
        
        # 回调
        client.on_connect = self._on_mqtt_connect
        client.on_message = self._on_mqtt_message
        client.on_disconnect = self._on_mqtt_disconnect
        
        # 连接
        client.connect(self._mqtt_host, self._mqtt_port, keepalive=self.DEFAULT_KEEPALIVE)
        client.loop_start()
        
        self._mqtt_client = client
        
        # 等待连接确认
        await asyncio.sleep(1)
        
        if not self._stats.mqtt_connected:
            raise ConnectionError("MQTT connection failed")
    
    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        """MQTT连接回调"""
        if rc == 0:
            self._stats.mqtt_connected = True
            self._state = BridgeState.CONNECTED
            print(f"[Bridge] Connected to MQTT at {self._mqtt_host}:{self._mqtt_port}")
            
            # 订阅所有规则中的topics
            for rule in self._subscription_rules:
                client.subscribe(rule.topic_pattern)
                print(f"[Bridge] Subscribed: {rule.topic_pattern}")
        else:
            self._stats.mqtt_last_error = f"Connection failed with code {rc}"
            self._record_error(f"MQTT connect error: {rc}")
    
    def _on_mqtt_disconnect(self, client, userdata, rc, properties=None):
        """MQTT断开回调"""
        self._stats.mqtt_connected = False
        self._state = BridgeState.RECONNECTING if rc != 0 else BridgeState.DISCONNECTED
        
        if rc != 0:
            print(f"[Bridge] Unexpected disconnect (rc={rc}), will reconnect...")
            if self._mqtt_task is None or self._mqtt_task.done():
                self._mqtt_task = asyncio.create_task(self._reconnect_loop())
    
    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT消息到达回调 (在paho线程中执行，需要线程安全)"""
        try:
            retain_val = msg.retain if hasattr(msg, 'retain') else False
            mqtt_msg = MqttMessage(
                topic=msg.topic,
                payload=msg.payload,
                qos=msg.qos,
                retain=retain_val,
            )
            
            # 在事件循环中处理转发
            asyncio.run_coroutine_threadsafe(
                self._handle_mqtt_message(mqtt_msg), 
                asyncio.get_event_loop()
            )
            
        except Exception as e:
            self._record_error(f"MQTT message handler error: {e}")
    
    async def _reconnect_loop(self):
        """自动重连循环"""
        while self._state != BridgeState.CONNECTED and self._state != BridgeState.DISCONNECTED:
            self._state = BridgeState.RECONNECTING
            self._stats.mqtt_reconnect_count += 1
            
            print(f"[Bridge] Reconnecting in {self.DEFAULT_RECONNECT_INTERVAL}s... "
                  f"(attempt #{self._stats.mqtt_reconnect_count})")
            
            await asyncio.sleep(self.DEFAULT_RECONNECT_INTERVAL)
            
            try:
                await self._connect_mqtt()
                break
            except Exception as e:
                self._record_error(f"Reconnect failed: {e}")
    
    # ═════════════════════════════════════════════════════
    # 消息处理核心
    # ═════════════════════════════════════════════════════
    
    async def _handle_mqtt_message(self, mqtt_msg: MqttMessage):
        """处理收到的MQTT消息并转发到WebSocket"""
        self._stats.mqtt_messages_received += 1
        
        # 查找匹配的订阅规则
        matched_rule = None
        for rule in self._subscription_rules:
            if rule.matches(mqtt_msg.topic):
                matched_rule = rule
                break
        
        if not matched_rule:
            # 无匹配规则：发送原始消息
            ws_msg = WsMessage(
                msg_type=MessageType.RAW_MQTT,
                data={"topic": mqtt_msg.topic, "payload": mqtt_msg.payload_json},
                source_topic=mqtt_msg.topic,
            )
        else:
            # 根据规则转换
            ws_msg = self._transform_message(mqtt_msg, matched_rule)
            matched_rule.messages_forwarded += 1
            matched_rule.last_message_time = time.time()
        
        # 广播到所有WebSocket客户端
        await self._broadcast_to_ws(ws_msg)
    
    def _transform_message(self, mqtt_msg: MqttMessage, rule: SubscriptionRule) -> WsMessage:
        """
        将MQTT消息转换为WebSocket消息格式
        
        转换逻辑:
        1. 从topic或payload中提取AGV ID
        2. 将MQTT payload映射为结构化data
        3. 设置正确的message_type
        """
        # 提取AGV ID
        agv_id = ""
        if rule.extract_agv_id_from == "topic":
            # 从topic最后一部分提取: agv/{agv_id}/status
            parts = mqtt_msg.topic.split("/")
            agv_id = parts[-2] if len(parts) >= 2 else parts[-1]
        elif rule.extract_field:
            payload = mqtt_msg.payload_json
            agv_id = str(payload.get(rule.extract_field, ""))
        
        # 构建数据
        data = mqtt_msg.payload_json if mqtt_msg._is_json() else {
            "raw_payload": mqtt_msg.payload_str,
            "encoding": "unknown",
        }
        
        # 添加元数据
        data["_mqtt_topic"] = mqtt_msg.topic
        data["_received_at"] = time.time()
        
        # 创建WS消息
        self._sequence_counter += 1
        ws_msg = WsMessage(
            msg_type=rule.message_type,
            target=agv_id,
            data=data,
            source_topic=mqtt_msg.topic,
            sequence_id=self._sequence_counter,
        )
        
        return ws_msg
    
    async def _broadcast_to_ws(self, ws_msg: WsMessage):
        """广播消息到所有WebSocket客户端"""
        if not self._ws_clients:
            return
        
        payload = ws_msg.to_json()
        dead_clients = set()
        
        now = time.time()
        for ws in self._ws_clients:
            # 速率限制
            ws_id = id(ws)
            last_time, count = self._client_rate_limits.get(ws_id, (0, 0))
            
            if now - last_time < 1.0:
                if count >= self.MESSAGE_RATE_LIMIT:
                    continue  # 跳过超速的客户端
                count += 1
            else:
                count = 1
            
            self._client_rate_limits[ws_id] = (now, count)
            
            # 发送
            try:
                await ws.send_text(payload)
                self._stats.ws_messages_sent += 1
            except Exception:
                dead_clients.add(ws)
        
        # 清理断开的连接
        self._ws_clients -= dead_clients
        for ws_id in dead_clients:
            self._client_rate_limits.pop(id(ws_id), None)
    
    async def handle_ws_command(self, ws, raw_data: str):
        """处理来自WebSocket的命令 (逆向: WS → MQTT)"""
        self._stats.ws_commands_received += 1
        
        try:
            cmd = json.loads(raw_data)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}
        
        action = cmd.get("action")
        target = cmd.get("target")         # AGV ID or topic
        payload = cmd.get("data", {})
        
        # 构造MQTT topic和消息
        topic = f"agv/{target}/command"
        mqtt_payload = json.dumps({
            "action": action,
            "source": "web_console",
            "timestamp": time.time(),
            **payload,
        })
        
        # 发布到MQTT
        success = await self._publish_to_mqtt(topic, mqtt_payload)
        
        if success:
            self._stats.mqtt_messages_published += 1
            return {"status": "sent", "topic": topic}
        else:
            return {"error": "MQTT publish failed"}
    
    async def _publish_to_mqtt(self, topic: str, payload: str, qos: int = 1) -> bool:
        """发布消息到MQTT"""
        if not self._mqtt_client or not self._stats.mqtt_connected:
            return False
        
        try:
            info = self._mqtt_client.publish(topic, payload, qos=qos)
            return info.rc == 0  # paho.MQTT_ERR_SUCCESS
        except Exception as e:
            self._record_error(f"MQTT publish error: {e}")
            return False
    
    # ═════════════════════════════════════════════════════
    # WebSocket 连接管理
    # ═════════════════════════════════════════════════════
    
    async def add_ws_connection(self, websocket):
        """注册新的WebSocket连接"""
        if len(self._ws_clients) >= self.MAX_WS_CLIENTS:
            raise Exception(f"Max clients ({self.MAX_WS_CLIENTS}) reached")
        
        self._ws_clients.add(websocket)
        self._stats.ws_client_count = len(self._ws_clients)
        
        # 发送欢迎消息
        welcome = WsMessage(
            msg_type=MessageType.SYSTEM_EVENT,
            target="broadcast",
            data={"event": "connected", "client_id": id(websocket)},
        )
        try:
            await websocket.send_text(welcome.to_json())
        except:
            pass
        
        print(f"[Bridge] WS client connected (total: {self._stats.ws_client_count})")
    
    def remove_ws_connection(self, websocket):
        """移除WebSocket连接"""
        self._ws_clients.discard(websocket)
        self._client_rate_limits.pop(id(websocket), None)
        self._stats.ws_client_count = len(self._ws_clients)
        print(f"[Bridge] WS client disconnected (total: {self._stats.ws_client_count})")
    
    # ═════════════════════════════════════════════════════
    # 订阅规则管理
    # ═════════════════════════════════════════════════════
    
    def register_default_rules(self):
        """注册默认的MQTT→WS订阅规则"""
        default_rules = [
            # AGV状态上报
            SubscriptionRule(
                topic_pattern="agv/+/status",
                message_type=MessageType.AGV_STATUS,
                extract_agv_id_from="topic",
            ),
            # AGV位置更新
            SubscriptionRule(
                topic_pattern="agv/+/position",
                message_type=MessageType.AGV_POSITION,
                extract_agv_id_from="topic",
            ),
            # VDA5050 连接状态
            SubscriptionRule(
                topic_pattern="vda5050/connection/#",
                message_type=MessageType.SYSTEM_EVENT,
                extract_agv_id_from="topic",
            ),
            # VDA5050 订单状态
            SubscriptionRule(
                topic_pattern="vda5050/order/#",
                message_type=MessageType.TASK_UPDATE,
                extract_agv_id_from="topic",
            ),
            # 系统告警
            SubscriptionRule(
                topic_pattern="system/alert/#",
                message_type=MessageType.ALERT,
            ),
        ]
        
        self._subscription_rules.extend(default_rules)
    
    def add_subscription_rule(self, rule: SubscriptionRule):
        """添加自定义订阅规则"""
        self._subscription_rules.append(rule)
        
        # 如果已连接MQTT，立即订阅新topic
        if self._mqtt_client and self._stats.mqtt_connected:
            self._mqtt_client.subscribe(rule.topic_pattern)
    
    def get_subscription_rules(self) -> List[Dict]:
        """获取所有订阅规则"""
        return [
            {
                "topic_pattern": r.topic_pattern,
                "message_type": r.message_type.value,
                "messages_forwarded": r.messages_forwarded,
                "last_active": r.last_message_time,
            }
            for r in self._subscription_rules
        ]
    
    # ═════════════════════════════════════════════════════
    # 状态查询
    # ═════════════════════════════════════════════════════
    
    @property
    def state(self) -> BridgeState:
        return self._state
    
    def get_stats(self) -> Dict[str, Any]:
        """获取桥接器统计信息"""
        stats = self._stats.to_dict()
        stats["subscription_rules"] = len(self._subscription_rules)
        stats["rate_limit_per_client"] = self.MESSAGE_RATE_LIMIT
        return stats
    
    def _record_error(self, error_msg: str):
        """记录错误"""
        self._stats.errors_total += 1
        self._stats.last_error_time = time.time()
        self._stats.mqtt_last_error = error_msg[:200]
        
        if self._on_error_callback:
            try:
                self._on_error_callback(error_msg, Exception(error_msg))
            except:
                pass


# ══════════════════════════════════════════════════════════
# FastAPI Router - Bridge API
# ══════════════════════════════════════════════════════════

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query

bridge_router = APIRouter(prefix="/api/v3/bridge", tags=["MQTT-WS Bridge"])

# 全局单例
_bridge_instance: Optional[MqttWsBridge] = None


def get_bridge() -> MqttWsBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = MqttWsBridge()
    return _bridge_instance


@bridge_router.get("/status")
async def get_bridge_status():
    """获取桥接器状态 (MQTT连接、WS客户端数、消息统计等)"""
    bridge = get_bridge()
    return bridge.get_stats()


@bridge_router.post("/start")
async def start_bridge():
    """启动MQTT-WebSocket桥接"""
    bridge = get_bridge()
    
    if bridge.state == BridgeState.CONNECTED:
        return {"message": "Already running"}
    
    await bridge.start()
    return {"message": "Bridge started", **bridge.get_stats()}


@bridge_router.post("/stop")
async def stop_bridge():
    """停止桥接"""
    bridge = get_bridge()
    await bridge.stop()
    return {"message": "Bridge stopped"}


@bridge_router.get("/rules")
async def get_subscription_rules():
    """获取当前订阅规则列表"""
    bridge = get_bridge()
    return {"rules": bridge.get_subscription_rules()}


@bridge_router.post("/rules")
async def add_rule(rule: SubscriptionRule):
    """添加自定义订阅规则"""
    bridge = get_bridge()
    bridge.add_subscription_rule(rule)
    return {"message": "Rule added", "rule": rule.topic_pattern}


@bridge_router.websocket("/ws")
async def bridge_websocket_endpoint(websocket: WebSocket):
    """
    MQTT-WebSocket 桥接端点
    
    此WebSocket提供经过桥接的实时数据流：
    Server → Client: AGV状态、位置、任务更新等
    Client → Server: 控制命令 (通过MQTT下发到AGV)
    
    使用方式:
    1. 连接到 ws://host/api/v3/bridge/ws
    2. 收到 connected 系统事件表示就绪
    3. 接收实时数据流
    4. 发送 JSON 命令: {"action": "...", "target": "agv_id", "data": {...}}
    """
    bridge = get_bridge()
    
    await websocket.accept()
    await bridge.add_ws_connection(websocket)
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            
            # 处理客户端发来的命令
            response = await bridge.handle_ws_command(websocket, raw_data)
            
            # 发送响应 (如果是请求-响应模式)
            if response.get("error"):
                await websocket.send_text(json.dumps({"type": "error", "data": response}))
                
    except WebSocketDisconnect:
        bridge.remove_ws_connection(websocket)
    except Exception as e:
        bridge.remove_ws_connection(websocket)
        print(f"[Bridge WS] Error: {e}")


# 导出
__all__ = [
    'MqttWsBridge', 'MqttMessage', 'WsMessage', 'SubscriptionRule',
    'BridgeStats', 'BridgeState', 'MessageType',
    'bridge_router',
]
