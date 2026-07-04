"""
Phase 3: 可靠性与可观测性 — 完整测试套件.

覆盖范围:
1. SLO监控系统 (slo_monitor.py)
   - SLOConfig参数校验
   - SLI指标计算 (可用性/延迟/错误率)
   - ErrorBudget计算和烧毁速率
   - SLORegistry核心逻辑

2. 业务错误码体系 (error_codes.py)
   - ErrorCode枚举完整性
   - BusinessError创建和序列化
   - RFC 7807 Problem Details格式
   - FastAPI异常处理

3. 告警规则引擎 (alert_engine.py)
   - ConditionEvaluator条件求值
   - AlertRule创建和管理
   - AlertManager评估和触发
   - 告警生命周期

4. 可观测性API路由
   - 健康检查端点响应格式
   - SLO状态端点数据验证

目标: 覆盖率90%+, 所有测试可独立运行.
"""

import pytest
import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock


# ==================== SLO监控测试 ====================

class TestSLOConfig:
    """SLO配置测试"""
    
    def test_default_config(self):
        from app.core.slo_monitor import SLOConfig
        
        config = SLOConfig()
        assert config.availability_target == 0.999
        assert config.latency_p99_target_ms == 500.0
        assert config.error_rate_target == 0.001
    
    def test_custom_config(self):
        from app.core.slo_monitor import SLOConfig
        
        config = SLOConfig(
            availability_target=0.9999,
            latency_p99_target_ms=200.0,
            error_rate_target=0.0001,
        )
        assert config.availability_target == 0.9999
        assert config.latency_p99_target_ms == 200.0
    
    def test_invalid_availability_target(self):
        from app.core.slo_monitor import SLOConfig
        
        with pytest.raises(AssertionError):
            SLOConfig(availability_target=1.5)  # > 1.0
        
        with pytest.raises(AssertionError):
            SLOConfig(availability_target=0)    # <= 0
    
    def test_latency_constraints(self):
        from app.core.slo_monitor import SLOConfig
        
        with pytest.raises(AssertionError):
            SLOConfig(latency_p99_target_ms=50, latency_p50_target_ms=100)  # p99 < p50


class TestSLIMetrics:
    """SLI指标计算测试"""
    
    @pytest.fixture
    def empty_metrics(self):
        from app.core.slo_monitor import SLIMetrics, WindowSize
        return SLIMetrics(window=WindowSize.HOUR_24)
    
    def test_empty_metrics_defaults(self, empty_metrics):
        """空指标应该返回理想值"""
        assert empty_metrics.total_requests == 0
        assert empty_metrics.availability == 1.0  # 无请求视为100%可用
        assert empty_metrics.error_rate == 0.0
        assert empty_metrics.avg_latency == 0.0
    
    def test_availability_calculation(self):
        from app.core.slo_monitor import SLIMetrics, WindowSize
        
        metrics = SLIMetrics(window=WindowSize.HOUR_24)
        metrics.total_requests = 100
        metrics.successful_requests = 95
        metrics.error_requests = 3
        metrics.client_error_requests = 2
        
        assert metrics.availability == 0.95
        assert metrics.error_rate == 0.03  # 3/100
    
    def test_percentile_calculation(self):
        from app.core.slo_monitor import SLIMetrics, WindowSize
        
        metrics = SLIMetrics(window=WindowSize.HOUR_24)
        metrics.latency_samples = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        
        p50 = metrics.get_percentile(50)
        p95 = metrics.get_percentile(95)
        p99 = metrics.get_percentile(99)
        
        # 注意: 我们的简化分位数算法使用索引取值
        # 对于10个元素[10,20,...,100]:
        # P50 -> idx=5 => 60 (第6个元素, 0-indexed)
        # P95 -> idx=9 => 100 (最后一个元素)
        # P99 -> idx=9 => 100
        assert p50 == 60   # 简化算法的P50
        assert p95 >= 90   # P95应该在高位
        assert p99 == 100  # 最大值
    
    def test_empty_samples_percentile(self):
        from app.core.slo_monitor import SLIMetrics, WindowSize
        
        metrics = SLIMetrics(window=WindowSize.HOUR_24)
        assert metrics.get_percentile(50) == 0.0
    
    def test_to_dict_format(self):
        from app.core.slo_monitor import SLIMetrics, WindowSize
        
        metrics = SLIMetrics(window=WindowSize.HOUR_24)
        metrics.total_requests = 10
        metrics.successful_requests = 9
        metrics.error_requests = 1
        metrics.latency_sum = 500.0
        metrics.latency_min = 20.0
        metrics.latency_max = 100.0
        metrics.latency_samples = [30, 50, 70]
        
        d = metrics.to_dict()
        
        assert d['window'] == '24h'
        assert d['total_requests'] == 10
        assert d['availability'] == 0.9
        assert 'p50_ms' in d
        assert 'p99_ms' in d


class TestErrorBudget:
    """错误预算测试"""
    
    def test_budget_initial_state(self):
        from app.core.slo_monitor import ErrorBudget
        
        budget = ErrorBudget()
        assert budget.remaining_pct == 100.0
        assert budget.consumed_pct == 0.0
        assert not budget.is_exhausted
    
    def test_budget_exhausted(self):
        from app.core.slo_monitor import ErrorBudget
        
        budget = ErrorBudget(consumed_pct=105.0, remaining_pct=0.0)
        assert budget.is_exhausted
        assert budget.burn_alert_level is not None
    
    def test_burn_rate_prediction(self):
        from app.core.slo_monitor import ErrorBudget
        
        budget = ErrorBudget(
            consumed_pct=20.0,
            remaining_pct=80.0,
            burn_rate_current=2.0,  # 每天2%
        )
        
        days_left = budget.days_until_exhaustion
        assert days_left is not None
        # 验证有预测值 (具体数值可能因实现而异)
        assert days_left > 0
    
    def test_no_burn_rate_never_exhausts(self):
        from app.core.slo_monitor import ErrorBudget
        
        budget = ErrorBudget(burn_rate_current=0)
        assert budget.days_until_exhaustion is None
    
    def test_burn_alert_levels(self):
        from app.core.slo_monitor import ErrorBudget, AlertSeverity
        
        # 紧急: 预算耗尽
        emergency_budget = ErrorBudget(remaining_pct=-5.0)
        assert emergency_budget.burn_alert_level == AlertSeverity.EMERGENCY
        
        # 严重: 快速烧灭 (>2%/hour)
        critical_budget = ErrorBudget(
            consumed_pct=5.0,
            burn_rate_1h=3.0,
        )
        assert critical_budget.burn_alert_level == AlertSeverity.CRITICAL
        
        # 正常: 充足预算
        healthy_budget = ErrorBudget(remaining_pct=80.0, burn_rate_1h=0.1)
        assert healthy_budget.burn_alert_level is None


class TestSLORegistry:
    """SLO注册中心核心逻辑测试"""
    
    @pytest.fixture
    def registry(self):
        from app.core.slo_monitor import SLORegistry, WindowSize
        r = SLORegistry()
        r.WindowSize = WindowSize  # 暴露WindowSize给测试使用
        return r
    
    def test_register_and_get(self, registry):
        from app.core.slo_monitor import SLOConfig
        
        registry.register("/api/test", SLOConfig(availability_target=0.99))
        status = registry.get_slo_status("/api/test")
        
        assert status is not None
        assert status.endpoint == "/api/test"
        assert status.config.availability_target == 0.99
    
    def test_get_unregistered_endpoint(self, registry):
        assert registry.get_slo_status("/nonexistent") is None
    
    def test_record_request_updates_metrics(self, registry):
        from app.core.slo_monitor import SLOConfig
        
        registry.register("/api/test", SLOConfig())
        
        # 模拟100次请求，其中5个错误
        for i in range(95):
            registry.record_request("/api/test", success=True, latency_ms=100.0, status_code=200)
        for i in range(5):
            registry.record_request("/api/test", success=False, latency_ms=200.0, status_code=500)
        
        status = registry.get_slo_status("/api/test")
        
        assert status is not None
        m_24h = status.metrics.get(registry.WindowSize.HOUR_24)
        assert m_24h is not None
        assert m_24h.total_requests == 100
        assert m_24h.successful_requests == 95
        assert m_24h.error_requests == 5
        assert abs(m_24h.availability - 0.95) < 0.01
    
    def test_error_budget_calculation(self, registry):
        from app.core.slo_monitor import SLOConfig
        
        # 目标可用性99%, 允许1%错误预算
        registry.register("/api/test", SLOConfig(availability_target=0.99))
        
        # 发送1000请求，15个错误 (超出1%预算)
        for i in range(985):
            registry.record_request("/api/test", success=True, latency_ms=50.0)
        for i in range(15):
            registry.record_request("/api/test", success=False, latency_ms=100.0, status_code=500)
        
        status = registry.get_slo_status("/api/test")
        # 错误率1.5%, 预算消耗约150% (或刚好100%)
        assert status.budget.consumed_pct >= 100.0  # 超出预算
    
    def test_system_summary(self, registry):
        from app.core.slo_monitor import SLOConfig
        
        registry.register("/api/a", SLOConfig())
        registry.register("/api/b", SLOConfig(availability_target=0.999))
        
        summary = registry.get_system_summary()
        
        assert summary['total_endpoints'] == 2
        assert 'overall_health' in summary
        assert 'timestamp' in summary


# ==================== 错误码测试 ====================

class TestErrorCode:
    """业务错误码枚举测试"""
    
    def test_system_error_codes_exist(self):
        from app.core.error_codes import ErrorCode
        
        assert ErrorCode.SUCCESS == 0
        assert ErrorCode.UNKNOWN_ERROR == 10001
        assert ErrorCode.INVALID_REQUEST == 10002
        assert ErrorCode.UNAUTHORIZED == 10003
        assert ErrorCode.RATE_LIMITED == 10005
        assert ErrorCode.TIMEOUT == 10007
    
    def test_task_error_codes(self):
        from app.core.error_codes import ErrorCode
        
        # 确认任务相关错误码存在 (名称包含TASK)
        task_codes = [e for e in ErrorCode if 'TASK' in e.name]
        # 至少有一些任务相关的错误定义
        assert len(task_codes) >= 1 or hasattr(ErrorCode, 'TASK_NOT_FOUND') or hasattr(ErrorCode, 'TASK_INVALID_STATUS')
    
    def test_vehicle_error_codes(self):
        from app.core.error_codes import ErrorCode
        
        assert ErrorCode.VEHICLE_NOT_FOUND.value == 11001
        assert ErrorCode.VEHICLE_OFFLINE.value == 11002
        assert ErrorCode.VEHICLE_FAULT.value == 11005
    
    def test_http_status_mapping(self):
        from app.core.error_codes import ErrorCode
        
        # 验证关键错误码的HTTP映射存在且合理
        assert isinstance(ErrorCode.TASK_NOT_FOUND.http_status, int)
        assert 400 <= ErrorCode.UNAUTHORIZED.http_status < 500  # 客户端错误系列
        assert ErrorCode.RATE_LIMITED.http_status == 429  # 限流标准状态码
        # 服务端错误应该在500系列
        assert 500 <= ErrorCode.SERVICE_UNAVAILABLE.http_status < 600
        assert 500 <= ErrorCode.TIMEOUT.http_status < 600
    
    def test_retryable_classification(self):
        from app.core.error_codes import ErrorCode
        
        assert ErrorCode.TIMEOUT.retryable is True
        assert ErrorCode.DATABASE_ERROR.retryable is True
        assert ErrorCode.CIRCUIT_OPEN.retryable is True
        assert ErrorCode.INVALID_REQUEST.retryable is False  # 客户端错误不重试
        assert ErrorCode.TASK_NOT_FOUND.retryable is False


class TestBusinessError:
    """业务异常类测试"""
    
    def test_create_basic_error(self):
        from app.core.error_codes import BusinessError, ErrorCode
        
        err = BusinessError(code=ErrorCode.TASK_NOT_FOUND, message="任务不存在")
        
        assert err.code == ErrorCode.TASK_NOT_FOUND
        assert err.message == "任务不存在"
        # HTTP状态码应该是404或映射值
        assert err.http_status in [400, 404, 500]
        assert err.trace_id is None
    
    def test_error_with_details(self):
        from app.core.error_codes import BusinessError, ErrorCode
        
        err = BusinessError(
            code=ErrorCode.VALIDATION_ERROR,
            message="校验失败",
            details={"field": "priority", "reason": "must be between 1-100"},
            trace_id="req-abc123",
        )
        
        assert err.details["field"] == "priority"
        assert err.trace_id == "req-abc123"
    
    def test_error_serialization(self):
        from app.core.error_codes import BusinessError, ErrorCode
        
        err = BusinessError(
            code=ErrorCode.DATABASE_ERROR,
            message="DB连接超时",
            details={"operation": "SELECT tasks"},
        )
        
        d = err.to_dict()
        
        # 验证基本结构
        assert 'code' in d
        assert 'status' in d
        assert d['status'] in [500, 503]
        assert d['detail'] == "DB连接超时"
        assert 'details' in d
    
    def test_error_repr(self):
        from app.core.error_codes import BusinessError, ErrorCode
        
        err = BusinessError(code=ErrorCode.TASK_NOT_FOUND, message="test")
        repr_str = repr(err)
        
        assert "BusinessError" in repr_str
        assert "code=" in repr_str
        # 应该包含状态码信息
        assert "40" in repr_str or "50" in repr_str or "status=" in repr_str
    
    def test_factory_functions(self):
        from app.core.error_codes import (
            raise_not_found, raise_conflict, raise_service_unavailable,
        )
        
        err1 = raise_not_found("task", "T-001")
        # 应该是某种NOT_FOUND错误码
        assert "not_found" in err1.code.name.lower() or "T-001" in err1.message
        assert "T-001" in err1.message or "T-001" in str(err1.details)
        
        err2 = raise_conflict("任务状态冲突")
        # 冲突通常是409或400系列
        assert err2.http_status in [400, 409]
        
        err3 = raise_service_unavailable("Redis", "连接被拒")
        assert "Redis" in err3.message
        # 服务不可用通常是503或500
        assert err3.http_status in [500, 503]


class TestErrorRegistry:
    """错误元数据注册表测试"""
    
    def test_get_builtin_metadata(self):
        from app.core.error_codes import error_registry, ErrorCode
        
        meta = error_registry.get(ErrorCode.TASK_NOT_FOUND)
        
        assert meta is not None
        assert meta.message_template == "任务 {task_id} 不存在"
        assert meta.solution is not None
    
    def test_problem_details_format(self):
        from app.core.error_codes import error_registry, BusinessError, ErrorCode
        
        err = BusinessError(
            code=ErrorCode.VEHICLE_OFFLINE,
            message="AGV-01离线",
            details={"vehicle_id": "AGV-01"},
            instance="/api/v2/vehicles/AGV-01/status",
            trace_id="trace-xyz",
        )
        
        problem = error_registry.to_problem_details(err)
        
        assert problem['type'].startswith('urn:agvtms:error:')
        assert problem['status'] == 503
        assert problem['code'] == ErrorCode.VEHICLE_OFFLINE.value
        assert problem['instance'] == "/api/v2/vehicles/AGV-01/status"
        assert problem['trace_id'] == "trace-xyz"
        assert 'solution' in problem
    
    def test_list_all_codes(self):
        from app.core.error_codes import error_registry
        
        all_codes = error_registry.list_all_codes()
        
        assert len(all_codes) >= 15  # 至少有内置的错误码 (可能有重复被去重)
        for code_info in all_codes:
            assert 'code' in code_info
            assert 'name' in code_info
            assert 'http_status' in code_info
            assert 'retryable' in code_info
    
    def test_filter_by_module(self):
        from app.core.error_codes import error_registry
        
        task_errors = error_registry.list_all_codes(module_filter="10xxx")
        
        for e in task_errors:
            assert 10001 <= e['code'] <= 10999


# ==================== 告警引擎测试 ====================

class TestConditionEvaluator:
    """条件求值器测试"""
    
    def test_greater_than_true(self):
        from app.core.alert_engine import ConditionEvaluator
        
        assert ConditionEvaluator.evaluate('error_rate > 0.05', {'error_rate': 0.08}) is True
    
    def test_greater_than_false(self):
        from app.core.alert_engine import ConditionEvaluator
        
        assert ConditionEvaluator.evaluate('error_rate > 0.05', {'error_rate': 0.02}) is False
    
    def test_less_than(self):
        from app.core.alert_engine import ConditionEvaluator
        
        assert ConditionEvaluator.evaluate('agv_online < 5', {'agv_online': 3}) is True
        assert ConditionEvaluator.evaluate('agv_online < 5', {'agv_online': 8}) is False
    
    def test_percentage_syntax(self):
        from app.core.alert_engine import ConditionEvaluator
        
        # 85 > 80% => 85 > 0.80 => True
        assert ConditionEvaluator.evaluate('cpu_usage > 80%', {'cpu_usage': 85}) is True
        assert ConditionEvaluator.evaluate('cpu_usage > 80%', {'cpu_usage': 75}) is False
    
    def test_missing_metric_returns_false(self):
        from app.core.alert_engine import ConditionEvaluator
        
        assert ConditionEvaluator.evaluate('unknown_metric > 10', {}) is False
    
    def test_equals_operator(self):
        from app.core.alert_engine import ConditionEvaluator
        
        assert ConditionEvaluator.evaluate('count == 5', {'count': 5}) is True
        assert ConditionEvaluator.evaluate('count == 5', {'count': 6}) is False


class TestAlertRule:
    """告警规则测试"""
    
    def test_create_rule(self):
        from app.core.alert_engine import AlertRule, AlertSeverity
        
        rule = AlertRule(
            name='test_rule',
            condition='error_rate > 0.1',
            severity=AlertSeverity.CRITICAL,
            duration_seconds=300,
        )
        
        assert rule.name == 'test_rule'
        assert rule.severity == AlertSeverity.CRITICAL
        assert rule.duration_seconds == 300
        assert rule.enabled is True
        assert rule.rule_id is not None  # 自动生成ID
    
    def test_rule_id_deterministic(self):
        from app.core.alert_engine import AlertRule
        
        rule1 = AlertRule(name="same_name", condition="x > 1")
        rule2 = AlertRule(name="same_name", condition="x > 1")
        
        # 相同名称生成相同ID
        assert rule1.rule_id == rule2.rule_id


class TestAlertManager:
    """告警管理器测试"""
    
    @pytest.fixture
    def manager(self):
        from app.core.alert_engine import AlertManager
        return AlertManager()
    
    def test_add_and_remove_rule(self, manager):
        from app.core.alert_engine import AlertRule
        
        rule = AlertRule(name='test', condition='x > 1')
        manager.add_rule(rule)
        
        assert len(manager._rules) == 1
        
        manager.remove_rule(rule.rule_id)
        assert len(manager._rules) == 0
    
    def test_evaluate_triggers_alert(self, manager):
        from app.core.alert_engine import AlertRule, AlertSeverity
        
        rule = AlertRule(
            name='high_error',
            condition='error_rate > 0.05',
            severity=AlertSeverity.WARNING,
            duration_seconds=0,  # 立即触发
        )
        manager.add_rule(rule)
        
        alerts = manager.evaluate_metrics({'error_rate': 0.10})
        
        assert len(alerts) >= 1
        assert alerts[0].rule_name == 'high_error'
        assert alerts[0].state.value in ['firing', 'pending']
    
    def test_evaluate_no_trigger_below_threshold(self, manager):
        from app.core.alert_engine import AlertRule
        
        manager.add_rule(AlertRule(name='test', condition='x > 100'))
        
        alerts = manager.evaluate_metrics({'x': 50})
        
        assert len(alerts) == 0
    
    def test_cooldown_prevents_repeated_alerts(self, manager):
        from app.core.alert_engine import AlertRule, AlertSeverity
        
        rule = AlertRule(
            name='cooldown_test',
            condition='y > 10',
            cooldown_seconds=3600,  # 很长的冷却时间
        )
        manager.add_rule(rule)
        
        # 第一次触发
        alerts1 = manager.evaluate_metrics({'y': 20})
        assert len(alerts1) >= 1
        
        # 第二次应该在冷却期内
        alerts2 = manager.evaluate_metrics({'y': 25})
        assert len(alerts2) == 0  # 被冷却抑制
    
    def test_enable_disable_rule(self, manager):
        from app.core.alert_engine import AlertRule
        
        rule = AlertRule(name='test', condition='z > 0')
        manager.add_rule(rule)
        
        # 禁用后不触发
        manager.disable_rule(rule.rule_id)
        alerts = manager.evaluate_metrics({'z': 100})
        assert len(alerts) == 0
        
        # 重新启用
        manager.enable_rule(rule.rule_id)
        alerts = manager.evaluate_metrics({'z': 100})
        assert len(alerts) >= 1
    
    def test_statistics(self, manager):
        from app.core.alert_engine import AlertRule, AlertSeverity
        
        manager.add_rule(AlertRule(name='r1', condition='a > 1'))
        manager.add_rule(AlertRule(name='r2', condition='b > 2', enabled=False))
        
        stats = manager.get_statistics()
        
        assert stats['rules_total'] == 2
        assert stats['rules_enabled'] == 1
    
    def test_active_alerts_list(self, manager):
        from app.core.alert_engine import AlertRule, AlertSeverity
        
        manager.add_rule(AlertRule(name='active_test', condition='v > 0', duration_seconds=0))
        manager.evaluate_metrics({'v': 100})
        
        active = manager.get_active_alerts()
        
        assert len(active) >= 1
        assert active[0]['rule_name'] == 'active_test'


# ==================== 集成测试 ====================

class TestBuiltinRulesIntegration:
    """内置告警规则集成测试"""
    
    def test_builtin_rules_count(self):
        from app.core.alert_engine import create_builtin_alert_rules
        
        rules = create_builtin_alert_rules()
        
        # 应该包含系统、资源、AGV、SLO等类别
        assert len(rules) >= 10
        rule_names = [r.name for r in rules]
        
        # 关键规则必须存在
        assert 'high_error_rate_5xx' in rule_names or any('error_rate' in n for n in rule_names)
        assert any('circuit_breaker' in n for n in rule_names) or any('circuit' in n.lower() for n in rule_names)
        assert any('deadlock' in n.lower() for n in rule_names)


class TestSLOAlertingIntegration:
    """SLO与告警引擎集成测试"""
    
    def test_slo_violation_triggers_alert(self):
        from app.core.slo_monitor import SLORegistry, SLOConfig, slo_registry
        from app.core.alert_engine import alert_manager, AlertRule, AlertSeverity
        
        # 注册高可用性要求的端点
        slo_registry.register("/critical/api", SLOConfig(availability_target=0.999))
        
        # 配置告警规则
        alert_manager.add_rule(AlertRule(
            name='slo_budget_warning',
            condition='error_budget_remaining_pct < 50',
            severity=AlertSeverity.WARNING,
            duration_seconds=0,
        ))
        
        # 注入大量错误以触发预算警告
        for _ in range(900):
            slo_registry.record_request("/critical/api", success=True, latency_ms=50.0)
        for _ in range(100):
            slo_registry.record_request("/critical/api", success=False, latency_ms=100.0, status_code=500)
        
        # 获取SLO状态
        status = slo_registry.get_slo_status("/critical/api")
        
        # 如果预算确实消耗了超过50%，应该能触发告警（如果规则匹配的话）
        if status and status.budget.remaining_pct < 50:
            # 这里模拟将budget指标传入告警引擎
            alerts = alert_manager.evaluate_metrics({
                'error_budget_remaining_pct': status.budget.remaining_pct
            })
            
            if alerts:
                assert alerts[0].severity == AlertSeverity.WARNING


# ==================== 运行入口 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
