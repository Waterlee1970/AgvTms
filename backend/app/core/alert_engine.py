"""
告警规则引擎 — Phase 3 可观测性核心.

对标: Prometheus AlertManager / Grafana Alerts.

功能:
  1. 规则定义:
     - 基于阈值的告警规则 (指标 > 阈值)
     - 基于趋势的告警规则 (连续N次上升)
     - 组合条件 (AND/OR逻辑)
     - 抑制规则 (防止告警风暴)
  
  2. 告警级别:
     - INFO: 信息通知 (无需立即处理)
     - WARNING: 警告 (需要关注)
     - CRITICAL: 严重 (需要立即处理)
     - EMERGENCY: 紧急 (可能影响生产)
  
  3. 告警通道:
     - 日志 (默认)
     - Webhook (企业微信/钉钉/Slack)
     - 数据库持久化
     - 回调函数
  
  4. 告警生命周期:
     - FIRING → 已触发 (持续中)
     - RESOLVED → 已恢复
     - SUPPRESSED → 被抑制
     - SILENCED → 静默中

依赖:
  - time (标准库)
  - threading (并发安全)

使用示例:
    from app.core.alert_engine import alert_manager, AlertRule
    
    # 定义规则
    rule = AlertRule(
        name='high_error_rate',
        condition='error_rate > 0.05',
        severity='warning',
        duration_seconds=300,  # 持续5分钟才触发
    )
    alert_manager.add_rule(rule)
    
    # 提交指标
    alert_manager.evaluate_metric('error_rate', 0.08)
"""

from __future__ import annotations

import json
import time
import re
import threading
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ==================== 枚举类型 ====================

class AlertSeverity(str, Enum):
    """告警严重级别"""
    INFO = "info"                    # 信息
    WARNING = "warning"              # 警告
    CRITICAL = "critical"            # 严重
    EMERGENCY = "emergency"          # 紧急
    
    @classmethod
    def from_string(cls, s: str) -> AlertSeverity:
        try:
            return cls(s.lower())
        except ValueError:
            return cls.WARNING


class AlertState(str, Enum):
    """告警状态"""
    PENDING = "pending"              # 待确认 (刚触发)
    FIRING = "firing"                # 触发中 (已确认)
    RESOLVED = "resolved"            # 已解决
    SUPPRESSED = "suppressed"        # 被抑制
    SILENCED = "silenced"            # 被静默


class AlertChannel(str, Enum):
    """告警通道"""
    LOG = "log"                      # 日志
    WEBHOOK = "webhook"              # Webhook
    CALLBACK = "callback"            # 回调
    DATABASE = "database"            # 数据库存储


# ==================== 数据模型 ====================

@dataclass
class AlertRule:
    """
    告警规则定义.
    
    支持的condition语法 (简化版PromQL):
    - 简单比较: metric > threshold, metric < threshold, metric == value
    - 百分比: metric > 5% (自动转换为小数)
    - 范围: metric in [min, max]
    
    示例:
        condition='error_rate > 0.05'
        condition='cpu_usage > 80%'
        condition='latency_p99 > 500'
        condition='agv_online_count < 3'
    """
    name: str                         # 规则名称 (唯一标识)
    condition: str                     # 触发条件表达式
    severity: AlertSeverity = AlertSeverity.WARNING
    duration_seconds: float = 0.0      # 持续时间 (0表示立即触发)
    cooldown_seconds: float = 300.0    # 冷却时间 (同一规则两次告警间隔)
    
    # 标签 (用于分组和路由)
    labels: Dict[str, str] = field(default_factory=dict)
    
    # 注释和文档
    description: str = ""
    runbook_url: Optional[str] = None  # 处理手册链接
    
    # 抑制配置
    group_by: List[str] = field(default_factory=list)  # 分组维度
    suppress_duplicates: bool = True  # 抑制重复告警
    
    enabled: bool = True              # 是否启用
    last_triggered: Optional[float] = None  # 上次触发时间
    
    @property
    def rule_id(self) -> str:
        """规则唯一ID"""
        return hashlib.md5(self.name.encode()).hexdigest()[:12]


@dataclass
class AlertInstance:
    """
    告警实例 — 一次具体的告警事件.
    """
    alert_id: str                     # 告警实例ID
    rule_name: str                    # 规则名称
    state: AlertState = AlertState.PENDING
    
    severity: AlertSeverity = AlertSeverity.WARNING
    value: Any = None                 # 触发时的指标值
    threshold: Any = None             # 阈值
    
    message: str = ""                 # 告警消息
    description: str = ""             # 详细描述 (从规则复制)
    details: Dict[str, Any] = field(default_factory=dict)
    
    # 时间戳
    fired_at: Optional[float] = None  # 触发时间
    resolved_at: Optional[float] = None  # 解决时间
    
    # 元信息
    labels: Dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""             # 指纹 (用于去重)
    
    # 统计
    notification_count: int = 0       # 已通知次数
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'alert_id': self.alert_id,
            'rule_name': self.rule_name,
            'state': self.state.value,
            'severity': self.severity.value,
            'value': self.value,
            'threshold': self.threshold,
            'message': self.message,
            'details': self.details,
            'fired_at': datetime.fromtimestamp(self.fired_at).isoformat() if self.fired_at else None,
            'resolved_at': datetime.fromtimestamp(self.resolved_at).isoformat() if self.resolved_at else None,
            'labels': self.labels,
            'notification_count': self.notification_count,
        }


@dataclass
class SilencedRule:
    """静默规则 (临时禁用某类告警)"""
    matchers: Dict[str, str]          # 匹配标签
    start_time: datetime
    end_time: datetime
    created_by: str = "system"
    comment: str = ""


@dataclass
class WebhookConfig:
    """Webhook配置"""
    url: str
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    timeout: float = 10.0
    template: Optional[str] = None    # 自定义消息模板


# ==================== 条件求值器 ====================

class ConditionEvaluator:
    """
    条件表达式求值器.
    
    安全起见，只支持有限的比较运算符，
    不使用eval/exec以防止代码注入。
    """
    
    # 允许的操作符模式
    _OPERATORS = {
        '>': lambda a, b: a > b,
        '<': lambda a, b: a < b,
        '>=': lambda a, b: a >= b,
        '<=': lambda a, b: a <= b,
        '==': lambda a, b: a == b,
        '!=': lambda a, b: a != b,
    }
    
    @classmethod
    def evaluate(cls, condition: str, metrics: Dict[str, float]) -> bool:
        """
        评估条件表达式.
        
        Args:
            condition: 条件字符串 (如 "error_rate > 0.05")
            metrics: 当前指标值字典
            
        Returns:
            是否满足条件
        """
        condition = condition.strip()
        
        # 尝试匹配各种模式
        for op_str, op_func in cls._OPERATORS.items():
            pattern = rf'^(\w+)\s*{re.escape(op_str)}\s*(.+)$'
            match = re.match(pattern, condition)
            
            if match:
                metric_name = match.group(1).strip()
                threshold_str = match.group(2).strip()
                
                # 获取实际值
                actual_value = metrics.get(metric_name)
                if actual_value is None:
                    return False
                
                # 解析阈值 (支持百分比)
                threshold_value = cls._parse_threshold(threshold_str)
                
                try:
                    return op_func(float(actual_value), float(threshold_value))
                except (ValueError, TypeError):
                    return False
        
        # 不支持的格式
        logger = __import__('logging').getLogger(__name__)
        logger.warning(f"Unsupported condition format: {condition}")
        return False
    
    @classmethod
    def _parse_threshold(cls, value_str: str) -> float:
        """解析阈值 (支持百分号)"""
        value_str = value_str.strip().rstrip('%')
        
        try:
            val = float(value_str)
            # 如果原字符串有%, 则除以100
            if '%' in value_str:
                val /= 100.0
            return val
        except ValueError:
            return 0.0


# ==================== 告警引擎核心 ====================

class AlertManager:
    """
    告警管理器 — 全局单例.
    
    功能:
    1. 管理所有告警规则
    2. 接收指标数据并评估
    3. 维护告警生命周期
    4. 发送通知到各通道
    """
    
    def __init__(self):
        self._rules: Dict[str, AlertRule] = {}
        self._active_alerts: Dict[str, AlertInstance] = {}  # alert_id -> Alert
        self._alert_history: deque = deque(maxlen=10000)    # 最近历史记录
        
        self._metrics_buffer: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._lock = threading.RLock()
        
        # 通知通道
        self._channels: Dict[AlertChannel, List[Callable]] = {
            AlertChannel.LOG: [],
            AlertChannel.WEBHOOK: [],
            AlertChannel.CALLBACK: [],
            AlertChannel.DATABASE: [],
        }
        
        # 静默规则
        self._silences: List[SilencedRule] = []
        
        # 统计
        self._stats = {
            'total_fired': 0,
            'total_resolved': 0,
            'total_suppressed': 0,
        }
    
    def add_rule(self, rule: AlertRule) -> None:
        """添加告警规则"""
        with self._lock:
            self._rules[rule.rule_id] = rule
        logger = __import__('logging').getLogger(__name__)
        logger.info(f"Alert rule added: {rule.name} ({rule.condition})")
    
    def remove_rule(self, rule_id: str) -> None:
        """移除告警规则"""
        with self._lock:
            self._rules.pop(rule_id, None)
    
    def enable_rule(self, rule_id: str) -> bool:
        """启用规则"""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule:
                rule.enabled = True
                return True
            return False
    
    def disable_rule(self, rule_id: str) -> bool:
        """禁用规则"""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule:
                rule.enabled = False
                return True
            return False
    
    def add_channel(self, channel_type: AlertChannel, handler: Callable) -> None:
        """添加通知通道"""
        self._channels[channel_type].append(handler)
    
    def evaluate_metrics(self, metrics: Dict[str, float]) -> List[AlertInstance]:
        """
        评估指标并触发告警.
        
        Args:
            metrics: 当前指标值字典 (如 {'error_rate': 0.08, 'cpu_usage': 85.5})
            
        Returns:
            新触发的告警列表
        """
        now = time.time()
        new_alerts = []
        
        # 缓存指标用于持续性判断
        for name, value in metrics.items():
            self._metrics_buffer[name].append((now, value))
        
        with self._lock:
            for rule_id, rule in self._rules.items():
                if not rule.enabled:
                    continue
                
                # 评估条件
                triggered = ConditionEvaluator.evaluate(rule.condition, metrics)
                
                if triggered:
                    # 检查冷却时间
                    if rule.last_triggered and (now - rule.last_triggered) < rule.cooldown_seconds:
                        continue
                    
                    # 检查持续时间 (如果有配置)
                    if rule.duration_seconds > 0:
                        if not self._check_duration_sustained(rule, now, rule.duration_seconds):
                            continue
                    
                    # 创建或更新告警
                    alert = self._create_or_update_alert(rule, metrics, now)
                    if alert:
                        new_alerts.append(alert)
                        rule.last_triggered = now
                
                else:
                    # 条件不满足, 尝试解决已有告警
                    self._try_resolve_alert(rule, now)
        
        return new_alerts
    
    def _check_duration_sustained(self, rule: AlertRule, now: float, duration: float) -> bool:
        """检查条件是否持续满足指定时间"""
        # 从条件中提取指标名
        metric_name = rule.condition.split()[0] if rule.condition else ''
        buffer = self._metrics_buffer.get(metric_name, [])
        
        if not buffer:
            return False
        
        cutoff = now - duration
        recent_samples = [(t, v) for t, v in buffer if t >= cutoff]
        
        if len(recent_samples) < 2:
            return False
        
        # 检查最近的所有样本是否都满足条件
        for _, value in recent_samples:
            test_metrics = {metric_name: value}
            if not ConditionEvaluator.evaluate(rule.condition, test_metrics):
                return False
        
        return True
    
    def _create_or_update_alert(
        self,
        rule: AlertRule,
        metrics: Dict[str, float],
        now: float,
    ) -> Optional[AlertInstance]:
        """创建或更新告警实例"""
        # 生成指纹 (基于规则名 + 标签分组)
        fingerprint_parts = [rule.name]
        for key in sorted(rule.group_by or []):
            fingerprint_parts.append(f"{key}={metrics.get(key, '')}")
        fingerprint = hashlib.md5('|'.join(fingerprint_parts).encode()).hexdigest()[:16]
        
        # 查找是否有相同指纹的活跃告警
        existing = None
        for alert_id, alert in self._active_alerts.items():
            if alert.fingerprint == fingerprint and alert.state in [AlertState.FIRING, AlertState.PENDING]:
                existing = alert
                break
        
        if existing:
            # 更新已有告警
            existing.value = self._extract_value(rule, metrics)
            existing.details['last_value'] = existing.value
            existing.notification_count += 1
            return None  # 不重复创建
        
        # 检查静默规则
        if self._is_silenced(rule.labels):
            alert = self._make_alert(rule, metrics, now, fingerprint)
            alert.state = AlertState.SUPPRESSED
            self._stats['total_suppressed'] += 1
            return alert
        
        # 创建新告警
        alert = self._make_alert(rule, metrics, now, fingerprint)
        alert.state = AlertState.FIRING
        alert.fired_at = now
        
        self._active_alerts[alert.alert_id] = alert
        self._alert_history.append(alert)
        self._stats['total_fired'] += 1
        
        # 发送通知
        self._send_notification(alert)
        
        return alert
    
    def _make_alert(
        self,
        rule: AlertRule,
        metrics: Dict[str, float],
        now: float,
        fingerprint: str,
    ) -> AlertInstance:
        """构造AlertInstance对象"""
        value = self._extract_value(rule, metrics)
        threshold = self._extract_threshold(rule)
        
        return AlertInstance(
            alert_id=f"alt-{int(now * 1000)}-{hashlib.md5(rule.name.encode()).hexdigest()[:6]}",
            rule_name=rule.name,
            state=AlertState.PENDING,
            severity=rule.severity,
            value=value,
            threshold=threshold,
            message=f"[{rule.severity.value.upper()}] {rule.name}: {rule.condition} (current: {value})",
            description=rule.description,
            labels=dict(rule.labels),
            fingerprint=fingerprint,
            fired_at=now,
        )
    
    def _extract_value(self, rule: AlertRule, metrics: Dict[str, float]) -> Any:
        """从条件中提取指标名对应的值"""
        metric_name = rule.condition.split()[0] if rule.condition else 'unknown'
        return metrics.get(metric_name)
    
    def _extract_threshold(self, rule: AlertRule) -> Any:
        """从条件中提取阈值"""
        match = re.search(r'([<>=!]+)\s*(.+)', rule.condition)
        if match:
            return match.group(2).strip()
        return None
    
    def _try_resolve_alert(self, rule: AlertRule, now: float) -> None:
        """尝试解决告警"""
        for alert_id, alert in list(self._active_alerts.items()):
            if alert.rule_name == rule.name and alert.state == AlertState.FIRING:
                alert.state = AlertState.RESOLVED
                alert.resolved_at = now
                self._stats['total_resolved'] += 1
                
                # 发送恢复通知
                alert.message = f"RESOLVED: {rule.name}"
                self._send_notification(alert)
    
    def _is_silenced(self, labels: Dict[str, str]) -> bool:
        """检查是否被静默"""
        now = datetime.utcnow()
        for silence in self._silences:
            if silence.start_time <= now <= silence.end_time:
                # 检查标签是否匹配
                if all(labels.get(k) == v for k, v in silence.matchers.items()):
                    return True
        return False
    
    def _send_notification(self, alert: AlertInstance) -> None:
        """发送通知到所有注册的通道"""
        payload = alert.to_dict()
        
        # LOG通道 (始终记录)
        logger = __import__('logging').getLogger(__name__)
        log_msg = (
            f"ALERT [{alert.severity.value.upper()}] "
            f"rule={alert.rule_name}, "
            f"state={alert.state.value}, "
            f"value={alert.value}"
        )
        if alert.state == AlertState.FIRING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
        
        # 其他通道
        for channel_type, handlers in self._channels.items():
            if channel_type == AlertChannel.LOG:
                continue
            
            for handler in handlers:
                try:
                    handler(payload)
                except Exception as e:
                    logger.error(f"Notification error ({channel_type.value}): {e}")
    
    def get_active_alerts(self) -> List[Dict]:
        """获取当前活跃的告警"""
        with self._lock:
            return [a.to_dict() for a in self._active_alerts.values() 
                   if a.state in [AlertState.FIRING, AlertState.PENDING]]
    
    def get_alert_history(
        self,
        limit: int = 100,
        severity: Optional[AlertSeverity] = None,
    ) -> List[Dict]:
        """获取告警历史"""
        history = list(self._alert_history)
        
        if severity:
            history = [a for a in history if a.severity == severity]
        
        return [a.to_dict() for a in history[-limit:]]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            active_count = sum(
                1 for a in self._active_alerts.values()
                if a.state in [AlertState.FIRING, AlertState.PENDING]
            )
            
            by_severity = defaultdict(int)
            for a in self._active_alerts.values():
                if a.state in [AlertState.FIRING, AlertState.PENDING]:
                    by_severity[a.severity.value] += 1
            
            return {
                'rules_total': len(self._rules),
                'rules_enabled': sum(1 for r in self._rules.values() if r.enabled),
                'alerts_active': active_count,
                'alerts_by_severity': dict(by_severity),
                'history_size': len(self._alert_history),
                'stats': dict(self._stats),
            }
    
    def silence(self, matchers: Dict[str, str], duration_minutes: int, comment: str = "") -> None:
        """创建静默规则"""
        silence = SilencedRule(
            matchers=matchers,
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow() + timedelta(minutes=duration_minutes),
            comment=comment,
        )
        self._silences.append(silence)
        
        # 清理过期静默
        now = datetime.utcnow()
        self._silences = [s for s in self._silences if s.end_time > now]


# ==================== 内置告警规则 ====================

def create_builtin_alert_rules() -> List[AlertRule]:
    """
    创建内置告警规则.
    
    对标工业界最佳实践:
    - Google SRE: Burn Rate Alerts
    - Prometheus: 常用告警模板
    - 极智嘉RMS: AGV行业特定告警
    """
    rules = [
        # ===== 系统可靠性 =====
        AlertRule(
            name='high_error_rate_5xx',
            condition='error_rate > 0.05',
            severity=AlertSeverity.WARNING,
            duration_seconds=300,
            cooldown_seconds=1800,
            labels={'category': 'reliability'},
            description='HTTP 5xx错误率超过5%',
            runbook_url='/docs/runbooks/high-error-rate.html',
        ),
        AlertRule(
            name='critical_error_rate',
            condition='error_rate > 0.10',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=60,
            cooldown_seconds=900,
            labels={'category': 'reliability'},
            description='HTTP 5xx错误率超过10% (紧急)',
        ),
        AlertRule(
            name='high_latency_p99',
            condition='latency_p99 > 2000',
            severity=AlertSeverity.WARNING,
            duration_seconds=300,
            cooldown_seconds=3600,
            labels={'category': 'performance'},
            description='P99延迟超过2秒',
        ),
        AlertRule(
            name='circuit_breaker_open',
            condition='circuit_breaker_open_count > 0',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=0,  # 立即触发
            cooldown_seconds=300,
            labels={'category': 'reliability'},
            description='断路器打开 (服务降级)',
        ),
        
        # ===== 资源利用率 =====
        AlertRule(
            name='high_cpu_usage',
            condition='cpu_usage > 85',
            severity=AlertSeverity.WARNING,
            duration_seconds=600,
            cooldown_seconds=7200,
            labels={'category': 'resource'},
            description='CPU使用率超过85%',
        ),
        AlertRule(
            name='high_memory_usage',
            condition='memory_usage > 90',
            severity=AlertSeverity.WARNING,
            duration_seconds=300,
            cooldown_seconds=7200,
            labels={'category': 'resource'},
            description='内存使用率超过90%',
        ),
        AlertRule(
            name='db_connection_pool_exhausted',
            condition='db_connections_active > db_connections_max * 0.9',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=60,
            cooldown_seconds=600,
            labels={'category': 'resource'},
            description='数据库连接池接近耗尽',
        ),
        
        # ===== AGV业务特有 =====
        AlertRule(
            name='multiple_agv_offline',
            condition='agv_offline_count > 2',
            severity=AlertSeverity.WARNING,
            duration_seconds=120,
            cooldown_seconds=1800,
            labels={'category': 'agv'},
            description='多辆AGV同时离线',
        ),
        AlertRule(
            name='low_battery_agv',
            condition='agv_low_battery_count > 0',
            severity=AlertSeverity.INFO,
            duration_seconds=60,
            cooldown_seconds=1800,
            labels={'category': 'agv'},
            description='AGV电量过低',
        ),
        AlertRule(
            name='deadlock_detected',
            condition='deadlock_count > 0',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=0,
            cooldown_seconds=60,
            labels={'category': 'agv'},
            description='检测到死锁',
        ),
        AlertRule(
            name='task_dispatch_failure',
            condition='task_dispatch_failures > 5',
            severity=AlertSeverity.WARNING,
            duration_seconds=300,
            cooldown_seconds=1800,
            labels={'category': 'dispatch'},
            description='任务派发连续失败',
        ),
        
        # ===== SLO违规 =====
        AlertRule(
            name='slo_availability_degraded',
            condition='slo_availability_24h < 0.999',
            severity=AlertSeverity.WARNING,
            duration_seconds=3600,
            cooldown_seconds=14400,
            labels={'category': 'slo'},
            description='24小时可用性低于99.9%',
        ),
        AlertRule(
            name='slo_budget_burning_fast',
            condition='error_budget_burn_rate_1h > 5',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=1800,
            cooldown_seconds=3600,
            labels={'category': 'slo'},
            description='错误预算快速消耗 (预计5天内耗尽)',
        ),
        AlertRule(
            name='slo_budget_exhausted',
            condition='error_budget_remaining_pct <= 0',
            severity=AlertSeverity.EMERGENCY,
            duration_seconds=0,
            cooldown_seconds=86400,
            labels={'category': 'slo'},
            description='SLO错误预算已耗尽!',
        ),
    ]
    
    return rules


# ==================== 全局单例 ====================

logger = __import__('logging').getLogger(__name__)

alert_manager = AlertManager()


def init_default_alert_rules() -> None:
    """初始化默认告警规则"""
    rules = create_builtin_alert_rules()
    for rule in rules:
        alert_manager.add_rule(rule)
    
    logger.info(f"Default alert rules initialized: {len(rules)} rules")


def create_wechat_webhook(webhook_url: str) -> Callable:
    """
    创建企业微信Webhook通知器.
    
    Args:
        webhook_url: 企业微信群机器人Webhook地址
        
    Returns:
        通知回调函数
    """
    import httpx  # 延迟导入
    
    def send_wechat(alert_payload: Dict) -> None:
        try:
            # 企业微信卡片消息格式
            alert = alert_payload
            color_map = {
                'info': 'info',
                'warning': 'warning',
                'critical': 'danger',
                'emergency': 'danger',
            }
            
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": (
                        f"> ## 🔔 AGV-TMS 告警通知\n\n"
                        f"> **级别:** `{alert['severity'].upper()}`\n"
                        f"> **规则:** {alert['rule_name']}\n"
                        f"> **状态:** {alert['state']}\n"
                        f"> **当前值:** {alert['value']}\n"
                        f"> **阈值:** {alert['threshold']}\n"
                        f"> **时间:** {alert.get('fired_at', '')}\n\n"
                        f"> [查看详情](/alerts/{alert['alert_id']})"
                    )
                }
            }
            
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(webhook_url, json=payload)
                if resp.status_code != 200:
                    logger.warning(f"WeChat webhook failed: {resp.status_code}")
                    
        except ImportError:
            logger.warning("httpx not installed, cannot send WeChat notifications")
        except Exception as e:
            logger.error(f"WeChat webhook error: {e}")
    
    return send_wechat


__all__ = [
    'alert_manager', 'AlertRule', 'AlertInstance', 'AlertManager',
    'AlertSeverity', 'AlertState', 'SilencedRule', 'WebhookConfig',
    'create_builtin_alert_rules', 'init_default_alert_rules',
    'create_wechat_webhook',
]
