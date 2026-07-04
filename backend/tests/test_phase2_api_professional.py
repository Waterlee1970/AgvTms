"""
Phase 2 API专业化升级 - 单元测试套件

测试范围:
1. 分页/排序/搜索参数解析 (deps.py)
2. 请求校验器 (validators.py)
3. 批量操作处理器 (batch_operations.py)
4. 安全性校验
5. 过滤参数白名单

覆盖率目标: 90%+
运行命令:
    pytest tests/test_phase2_api_professional.py -v --cov=app/api --cov-report=term-missing
"""

import pytest
import asyncio
from typing import Dict, Any, List
from unittest.mock import AsyncMock, patch


# ==================== 1. PaginationParams 测试 ====================

class TestPaginationParams:
    """分页参数校验测试"""
    
    def test_default_values(self):
        """默认值应为 page=1, page_size=20"""
        from app.api.deps import PaginationParams
        
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == 20
    
    def test_offset_calculation(self):
        """offset计算正确性: offset = (page-1) * page_size"""
        from app.api.deps import PaginationParams
        
        # 第1页: offset=0
        assert PaginationParams(page=1, page_size=20).offset == 0
        
        # 第3页: offset=40
        assert PaginationParams(page=3, page_size=20).offset == 40
        
        # 第10页,每页50: offset=450
        assert PaginationParams(page=10, page_size=50).offset == 450
    
    def test_page_minimum_validation(self):
        """page最小值为1"""
        from app.api.deps import PaginationParams
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            PaginationParams(page=0)
        
        with pytest.raises(ValidationError):
            PaginationParams(page=-1)
    
    def test_page_size_range_validation(self):
        """page_size应在[1, 100]范围内"""
        from app.api.deps import PaginationParams
        from pydantic import ValidationError  # 添加缺失的导入
        
        # 边界值应通过
        assert PaginationParams(page_size=1).page_size == 1
        assert PaginationParams(page_size=100).page_size == 100
        
        # 超出范围应失败
        with pytest.raises(ValidationError):
            PaginationParams(page_size=101)


class TestSortParams:
    """排序参数解析测试"""
    
    def test_default_sort(self):
        """默认应按id降序"""
        from app.api.deps import SortParams
        
        sort = SortParams(sort_by=None)
        parsed = sort.parsed
        
        assert len(parsed) == 1
        assert parsed[0].field == "id"
    
    def test_single_field_sort(self):
        """单字段排序解析"""
        from app.api.deps import SortParams
        
        sort = SortParams(sort_by="created_at:asc")
        parsed = sort.parsed
        
        assert len(parsed) == 1
        assert parsed[0].field == "created_at"
        assert parsed[0].order == "asc"
    
    def test_multi_field_sort(self):
        """多字段逗号分隔排序"""
        from app.api.deps import SortParams
        
        sort = SortParams(sort_by="priority:desc,status:asc")
        parsed = sort.parsed
        
        assert len(parsed) == 2
        assert parsed[0].field == "priority"
        assert parsed[1].field == "status"


# ==================== 2. Validators 测试 ====================

class TestTaskRequestValidator:
    """任务请求校验器测试"""
    
    def test_valid_task_request(self):
        """有效请求应通过校验"""
        from app.api.validators import TaskRequestValidator
        
        valid_data = {
            "task_type": "agv_only",
            "pickup_node": "WH_A",
            "dropoff_node": "LINE_B",
            "priority": 10,
        }
        
        errors = TaskRequestValidator.validate_create(valid_data)
        assert len(errors) == 0
    
    def test_invalid_task_type(self):
        """无效task_type应报错"""
        from app.api.validators import TaskRequestValidator
        
        data = {"task_type": "invalid_type"}
        errors = TaskRequestValidator.validate_create(data)
        
        task_type_errors = [e for e in errors if e.field == "task_type"]
        assert len(task_type_errors) > 0
        assert any("ENUM_INVALID" == err.code for err in task_type_errors)
    
    def test_same_pickup_dropoff(self):
        """起终点相同应报错"""
        from app.api.validators import TaskRequestValidator
        
        data = {"pickup_node": "SAME", "dropoff_node": "SAME"}
        errors = TaskRequestValidator.validate_create(data)
        
        same_loc_errors = [e for e in errors if e.field == "dropoff_node"]
        assert len(same_loc_errors) > 0
        assert any("SAME_LOCATION" == err.code for err in same_loc_errors)
    
    def test_priority_out_of_range(self):
        """优先级超出范围应报错"""
        from app.api.validators import TaskRequestValidator
        
        data = {"priority": 150}
        errors = TaskRequestValidator.validate_create(data)
        
        priority_errors = [e for e in errors if e.field == "priority"]
        assert len(priority_errors) > 0
        assert any("RANGE_ERROR" == err.code for err in priority_errors)


class TestVehicleRequestValidator:
    """车辆请求校验器测试"""
    
    def test_valid_vehicle_id_formats(self):
        """合法vehicle_id格式应通过"""
        from app.api.validators import VehicleRequestValidator
        
        valid_ids = ["AGV-001", "forklift_v2", "AMR-TEST-123", "a"*50]
        
        for vid in valid_ids:
            error = VehicleRequestValidator.validate_id(vid)
            assert error is None, f"'{vid}' should be valid"
    
    def test_invalid_vehicle_id_formats(self):
        """非法ID格式检测"""
        from app.api.validators import VehicleRequestValidator
        
        invalid_cases = [
            ("", "空字符串"),
            ("-starts-with-dash", "以连字符开头"),
            ("x"*51, "超过最大长度"),
            ("has space", "包含空格"),
        ]
        
        for vid, reason in invalid_cases:
            error = VehicleRequestValidator.validate_id(vid)
            assert error is not None, f"'{vid}' ({reason}) should be invalid"


class TestSecurityValidator:
    """安全性校验器测试"""
    
    def test_safe_strings_pass(self):
        """正常文本应通过安全检查"""
        from app.api.validators import SecurityValidator
        
        safe_inputs = ["Hello World", "Task-123_ABC", "价格 ¥100"]
        
        for text in safe_inputs:
            assert SecurityValidator.check_sql_injection(text) is True
            assert SecurityValidator.check_xss(text) is True
    
    def test_sql_injection_detected(self):
        """SQL注入模式应被检测"""
        from app.api.validators import SecurityValidator
        
        injection_attempts = [
            "' OR '1'='1",
            "DROP TABLE users;--",
            "1; DELETE FROM tasks",
            "admin' UNION SELECT * FROM users--",
        ]
        
        for attempt in injection_attempts:
            assert SecurityValidator.check_sql_injection(attempt) is False, \
                f"Should detect SQL injection: '{attempt}'"
    
    def test_xss_detected(self):
        """XSS攻击模式应被检测"""
        from app.api.validators import SecurityValidator
        
        xss_attempts = [
            "<script>alert('XSS')</script>",
            "javascript:void(0)",
        ]
        
        for attempt in xss_attempts:
            assert SecurityValidator.check_xss(attempt) is False


# ==================== 3. BatchProcessor 测试 ====================

class TestBatchProcessor:
    """批量操作处理器测试"""
    
    @pytest.fixture
    def processor(self):
        from app.api.batch_operations import BatchProcessor
        return BatchProcessor(
            max_batch_size=5,
            stop_on_first_error=False,
            concurrency_limit=2,
        )
    
    @pytest.mark.asyncio
    async def test_all_items_success(self, processor):
        """所有项目成功时应返回全部succeeded"""
        items = [{"name": f"item-{i}"} for i in range(3)]
        
        async def mock_process(item, **ctx):
            return {"processed": item["name"], "status": "ok"}
        
        result = await processor.execute(items=items, process_fn=mock_process)
        
        assert result.total_items == 3
        assert result.succeeded_count == 3
        assert result.failed_count == 0
        assert result.status.value == "completed"
    
    @pytest.mark.asyncio
    async def test_partial_failure(self, processor):
        """部分项目失败时应记录详情"""
        items = [{"should_fail": i == 1} for i in range(3)]
        
        async def conditional_fail(item, **ctx):
            if item.get("should_fail"):
                raise ValueError("Intentional failure")
            return {"status": "ok"}
        
        result = await processor.execute(items=items, process_fn=conditional_fail)
        
        assert result.succeeded_count == 2
        assert result.failed_count == 1
        assert len(result.errors_summary) == 1
    
    @pytest.mark.asyncio
    async def test_batch_too_large(self, processor):
        """超出批次大小限制应直接返回错误"""
        items = [{"i": i} for i in range(10)]  # max_batch_size=5
        
        async def dummy(item, **ctx):
            pass
        
        result = await processor.execute(items=items, process_fn=dummy)
        
        assert result.status.value == "failed"
        assert any("BATCH_TOO_LARGE" == err.get('code') for err in result.errors_summary)


# ==================== 4. Filter Params 校验测试 ====================

class TestFilterParamsValidation:
    """过滤参数白名单校验"""
    
    def test_allowed_fields_pass(self):
        """允许的字段应通过校验"""
        from app.api.validators import validate_filter_params
        
        params = {"status": "pending", "priority_min": "10"}
        allowed = {"status", "priority_min", "priority_max"}
        
        errors = validate_filter_params(params, allowed_fields=allowed)
        assert len(errors) == 0
    
    def test_disallowed_field_rejected(self):
        """不允许的字段应被拒绝"""
        from app.api.validators import validate_filter_params
        
        params = {"status": "ok", "hack_field": "malicious"}
        allowed = {"status"}
        
        errors = validate_filter_params(params, allowed_fields=allowed)
        
        field_errors = [e for e in errors if e.field == "hack_field"]
        assert len(field_errors) > 0
        assert any("FIELD_NOT_ALLOWED" == err.code for err in field_errors)
    
    def test_sql_injection_in_filter(self):
        """过滤参数中的SQL注入尝试应被检测"""
        from app.api.validators import validate_filter_params
        
        params = {"search": "' OR 1=1; DROP TABLE--"}
        allowed = {"search"}
        
        errors = validate_filter_params(params, allowed_fields=allowed)
        
        security_errors = [e for e in errors if e.code == "SECURITY_RISK"]
        assert len(security_errors) > 0


# ==================== 5. OpenAPI Config 测试 ====================

class TestOpenAPIConfig:
    """OpenAPI配置正确性验证"""
    
    def test_tags_defined(self):
        """Tags列表不应为空且包含核心模块"""
        from app.api.openapi_config import OPENAPI_TAGS
        
        tag_names = {tag["name"] for tag in OPENAPI_TAGS}
        
        required_tags = {"Tasks", "Vehicles", "Map", "Scheduling"}
        assert required_tags.issubset(tag_names), \
            f"Missing tags: {required_tags - tag_names}"
    
    def test_security_schemes_defined(self):
        """安全定义应包含Bearer Token和API Key"""
        from app.api.openapi_config import SECURITY_SCHEMES
        
        assert "bearerAuth" in SECURITY_SCHEMES
        assert "apiKeyAuth" in SECURITY_SCHEMES
        
        assert SECURITY_SCHEMES["bearerAuth"]["type"] == "http"
        assert SECURITY_SCHEMES["bearerAuth"]["scheme"] == "bearer"
    
    def test_example_responses_exist(self):
        """预定义响应示例应覆盖主要场景"""
        from app.api.openapi_config import EXAMPLE_RESPONSES
        
        expected_keys = {
            "success_create_task",
            "paginated_list", 
            "error_validation",
            "error_not_found",
            "error_rate_limited",
            "error_server",
        }
        
        assert expected_keys.issubset(set(EXAMPLE_RESPONSES.keys()))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
