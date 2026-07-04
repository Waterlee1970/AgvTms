"""
AGV-TMS Locust 性能基准测试套件 — v2.3 (Phase 4.0 P2-03)

目标指标:
  - P50 响应时间 < 50ms
  - P99 响应时间 < 200ms
  - 最大吞吐 > 1000 RPS
  - 错误率 < 0.1%

用户角色:
  - AgvMonitorUser (70%): 只读负载 — 模拟前端监控页面
  - TaskOperatorUser (20%): 任务操作 — 模拟WMS任务下发
  - AdminUser (10%): 管理操作 — 模拟配置变更

用法:
  locust -f locustfile.py --host http://localhost:8000
  locust -f locustfile.py --headless -u 100 -r 10 --run-time 5m --csv results/baseline
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from locust import HttpUser, task, between, events, constant_pacing
    from locust.runners import MasterRunner, WorkerRunner
    from locust.env_utils import extra_options as locust_extra_options
    HAS_LOCUST = True
except ImportError:
    HAS_LOCUST = False
    print("[WARN] locust not installed: pip install locust")

import logging

logger = logging.getLogger(__name__)

# ==================== 配置常量 ====================

DEFAULT_HOST = "http://localhost:8000"

# API 端点定义（基于 backend/app/api/ 路由）
ENDPOINTS = {
    # 核心读取端点
    "agv_list": "/api/v1/agvs",
    "task_list": "/api/v1/tasks",
    "map_nodes": "/api/v1/map/nodes",
    "map_edges": "/api/v1/map/edges",
    "schedule_results": "/api/v1/schedule/results",
    "conveyors": "/api/v1/conveyors",
    "transfer_stations": "/api/v1/transfer-stations",
    "healthz": "/api/healthz",
    "health_detailed": "/api/v1/services/health/check",
    "metrics_prometheus": "/metrics",
    
    # 写入端点
    "create_task": "/api/v1/tasks",
    "trigger_schedule": "/api/v1/schedule/trigger",
    "agv_update": "/api/v1/agvs/{agv_id}",
    "wms_submit_order": "/api/wms/order",
    
    # 管理端点
    "algorithm_config": "/api/v1/algorithms/config",
    "system_info": "/api/v1/system/info",
    "evaluator_run": "/api/v1/evaluator/run",
    "simulation_reset": "/api/schedule/reset",
}


@dataclass
class SafeRequestResult:
    """安全请求结果包装器"""
    success: bool
    status_code: int = 0
    response_time_ms: float = 0.0
    response_length: int = 0
    error: str = ""
    data: Optional[Dict[str, Any]] = None


def safe_request(
    client: Any,
    method: str,
    url: str,
    name: str = "",
    **kwargs: Any,
) -> SafeRequestResult:
    """
    安全请求包装器 — 捕获所有异常避免压测脚本崩溃.
    
    Args:
        client: Locust HttpClient 实例
        method: HTTP 方法 (get/post/put/delete)
        url: 请求 URL
        name: Locust 统计名称
        **kwargs: 传递给请求的参数
    
    Returns:
        SafeRequestResult 包含结果或错误信息
    """
    start = time.time()
    default_name = f"{method.upper()} {url}"
    display_name = name or default_name
    
    try:
        if method.lower() == "get":
            with client.get(url, name=display_name, catch_response=True, **kwargs) as resp:
                resp_time = (time.time() - start) * 1000
                result = SafeRequestResult(
                    success=(200 <= resp.status_code < 400),
                    status_code=resp.status_code,
                    response_time_ms=resp_time,
                    response_length=len(resp.text or ""),
                )
                if result.success:
                    try:
                        result.data = resp.json()
                    except Exception:
                        pass
                    resp.success()
                else:
                    result.error = f"HTTP {resp.status_code}"
                    resp.failure(result.error)
                return result
                
        elif method.lower() == "post":
            with client.post(url, name=display_name, catch_response=True, **kwargs) as resp:
                resp_time = (time.time() - start) * 1000
                result = SafeRequestResult(
                    success=(200 <= resp.status_code < 400),
                    status_code=resp.status_code,
                    response_time_ms=resp_time,
                    response_length=len(resp.text or ""),
                )
                if result.success:
                    try:
                        result.data = resp.json()
                    except Exception:
                        pass
                    resp.success()
                else:
                    result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    resp.failure(result.error)
                return result
                
        elif method.lower() == "put":
            with client.put(url, name=display_name, catch_response=True, **kwargs) as resp:
                resp_time = (time.time() - start) * 1000
                return SafeRequestResult(
                    success=(200 <= resp.status_code < 400),
                    status_code=resp.status_code,
                    response_time_ms=resp_time,
                )
                
        elif method.lower() == "delete":
            with client.delete(url, name=display_name, catch_response=True, **kwargs) as resp:
                resp_time = (time.time() - start) * 1000
                return SafeRequestResult(
                    success=(200 <= resp.status_code < 400),
                    status_code=resp.status_code,
                    response_time_ms=resp_time,
                )
        else:
            return SafeRequestResult(success=False, error=f"Unsupported method: {method}")
            
    except Exception as e:
        resp_time = (time.time() - start) * 1000
        err_msg = f"{type(e).__name__}: {str(e)[:100]}"
        
        # 手动记录失败到 Locust
        try:
            client.environment.events.request.fire(
                request_type=method.upper(),
                name=display_name,
                response_time=resp_time,
                response_length=0,
                exception=e,
                context=None,
            )
        except Exception:
            pass
            
        return SafeRequestResult(success=False, response_time_ms=resp_time, error=err_msg)


# ==================== 任务数据生成器 ====================

class TestDataGenerator:
    """压测数据生成器"""
    
    AGV_TYPES = ["forklift", "pallet", "conveyor", "heavy", "mini"]
    TASK_PRIORITIES = ["low", "normal", "high", "urgent"]
    PICKUP_POINTS = [f"P{i:02d}" for i in range(1, 16)]  # P01-P15
    DROPOFF_POINTS = [f"D{i:02d}" for i in range(1, 13)]   # D01-D12
    CARGO_TYPES = ["box_small", "box_large", "pallet", "cylinder", "bag", "fragile"]
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self._agv_ids_cache: Optional[List[str]] = None
        self._node_ids_cache: Optional[List[str]] = None
    
    def random_agv_id(self) -> str:
        return f"agv_{random.randint(1, 100):03d}"
    
    def random_node_id(self) -> str:
        return f"node_{random.randint(1, 200):04d}"
    
    def generate_create_task_payload(self) -> Dict[str, Any]:
        """生成创建任务的请求体"""
        priority_weights = [0.15, 0.40, 0.35, 0.10]  # low/normal/high/urgent 分布
        priority = random.choices(self.TASK_PRIORITIES, weights=priority_weights)[0]
        
        return {
            "id": f"task_{int(time.time()*1000)}_{random.randint(1000,9999)}",
            "priority": priority,
            "pickup_point": random.choice(self.PICKUP_POINTS),
            "dropoff_point": random.choice(self.DROPOFF_POINTS),
            "cargo_type": random.choice(self.CARGO_TYPES),
            "cargo_weight_kg": round(random.uniform(1, 500), 1),
            "urgent": priority == "urgent",
            "metadata": {
                "source": "locust_benchmark",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        }
    
    def generate_agv_update_payload(self, agv_id: str) -> Dict[str, Any]:
        """生成AGV状态更新请求体"""
        return {
            "battery_level": round(random.uniform(20, 100), 1),
            "state": random.choice(["idle", "moving", "charging"]),
            "x": round(random.uniform(0, 200), 2),
            "y": round(random.uniform(0, 150), 2),
            "angle": round(random.uniform(-180, 180), 1),
        }
    
    def generate_wms_order_payload(self) -> Dict[str, Any]:
        """生成WMS订单提交请求体"""
        order_types = ["inbound", "outbound", "transfer", "cycle_count"]
        return {
            "order_id": f"WMS-{int(time.time())}-{random.randint(10000,99999)}",
            "order_type": random.choice(order_types),
            "items": [
                {
                    "sku": f"SKU-{random.randint(100000,999999)}",
                    "quantity": random.randint(1, 20),
                    "location_from": f"LOC-A{random.randint(1,30)}-{random.randint(1,10)}",
                    "location_to": f"LOC-B{random.randint(1,30)}-{random.randint(1,10)}",
                }
                for _ in range(random.randint(1, 3))
            ],
            "priority": random.choice(["standard", "expedite", "rush"]),
        }
    
    def generate_algorithm_config_payload(self) -> Dict[str, Any]:
        """生成算法配置更新请求体"""
        algos = ["aco", "sa", "mip", "astar", "hybrid_v1", "hybrid_v2"]
        return {
            "primary_algorithm": random.choice(algos),
            "params": {
                "max_iterations": random.choice([50, 100, 200, 500]),
                "population_size": random.choice([20, 50, 100]),
                "mutation_rate": round(random.uniform(0.01, 0.3), 3),
                "cross_over_rate": round(random.uniform(0.5, 0.95), 3),
            }
        }


# 全局数据生成器单例
_gen = TestDataGenerator()


# ==================== 用户类定义 ====================


if not HAS_LOCUST:
    # 创建 dummy 装饰器和基类以便导入不报错
    def task(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator
    
    class HttpUser:
        host = DEFAULT_HOST
        wait_time = between(1, 3)
        abstract = True
        
        def __init__(self, *args, **kwargs):
            pass


class AgvMonitorUser(HttpUser):
    """
    AGV 监控用户 (权重 70%) — 只读负载模拟.
    
    模拟前端监控页面的行为模式:
      - 定期刷新 AGV 列表
      - 查看 AGV 详情
      - 刷新地图数据
      - 查看输送线状态
      - 查看调度结果
      - 健康检查轮询
    """
    
    weight = 70
    wait_time = between(1, 5)  # 每 1-5 秒一个操作
    
    @task(25)
    def get_agv_list(self):
        """刷新 AGV 列表（最高频操作）"""
        params = {"limit": random.choice([20, 50, 100]), "offset": 0}
        safe_request(self.client, "get", ENDPOINTS["agv_list"], params=params)
    
    @task(15)
    def get_single_agv_detail(self):
        """查看单个 AGV 详情"""
        agv_id = _gen.random_agv_id()
        safe_request(self.client, "get", f"/api/v1/agvs/{agv_id}")
    
    @task(12)
    def get_task_list(self):
        """刷新任务列表"""
        params = {
            "status": random.choice(["pending", "executing", "completed", None]),
            "limit": random.choice([20, 50]),
        }
        # 清除None值
        params = {k: v for k, v in params.items() if v is not None}
        safe_request(self.client, "get", ENDPOINTS["task_list"], params=params)
    
    @task(10)
    def get_map_data(self):
        """获取地图节点和边"""
        endpoint_type = random.choices(
            ["nodes", "edges", "topology"],
            weights=[0.45, 0.35, 0.20]
        )[0]
        if endpoint_type == "nodes":
            safe_request(self.client, "get", ENDPOINTS["map_nodes"])
        elif endpoint_type == "edges":
            safe_request(self.client, "get", ENDPOINTS["map_edges"])
        else:
            safe_request(self.client, "get", "/api/v1/map/topology")
    
    @task(8)
    def get_conveyor_status(self):
        """查看输送线状态"""
        safe_request(self.client, "get", ENDPOINTS["conveyors"])
    
    @task(7)
    def get_transfer_station_status(self):
        """查看接驳台状态"""
        safe_request(self.client, "get", ENDPOINTS["transfer_stations"])
    
    @task(10)
    def get_schedule_results(self):
        """查看调度结果"""
        params = {"limit": random.choice([20, 50])}
        safe_request(self.client, "get", ENDPOINTS["schedule_results"], params=params)
    
    @task(8)
    def health_check(self):
        """健康检查轮询"""
        if random.random() < 0.7:
            safe_request(self.client, "get", ENDPOINTS["healthz"])
        else:
            safe_request(self.client, "get", ENDPOINTS["health_detailed"])
    
    @task(5)
    def get_system_metrics(self):
        """获取系统指标"""
        safe_request(self.client, "get", ENDPOINTS["metrics_prometheus"])


class TaskOperatorUser(HttpUser):
    """
    任务操作用户 (权重 20%) — 任务 CRUD 操作模拟.
    
    模拟 WMS / 上位系统的行为模式:
      - 提交新任务
      - 触发调度
      - 查询任务执行状态
      - 提交 WMS 订单
      - 取消/重新分配任务
    """
    
    weight = 20
    wait_time = between(2, 6)
    
    @task(30)
    def create_task(self):
        """创建新的搬运任务"""
        payload = _gen.generate_create_task_payload()
        safe_request(
            self.client, "post",
            ENDPOINTS["create_task"],
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(25)
    def trigger_schedule(self):
        """触发一次调度计算"""
        payload = {
            "mode": random.choice(["full", "incremental", "urgent_only"]),
            "optimization_target": random.choice(["time", "energy", "balance"]),
        }
        safe_request(
            self.client, "post",
            ENDPOINTS["trigger_schedule"],
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(20)
    def query_task_status(self):
        """查询任务执行状态"""
        safe_request(self.client, "get", ENDPOINTS["task_list"], params={
            "status": random.choice(["pending", "assigned", "in_progress", "completed"]),
            "limit": 20,
        })
    
    @task(15)
    def submit_wms_order(self):
        """提交 WMS 订单"""
        payload = _gen.generate_wms_order_payload()
        safe_request(
            self.client, "post",
            ENDPOINTS["wms_submit_order"],
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(5)
    def cancel_task(self):
        """取消任务"""
        task_id = f"task_{random.randint(100000, 999999)}"
        safe_request(self.client, "put", f"/api/v1/tasks/{task_id}/cancel")
    
    @task(5)
    def update_task_priority(self):
        """更新任务优先级"""
        task_id = f"task_{random.randint(100000, 999999)}"
        payload = {"priority": random.choice(["high", "urgent"])}
        safe_request(
            self.client, "put",
            f"/api/v1/tasks/{task_id}/priority",
            json=payload,
            headers={"Content-Type": "application/json"},
        )


class AdminUser(HttpUser):
    """
    管理员用户 (权重 10%) — 管理操作模拟.
    
    模拟运维人员的行为模式:
      - 修改算法参数
      - 更新 AGV 状态
      - 管理地图
      - 查看系统信息
      - 运行评测
      - 重置仿真环境
    """
    
    weight = 10
    wait_time = between(3, 8)
    
    @task(20)
    def update_algorithm_config(self):
        """更新算法配置"""
        payload = _gen.generate_algorithm_config_payload()
        safe_request(
            self.client, "put",
            ENDPOINTS["algorithm_config"],
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(18)
    def update_agv_status(self):
        """手动更新 AGV 状态（模拟外部事件）"""
        agv_id = _gen.random_agv_id()
        payload = _gen.generate_agv_update_payload(agv_id)
        url = ENDPOINTS["agv_update"].format(agv_id=agv_id)
        safe_request(
            self.client, "put",
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(12)
    def get_system_info(self):
        """获取系统信息"""
        safe_request(self.client, "get", ENDPOINTS["system_info"])
    
    @task(10)
    def run_evaluator(self):
        """运行算法评测"""
        payload = {
            "algorithms": random.sample(
                ["aco", "sa", "mip", "astar", "sipp", "theta_star", "cbs"],
                k=random.randint(2, 5),
            ),
            "scenario": random.choice(["small", "medium", "large"]),
            "runs": random.randint(1, 3),
        }
        safe_request(
            self.client, "post",
            ENDPOINTS["evaluator_run"],
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    
    @task(10)
    def manage_map(self):
        """地图管理操作"""
        map_actions = [
            ("GET", "/api/v1/map/info"),
            ("GET", "/api/v1/map/zones"),
            ("GET", "/api/v1/map/charging-stations"),
            ("GET", "/api/v1/map/pickup-points"),
            ("GET", "/api/v1/map/dropoff-points"),
        ]
        method, url = random.choice(map_actions)
        safe_request(self.client, method, url)
    
    @task(10)
    def reset_simulation(self):
        """重置仿真环境（低频）"""
        safe_request(self.client, "post", ENDPOINTS["simulation_reset"])
    
    @task(10)
    def get_monitoring_dashboard(self):
        """监控仪表盘数据"""
        endpoints = [
            "/api/v1/monitoring/summary",
            "/api/v1/monitoring/alerts",
            "/api/v1/analytics/throughput?hours=1",
            "/api/v1/analytics/efficiency?days=1",
        ]
        safe_request(self.client, "get", random.choice(endpoints))


# ==================== 测试阶段自定义 LoadShape ====================

class StagedLoadShape:
    """
    阶梯式加载策略.
    
    阶段:
      Stage 1:  0-2min →  10 users (预热)
      Stage 2:  2-4min →  50 users (低负载)
      Stage 3:  4-6min → 100 users (中负载)
      Stage 4:  6-8min → 200 users (高负载)
      Stage 5:  8-10min→ 500 users (峰值压力)
    """
    
    stages = [
        {"duration": 120, "users": 10, "spawn_rate": 5},
        {"duration": 120, "users": 50, "spawn_rate": 10},
        {"duration": 120, "users": 100, "spawn_rate": 15},
        {"duration": 120, "users": 200, "spawn_rate": 20},
        {"duration": 120, "users": 500, "spawn_rate": 30},
    ]
    
    def tick(self):
        run_time = self.get_run_time()
        
        elapsed = 0
        for stage in self.stages:
            elapsed += stage["duration"]
            if run_time < elapsed:
                return (stage["users"], stage["spawn_rate"])
        
        # 所有阶段完成，停止
        return (None, None)


# ==================== 事件钩子 ====================

def on_test_start(environment, **kwargs):
    """压测开始时调用"""
    logger.info("=" * 60)
    logger.info("AGV-TMS Performance Benchmark Starting")
    logger.info("=" * 60)
    logger.info("Target: %s", environment.host or DEFAULT_HOST)
    logger.info("Users: AgvMonitor(70%%) + TaskOperator(20%%) + Admin(10%%)")
    logger.info("Target Metrics: P50<50ms | P99<200ms | RPS>1000")


def on_test_stop(environment, **kwargs):
    """压测结束时调用"""
    logger.info("=" * 60)
    logger.info("AGV-TMS Performance Benchmark Complete")
    stats = environment.stats.total
    
    if stats:
        logger.info("Summary:")
        logger.info("  Total Requests: %d", stats.num_requests)
        logger.info("  Total Failures: %d", stats.num_failures)
        logger.info("  Response Time (avg): %.2fms", stats.avg_response_time)
        if hasattr(stats, 'response_time_percentile'):
            logger.info("  P50: %.2fms", stats.response_time_percentile(50))
            logger.info("  P90: %.2fms", stats.response_time_percentile(90))
            logger.info("  P95: %.2fms", stats.response_time_percentile(95))
            logger.info("  P99: %.2fms", stats.response_time_percentile(99))
        logger.info("  RPS: %.2f", stats.current_rps if stats.current_rps else 0)
        logger.info("  Error Rate: %.2f%%", (stats.num_failures / max(1, stats.num_requests)) * 100)
    
    logger.info("=" * 60)


def on_locust_init(environment, **kwargs):
    """Locust 启动初始化"""
    if not HAS_LOCUST:
        logger.warning("Locust not fully available, running in degraded mode")


# 注册事件钩子
if HAS_LOCUST:
    events.test_start.add_listener(on_test_start)
    events.test_stop.add_listener(on_test_stop)
    events.init.add_listener(on_locust_init)


# ==================== 直接运行支持 ====================

if __name__ == "__main__":
    if not HAS_LOCUST:
        print("""
╔══════════════════════════════════════════════════════╗
║     AGV-TMS Performance Benchmark Suite v2.3          ║
║                                                       ║
║     Requires: pip install locust                      ║
║                                                       ║
║     Usage:                                            ║
║       locust -f locustfile.py --host <URL>             ║
║       # Web UI mode                                   ║
║       locust -f locustfile.py --headless               ║
║              -u 100 -r 10 --run-time 5m               ║
║              --csv results/baseline                   ║
║                                                       ║
║     Targets:                                          ║
║       P50 < 50ms | P99 < 200ms | RPS > 1000           ║
║       Error Rate < 0.1%                               ║
╚══════════════════════════════════════════════════════╝
        """)
    else:
        import sys
        # 直接启动 locust
        sys.argv = [
            "locust",
            "-f", __file__,
            "--host", DEFAULT_HOST,
        ] + sys.argv[1:]
        from locust.main import main
        main()
