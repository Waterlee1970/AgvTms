"""
算法评估 API 接口 (v1.6 — Phase 1 工业可用版)
==========================================

提供 REST API 供前端调用:
  GET    /api/v2/evaluator/algorithms              - 列出所有注册算法
  GET    /api/v2/evaluator/presets                  - 列出场景预设
  POST   /api/v2/evaluator/scenarios/generate       - 生成测试场景(预设)
  POST   /api/v2/evaluator/scenarios/custom         - 自定义场景生成(交互式配置)
  POST   /api/v2/evaluator/evaluate/single          - 单场景对比 (异步后台执行)
  POST   /api/v2/evaluator/evaluate/batch           - 批量评估
  POST   /api/v2/evaluator/evaluate/visualize       - 带轨迹的可视化评估 (异步)
  GET    /api/v2/evaluator/reports/latest           - 获取最新报告

v1.6 新增:
  POST   /api/v2/industrial/opcua/start             - 启动 OPC UA 模拟器
  GET    /api/v2/industrial/opcua/agvs              - 获取 OPC UA AGV 状态
  POST   /api/v2/industrial/opcua/command           - 下发 AGV 指令
  DELETE /api/v2/industrial/opcua/stop              - 停止 OPC UA 模拟器
  POST   /api/v2/industrial/wms/order               - 提交 WMS 订单
  POST   /api/v2/industrial/mes/work_order          - 提交 MES 生产工单
  GET    /api/v2/industrial/wms/tasks               - 获取待处理任务
  GET    /api/v2/industrial/wms/stats                - WMS/MES 集成统计
"""

import math
import json
import os
import time
import uuid
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

router = APIRouter(prefix="/api/v2/evaluator", tags=["algorithm-evaluation"])

# v1.6: 工业集成路由
industrial_router = APIRouter(prefix="/api/v2/industrial", tags=["industrial-integration"])


# ==================== 统一导入辅助 ====================
# 消除 25+ 处重复的 try/except import fallback

def _import_module(module_path: str):
    """统一导入辅助：先尝试 backend.xxx，回退到 app.xxx"""
    try:
        return __import__(f"backend.{module_path}", fromlist=["*"])
    except ImportError:
        return __import__(module_path, fromlist=["*"])


# ==================== 进度追踪 ====================

# 内存中的进度存储 {task_id: ProgressInfo}
_progress_store: Dict[str, Dict[str, Any]] = {}
_PROGRESS_MAX_SIZE = 500  # P2-4: 最大缓存条目数，防止内存泄漏
_PROGRESS_TTL_SECONDS = 3600  # 1小时过期


def _update_progress(task_id: str, stage: str, message: str, percent: float, data: Optional[Dict] = None):
    """更新评测进度（带大小限制和过期保护）"""
    # 定期清理过期的进度记录
    _cleanup_stale_entries()

    if len(_progress_store) >= _PROGRESS_MAX_SIZE:
        # 淘汰最旧的条目
        oldest_key = min(_progress_store.keys(), key=lambda k: _progress_store[k].get("timestamp", 0))
        del _progress_store[oldest_key]

    _progress_store[task_id] = {
        "task_id": task_id,
        "stage": stage,
        "message": message,
        "percent": round(percent, 1),
        "timestamp": time.time(),
        "data": data or {},
    }


def _get_progress(task_id: str) -> Optional[Dict[str, Any]]:
    """获取评测进度"""
    return _progress_store.get(task_id)


def _cleanup_stale_entries():
    """清理过期的进度条目（P2-4: 内存保护）"""
    now = time.time()
    stale_keys = [
        k for k, v in _progress_store.items()
        if now - v.get("timestamp", 0) > _PROGRESS_TTL_SECONDS
    ]
    for k in stale_keys:
        del _progress_store[k]


# ==================== JSON安全序列化 ====================

def safe_json_serialize(obj: Any) -> Any:
    """
    递归清理数据中的非法JSON值(inf/nan)
    
    - float('inf') -> None (或可配置的大数)
    - float('-inf') -> None
    - float('nan') -> None
    """
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None  # 或返回 0 或一个极大值
        return obj
    elif isinstance(obj, dict):
        return {k: safe_json_serialize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [safe_json_serialize(item) for item in obj]
    elif isinstance(obj, (int, str, bool, type(None))):
        return obj
    else:
        # 其他类型尝试转字符串
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


# ==================== 数据模型 ====================

class GenerateScenarioRequest(BaseModel):
    """生成场景请求 - 预设模式"""
    preset_name: str = Field(..., description="预设名称: small_warehouse, medium_warehouse, factory_floor, etc.")
    seed: int = Field(42, description="随机种子")
    overrides: Dict[str, Any] = Field(default_factory=dict, description="参数覆盖")


class FaultInjectionConfig(BaseModel):
    """故障注入配置"""
    enabled: bool = Field(False, description="是否启用故障模拟")
    fault_type: str = Field("agv_breakdown", description="故障类型: agv_breakdown/conveyor_jam/node_blocked/batch_fault")
    faulty_agv_indices: List[int] = Field(default_factory=list, description="故障AGV索引列表")
    faulty_segment_indices: List[int] = Field(default_factory=list, description="故障输送线路段索引")
    blocked_node_ids: List[str] = Field(default_factory=list, description="阻塞节点ID列表")
    fault_time: float = Field(50.0, description="故障发生时间点(仿真步)")
    fault_duration: float = Field(30.0, description="故障持续时长(仿真步)")


class CustomScenarioRequest(BaseModel):
    """自定义场景生成请求 - 交互式参数配置"""
    # 基础设置
    grid_rows: int = Field(15, description="网格行数", ge=5, le=50)
    grid_cols: int = Field(20, description="网格列数", ge=5, le=60)

    # AGV设置
    num_agvs: int = Field(15, description="AGV数量", ge=1, le=100)
    agv_speed_min: float = Field(1.0, description="AGV最小速度(m/s)", ge=0.5, le=5.0)
    agv_speed_max: float = Field(3.0, description="AGV最大速度(m/s)", ge=0.5, le=5.0)
    battery_min: int = Field(30, description="最低初始电量%", ge=5, le=100)
    battery_max: int = Field(100, description="最高初始电量%", ge=5, le=100)
    agv_capacity: float = Field(1.0, description="AGV载重能力")

    # 任务设置
    num_tasks: int = Field(40, description="任务数量", ge=1, le=300)
    high_priority_ratio: float = Field(0.2, description="高优先级任务比例", ge=0.0, le=1.0)
    task_duration_min: float = Field(30.0, description="最短预计时长(秒)", ge=1.0)
    task_duration_max: float = Field(180.0, description="最长预计时长(秒)", ge=1.0)

    # TMS输送线设置
    has_conveyor: bool = Field(False, description="是否包含输送线")
    num_conveyor_tasks: int = Field(10, description="输送任务数量", ge=0, le=50)
    num_conveyor_segments: int = Field(4, description="输送线路段数", ge=0, le=20)
    conveyor_speed: float = Field(0.5, description="输送线速度(m/s)", ge=0.1, le=3.0)

    # 三种任务类型分布 (仅 has_conveyor=True 时生效)
    # 约束: mixed_ratio + agv_only_ratio + conveyor_only_ratio ≈ 1.0
    mixed_ratio: float = Field(0.25, description="混合长程任务占比 (默认25%)", ge=0.0, le=1.0)
    agv_only_ratio: float = Field(0.375, description="纯AGV任务占比 (默认37.5%)", ge=0.0, le=1.0)
    conveyor_only_ratio: float = Field(0.375, description="纯输送线任务占比 (默认37.5%)", ge=0.0, le=1.0)

    # 场景类型和难度
    scenario_type: str = Field("warehouse", description="场景类型: warehouse/factory/port/hospital/mixed")
    difficulty: str = Field("medium", description="难度: easy/medium/hard/extreme")

    # 故障模拟
    fault_injection: Optional[FaultInjectionConfig] = Field(None, description="故障注入配置")

    # 其他
    seed: int = Field(42, description="随机种子")


class EvaluateSingleRequest(BaseModel):
    """单场景评估请求"""
    scenario_data: Dict[str, Any] = Field(..., description="场景数据 (AGVTMS_Scenario.to_dict()格式)")
    algorithm_names: Optional[List[str]] = Field(None, description="要评估的算法列表，None=全部")


class EvaluateVisualizeRequest(BaseModel):
    """带轨迹的可视化评估请求"""
    scenario_data: Dict[str, Any] = Field(..., description="场景数据")
    algorithm_names: Optional[List[str]] = Field(None, description="算法列表，默认只取第一个算法做可视化")
    simulation_steps: int = Field(150, description="仿真步数", ge=50, le=500)
    fault_config: Optional[FaultInjectionConfig] = Field(None, description="故障配置")
    seed: int = Field(42, description="随机种子")


class EvaluateBatchRequest(BaseModel):
    """批量评估请求"""
    preset_names: Optional[List[str]] = Field(None, description="场景预设列表")
    algorithm_names: Optional[List[str]] = Field(None, description="算法列表")
    seed: int = Field(42, description="随机种子")
    variants_per_type: int = Field(1, description="每种变体数")


class AlgorithmListResponse(BaseModel):
    """算法列表响应"""
    algorithms: List[Dict[str, Any]]
    total: int
    available: int


# ==================== 场景类型映射 ====================

SCENARIO_TYPE_MAP = {
    "warehouse": "WAREHOUSE",
    "factory": "FACTORY",
    "port": "PORT",
    "hospital": "HOSPITAL",
    "mixed": "MIXED_REALISTIC",
}

DIFFICULTY_MAP = {
    "easy": "EASY",
    "medium": "MEDIUM",
    "hard": "HARD",
    "extreme": "EXTREME",
}


# ==================== API端点 ====================

@router.get("/algorithms", response_model=AlgorithmListResponse)
async def list_algorithms():
    """
    列出所有已注册的调度算法
    
    返回每个算法的名称、分类、能力标签、依赖状态等
    """
    registry_mod = _import_module("app.algorithms.v2.evaluator.registry")
    registry = registry_mod.get_registry()

    all_algos = registry.list_all()

    return AlgorithmListResponse(
        algorithms=[{
            "name": a.name,
            "display_name": a.display_name,
            "category": a.category.value,
            "version": a.version,
            "description": a.description,
            "capabilities": a.capabilities,
            "is_available": a.is_available,
            "required_deps": a.required_deps,
        } for a in all_algos],
        total=len(all_algos),
        available=sum(1 for a in all_algos if a.is_available),
    )


@router.get("/presets")
async def list_presets():
    """
    列出所有可用的场景预设配置
    """
    scenarios_mod = _import_module("app.algorithms.v2.evaluator.scenarios")
    ScenarioGenerator = scenarios_mod.ScenarioGenerator

    presets_info = []
    for name, params in ScenarioGenerator.PRESETS.items():
        stype = params.get("stype")
        diff = params.get("diff")
        presets_info.append({
            "name": name,
            "type": stype.value if hasattr(stype, 'value') else str(stype),
            "difficulty": diff.value if hasattr(diff, 'value') else str(diff),
            "grid_size": params.get("grid_size"),
            "agvs": params.get("num_agvs"),
            "tasks": params.get("num_tasks"),
            "has_conveyor": params.get("has_conveyor", False),
        })

    return {"presets": presets_info, "total": len(presets_info)}


@router.post("/scenarios/generate")
async def generate_scenario(req: GenerateScenarioRequest):
    """
    生成指定类型的测试场景 (基于预设模板)
    
    支持的场景类型: warehouse, factory, port, hospital, cross_docking, stress_test, mixed
    可通过 overrides 参数微调各项数值
    """
    scenarios_mod = _import_module("app.algorithms.v2.evaluator.scenarios")
    ScenarioGenerator = scenarios_mod.ScenarioGenerator

    try:
        gen = ScenarioGenerator(seed=req.seed)
        scene = gen.generate(preset_name=req.preset_name, **req.overrides)

        result = {
            "success": True,
            "scenario": scene.to_dict(),
            "metadata": {
                "id": scene.metadata.scenario_id,
                "name": scene.metadata.name,
                "type": scene.metadata.scenario_type.value,
                "difficulty": scene.metadata.difficulty.value,
                "nodes": scene.metadata.num_nodes,
                "edges": scene.metadata.num_edges,
                "agvs": scene.metadata.num_agvs,
                "tasks": scene.metadata.num_tasks,
            }
        }
        return safe_json_serialize(result)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"场景生成失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/scenarios/custom")
async def generate_custom_scenario(req: CustomScenarioRequest):
    """
    根据用户交互式参数生成自定义测试场景
    
    支持完整的参数控制:
    - 基础: 网格大小、场景类型、难度
    - AGV: 数量、速度范围、电池、容量
    - 任务: 数量、优先级分布、预计时长
    - TMS: 输送线开关、输送任务数、线路段数
    - 故障: AGV故障/输送线堵塞/节点阻塞
    """
    scenarios_mod = _import_module("app.algorithms.v2.evaluator.scenarios")
    ScenarioGenerator = scenarios_mod.ScenarioGenerator
    ScenarioType = scenarios_mod.ScenarioType
    ScenarioDifficulty = scenarios_mod.ScenarioDifficulty
    AGVTMS_Scenario = scenarios_mod.AGVTMS_Scenario

    try:
        # 映射场景类型和难度
        stype_enum = SCENARIO_TYPE_MAP.get(req.scenario_type, ScenarioType.WAREHOUSE)
        stype = ScenarioType(stype_enum) if isinstance(stype_enum, str) else stype_enum
        diff_enum = DIFFICULTY_MAP.get(req.difficulty, ScenarioDifficulty.MEDIUM)
        diff = ScenarioDifficulty(diff_enum) if isinstance(diff_enum, str) else diff_enum

        gen = ScenarioGenerator(seed=req.seed)

        # 构建overrides字典
        overrides = {
            "grid_size": (req.grid_rows, req.grid_cols),
            "num_agvs": req.num_agvs,
            "num_tasks": req.num_tasks,
            "stype": stype,
            "diff": diff,
            "has_conveyor": req.has_conveyor,
        }

        if req.has_conveyor:
            overrides["num_ctasks"] = req.num_conveyor_tasks
            overrides["num_csegs"] = req.num_conveyor_segments

        # 生成基础场景
        preset_name = {
            "warehouse": "small_warehouse" if req.num_agvs <= 10 else ("medium_warehouse" if req.num_agvs <= 25 else "large_warehouse"),
            "factory": "factory_floor",
            "port": "port_terminal",
            "hospital": "hospital_logistics",
            "mixed": "mixed_agv_tms",
        }.get(req.scenario_type, "medium_warehouse")

        scene = gen.generate(preset_name=preset_name, **overrides)

        # === 后处理：应用自定义AGV配置 ===
        if req.agv_speed_min != req.agv_speed_max or req.battery_min != req.battery_max:
            import random
            rng = random.Random(req.seed + 999)
            for agv in scene.agvs:
                agv["speed"] = round(rng.uniform(req.agv_speed_min, req.agv_speed_max), 2)
                agv["battery_level"] = round(rng.uniform(req.battery_min, req.battery_max), 1)
                agv["capacity"] = req.agv_capacity

        # 应用自定义任务优先级分布
        if req.high_priority_ratio > 0 and scene.tasks:
            rng_task = random.Random(req.seed + 888)
            num_high = int(len(scene.tasks) * req.high_priority_ratio)
            high_priority_indices = set(rng_task.sample(range(len(scene.tasks)), min(num_high, len(scene.tasks))))
            for i, task in enumerate(scene.tasks):
                if i in high_priority_indices:
                    task["priority"] = rng_task.choice([7, 9])
                else:
                    task["priority"] = rng_task.choice([1, 3, 5])
                task["estimated_duration"] = round(
                    rng_task.uniform(req.task_duration_min, req.task_duration_max), 1
                )

        # === 应用故障注入到场景数据中 ===
        fault_info = None
        if req.fault_injection and req.fault_injection.enabled:
            fc = req.fault_injection
            fault_info = fc.dict() if hasattr(fc, 'dict') else dict(fc)

            # 在场景中标记故障AGV
            if fc.fault_type in ("agv_breakdown", "batch_fault"):
                for idx in fc.faulty_agv_indices:
                    if 0 <= idx < len(scene.agvs):
                        scene.agvs[idx]["status"] = "fault"
                        scene.agvs[idx]["fault_config"] = {
                            "fault_type": fc.fault_type,
                            "fault_time": fc.fault_time,
                            "fault_duration": fc.fault_duration,
                        }

            # 在场景中标记堵塞的线路段
            if fc.fault_type in ("conveyor_jam", "batch_fault") and scene.conveyor_segments:
                for idx in fc.faulty_segment_indices:
                    if 0 <= idx < len(scene.conveyor_segments):
                        scene.conveyor_segments[idx]["status"] = "jammed"

            # 更新元数据标签
            scene.metadata.tags.append("fault_injection")

        result = {
            "success": True,
            "scenario": scene.to_dict(),
            "metadata": {
                "id": scene.metadata.scenario_id,
                "name": f"Custom_{req.scenario_type}_{req.grid_rows}x{req.grid_cols}",
                "type": scene.metadata.scenario_type.value,
                "difficulty": scene.metadata.difficulty.value,
                "nodes": scene.metadata.num_nodes,
                "edges": scene.metadata.num_edges,
                "agvs": scene.metadata.num_agvs,
                "tasks": scene.metadata.num_tasks,
                "has_conveyor": req.has_conveyor,
                "conveyor_tasks": len(scene.conveyor_tasks or []),
                "fault_injection": fault_info,
            }
        }
        return safe_json_serialize(result)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"自定义场景生成失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/evaluate/single")
async def evaluate_single(req: EvaluateSingleRequest, background_tasks: BackgroundTasks):
    """
    在单个场景上运行多算法对比 (v1.6: 异步后台执行)

    改进点:
      - 6 个算法**并行执行**(ThreadPoolExecutor), 不再串行阻塞
      - 集成死锁风险检测 (ZoneManager RAG)
      - 前端通过 /evaluate/progress/{task_id} 轮询进度

    进度通过 GET /evaluate/progress/{task_id} 轮询获取
    """
    task_id = f"eval_{int(time.time()*1000)}"

    # 异步后台执行评测任务
    def _run_evaluation():
        try:
            evaluator_mod = _import_module("app.algorithms.v2.evaluator")
            AGVTMS_Scenario = evaluator_mod.AGVTMS_Scenario
            ScenarioRunner = evaluator_mod.ScenarioRunner
            EvaluatorConfig = evaluator_mod.EvaluatorConfig
        except (ImportError, AttributeError) as e:
            _update_progress(task_id, "error", f"模块加载失败: {str(e)}", 100, {"error": str(e)})
            return

        try:
            _update_progress(task_id, "parsing", "正在解析场景数据...", 5)

            scenario = AGVTMS_Scenario.from_dict(req.scenario_data)
            algo_count = len(req.algorithm_names) if req.algorithm_names else 8

            _update_progress(task_id, "parsing",
                             f"场景已加载: {scenario.metadata.num_agvs}辆AGV, "
                             f"{scenario.metadata.num_tasks}个任务, {scenario.metadata.num_nodes}个节点",
                             10, {"agvs": scenario.metadata.num_agvs, "tasks": scenario.metadata.num_tasks})

            config = EvaluatorConfig(verbose=False)
            runner = ScenarioRunner(config=config)

            algo_count = len(req.algorithm_names) if req.algorithm_names else len(runner.registry.list_available())
            
            # 进度回调: 每完成一个算法更新一次 (20% → 80%)
            # algo_name 可能含 "(运行中...Xs)" 心跳后缀，需解析
            def on_algo_done(completed: int, total: int, algo_name: str):
                pct = 20 + int((completed / total) * 60)  # 20~80
                # 解析心跳消息: "v1_hybrid (运行中...15s)" → 显示原样
                clean_name = algo_name.split(' (')[0].strip() if ' (' in algo_name else algo_name
                is_heartbeat = '(运行中...' in algo_name
                if is_heartbeat:
                    msg = f"重量级算法执行中: {algo_name}"
                else:
                    msg = f"算法评估中... ({completed}/{total}) {clean_name}"
                _update_progress(task_id, "evaluating", msg, pct,
                    {"completed": completed, "total": total, "current": clean_name,
                     "heartbeat": is_heartbeat})

            _update_progress(task_id, "evaluating",
                             f"开始并行运行 {algo_count} 个算法评估...", 20,
                             {"algorithms": req.algorithm_names or "all", "total_algorithms": algo_count})

            report = runner.run_comparison(
                scenario,
                algorithm_names=req.algorithm_names,
                progress_callback=on_algo_done,
            )

            _update_progress(task_id, "comparing", "正在生成评分卡和排名...", 85)

            result = {
                "success": True,
                "task_id": task_id,
                "report": report.to_dict(),
                "radar_data": report.radar_data(),
                "markdown": report.to_markdown(),
            }

            _update_progress(task_id, "completed",
                             f"评估完成！获胜算法: {report.winner}",
                             100, {"winner": report.winner, "algorithms_evaluated": len(report.score_cards) if hasattr(report, 'score_cards') else algo_count})

            # 缓存结果供后续查询
            _progress_store[task_id]["result"] = result

        except Exception as e:
            import traceback
            _update_progress(task_id, "error", f"评估失败: {str(e)}", 100, {"error": str(e)})
            # 错误也要记录，不抛异常（因为是后台任务）

    background_tasks.add_task(_run_evaluation)

    # 立即返回 task_id，前端轮询
    return {
        "success": True,
        "task_id": task_id,
        "message": "评测任务已提交，请使用 progress 接口查询进度",
    }


@router.get("/evaluate/progress/{task_id}")
async def get_evaluation_progress(task_id: str):
    """获取评测任务进度"""
    progress = _get_progress(task_id)
    if not progress:
        return {
            "task_id": task_id,
            "stage": "unknown",
            "message": "未找到任务进度",
            "percent": 0,
        }
    # 返回完整 progress dict（包含顶层 result 字段）
    # completed 时 _run_evaluation 会写入 _progress_store[task_id]["result"]
    # 前端直接从 response.result 取报告数据
    return progress


@router.post("/evaluate/visualize")
async def evaluate_visualize(req: EvaluateVisualizeRequest):
    """
    带轨迹的算法评估 - 用于前端可视化展示

    返回完整的数据:
    - 标准评估报告(用于评分排名)
    - 仿真轨迹数据(用于动画播放)
      - 每时间步的AGV位置快照
      - 任务状态变化事件
      - 故障发生/恢复事件

    错误时统一返回包含 error 字段的 JSON 结构体 (非 HTTPException)，
    以确保前端能通过 response.data.task_id 判断成功/失败。
    """
    task_id = f"viz_{int(time.time()*1000)}"
    error_response = lambda msg, stage="error": {
        "success": False,
        "task_id": task_id,
        "error": msg,
        "stage": stage,
    }

    try:
        # --- Step 1: 导入模块 ---
        try:
            evaluator_mod = _import_module("app.algorithms.v2.evaluator")
            simulator_mod = _import_module("app.algorithms.v2.evaluator.simulator")
            AGVTMS_Scenario = evaluator_mod.AGVTMS_Scenario
            ScenarioRunner = evaluator_mod.ScenarioRunner
            EvaluatorConfig = evaluator_mod.EvaluatorConfig
            SchedulingSimulator = simulator_mod.SchedulingSimulator
        except (ImportError, AttributeError) as e:
            return safe_json_serialize(error_response(f"模块加载失败: {str(e)}", "parsing"))

        # --- Step 2: 解析场景 ---
        try:
            scenario = AGVTMS_Scenario.from_dict(req.scenario_data)
        except Exception as e:
            return safe_json_serialize(error_response(f"场景解析失败: {str(e)}", "parsing"))

        _update_progress(task_id, "parsing",
                         f"场景已加载: {scenario.metadata.num_agvs}辆AGV, {scenario.metadata.num_tasks}个任务",
                         10)

        # 确定要可视化的算法（默认取第一个）
        algo_names = req.algorithm_names
        if not algo_names or len(algo_names) == 0:
            algo_names = None  # 使用全部

        _update_progress(task_id, "evaluating", "正在运行算法评估...", 20)

        # --- Step 3: 运行评估 ---
        try:
            config = EvaluatorConfig(verbose=False)
            runner = ScenarioRunner(config=config)
            report = runner.run_comparison(scenario, algorithm_names=algo_names)
        except Exception as e:
            return safe_json_serialize(error_response(f"算法评估失败: {str(e)}", "evaluating"))

        winner_algo_name = getattr(report, 'winner', str(algo_names[0] if algo_names else 'unknown'))

        _update_progress(task_id, "evaluating",
                         f"算法评估完成，获胜算法: {winner_algo_name}",
                         50, {"winner": winner_algo_name})

        _update_progress(task_id, "simulating",
                         f"正在运行 {req.simulation_steps} 步仿真...", 60)

        # --- Step 4: 运行仿真 ---
        try:
            fault_dict = None
            if req.fault_config:
                fault_dict = req.fault_config.dict() if hasattr(req.fault_config, 'dict') else dict(req.fault_config)

            simulator = SchedulingSimulator(scenario, seed=req.seed % 10000)
            trajectory = simulator.simulate(
                time_steps=req.simulation_steps,
            )
        except Exception as e:
            return safe_json_serialize(error_response(f"仿真运行失败: {str(e)}", "simulating"))

        _update_progress(task_id, "comparing", "正在生成可视化数据...", 90)

        # --- Step 5: 构建结果 ---
        result = {
            "success": True,
            "task_id": task_id,
            "report": report.to_dict() if hasattr(report, 'to_dict') else {},
            "radar_data": report.radar_data() if hasattr(report, 'radar_data') else {},
            "trajectory": trajectory.to_dict() if trajectory and hasattr(trajectory, 'to_dict') else {},
            "visualization_algo": winner_algo_name,
        }

        _update_progress(task_id, "completed",
                         f"可视化就绪！算法: {winner_algo_name}, 仿真{req.simulation_steps}步",
                         100, {"winner": winner_algo_name, "steps": req.simulation_steps})

        return safe_json_serialize(result)

    except Exception as e:
        # 兜底：未被上述步骤捕获的未知异常
        import traceback
        _update_progress(task_id, "error", f"可视化评估失败: {str(e)}", 100, {"error": str(e)})
        # 返回统一错误结构体而非 HTTPException，保证前端兼容
        return safe_json_serialize(error_response(f"内部错误: {str(e)}", "error"))


@router.post("/evaluate/batch")
async def evaluate_batch(req: EvaluateBatchRequest, background_tasks=None):
    """
    批量评估：多种场景 × 多种算法
    
    可选择后台运行（异步），返回任务ID后轮询结果
    """
    runner_mod = _import_module("app.algorithms.v2.evaluator.runner")
    visualizer_mod = _import_module("app.algorithms.v2.evaluator.visualizer")
    run_full_evaluation = runner_mod.run_full_evaluation
    ResultsVisualizer = visualizer_mod.ResultsVisualizer

    task_id = f"eval_{int(time.time()*1000)}"

    if background_tasks is None:
        # 同步执行
        try:
            result = run_full_evaluation(
                presets=req.preset_names,
                algorithms=req.algorithm_names,
                seed=req.seed,
                variants=req.variants_per_type,
            )

            visualizer = ResultsVisualizer()

            return {
                "task_id": task_id,
                "status": "completed",
                "summary": result.to_dict(),
                "overall_rankings": result.overall_rankings,
                "best_by_type": result.best_by_scenario_type,
                "recommendations": result.recommendations,
                "report_markdown": result.summary_report,
                "num_scenarios": len(result.scenario_reports),
            }
        except Exception as e:
            import traceback
            raise HTTPException(status_code=500, detail=f"批量评估失败: {str(e)}\n{traceback.format_exc()}")
    else:
        # 异步后台执行批量评估
        def _run_batch():
            try:
                result = run_full_evaluation(
                    presets=req.preset_names,
                    algorithms=req.algorithm_names,
                    seed=req.seed,
                    variants=req.variants_per_type,
                )
                _update_progress(task_id, "completed", "批量评估完成", 100,
                                 {"summary": result.to_dict() if hasattr(result, 'to_dict') else {}})
            except Exception as e:
                _update_progress(task_id, "error", f"批量评估失败: {str(e)}", 100, {"error": str(e)})

        background_tasks.add_task(_run_batch)

    return {"task_id": task_id, "status": "pending", "message": "批量评估任务已提交"}


@router.get("/reports/latest")
async def get_latest_report():
    """
    获取最新的评估报告摘要
    """
    import glob
    results_dir = os.path.join(os.path.dirname(__file__), "..", "..", "benchmark_results")

    json_files = sorted(glob.glob(os.path.join(results_dir, "eval_report_*.json")),
                       key=os.path.getmtime, reverse=True)

    if not json_files:
        return {"message": "暂无报告", "has_report": False}

    latest = json_files[0]
    with open(latest, 'r', encoding='utf-8') as f:
        data = json.load(f)

    return {
        "has_report": True,
        "file_path": latest,
        "generated_at": os.path.getmtime(latest),
        "data": data,
    }


# ==================== v1.6: 工业集成 API ====================

# OPC UA 适配器全局实例
_opcua_adapter = None
_wms_service = None


@industrial_router.post("/opcua/start")
async def opcua_start(
    mode: str = Query("simulation", description="模式: simulation 或 live"),
    num_agvs: int = Query(10, ge=1, le=100, description="模拟AGV数量"),
    move_speed: float = Query(1.5, ge=0.1, le=10.0, description="移动速度 m/s"),
):
    """启动 OPC UA 模拟器 / 连接真实 OPC UA Server"""
    global _opcua_adapter

    if _opcua_adapter and _opcua_adapter.is_running:
        raise HTTPException(status_code=400, detail="OPC UA adapter already running")

    opcua_mod = _import_module("app.adapters.opcua_adapter")
    OpcUaAdapter = opcua_mod.OpcUaAdapter

    _opcua_adapter = OpcUaAdapter(mode=mode, num_sim_agvs=num_agvs, move_speed=move_speed)
    await _opcua_adapter.initialize()
    await _opcua_adapter.start()

    return {
        "success": True,
        "mode": mode,
        "num_agvs": num_agvs if mode == "simulation" else "connected",
        "connected_count": _opcua_adapter.connected_agv_count,
        "message": f"OPC UA adapter started in {mode} mode",
    }


@industrial_router.get("/opcua/agvs")
async def opcua_get_agvs():
    """获取所有 AGV 设备状态 (OPC UA)"""
    global _opcua_adapter
    if not _opcua_adapter or not _opcua_adapter.is_running:
        raise HTTPException(status_code=400, detail="OPC UA adapter not running. POST /industrial/opcua/start first")

    agv_list = await _opcua_adapter.get_all_agv_statuses()
    return {
        "agvs": [_opcua_adapter.to_api_format(a) for a in agv_list],
        "count": len(agv_list),
    }


class OpcUaCommandRequest(BaseModel):
    """OPC UA 指令请求体"""
    agv_id: str = Field(..., description="AGV ID")
    command: str = Field(..., description="指令: move/stop/resume/charge/load/unload/cancel_task")
    params: Optional[Dict[str, Any]] = None


@industrial_router.post("/opcua/command")
async def opcua_send_command(req: OpcUaCommandRequest):
    """向 AGV 下发控制指令 (OPC UA)"""
    global _opcua_adapter
    if not _opcua_adapter or not _opcua_adapter.is_running:
        raise HTTPException(status_code=400, detail="OPC UA adapter not running")

    opcua_mod = _import_module("app.adapters.opcua_adapter")
    cmd_enum = opcua_mod.AgvCommand(req.command)
    result = await _opcua_adapter.send_command(req.agv_id, cmd_enum, req.params)
    return {
        "agv_id": result.agv_id,
        "command": result.command,
        "success": result.success,
        "message": result.message,
    }


@industrial_router.delete("/opcua/stop")
async def opcua_stop():
    """停止 OPC UA 适配器"""
    global _opcua_adapter
    if _opcua_adapter:
        await _opcua_adapter.stop()
        _opcua_adapter = None
    return {"success": True, "message": "OPC UA adapter stopped"}


# ---- WMS/MES 集成 API ----

@industrial_router.post("/wms/order")
async def wms_receive_order(req: Dict[str, Any]):
    """
    接收 WMS 订单.

    支持的 order_type:
      inbound(入库), outbound(出库), inventory(盘点),
      move(移库), pick(拣货), replenish(补货), return(退料)
    """
    global _wms_service
    if _wms_service is None:
        wms_mod = _import_module("app.api.wms_integration")
        _wms_service = wms_mod.WmsIntegrationService()
        # 尝试加载地图节点用于位置解析
        # TODO: 从数据库或缓存中读取当前地图

    wms_req = wms_mod.WmsOrderRequest(**req)
    ack = _wms_service.receive_wms_order(wms_req)

    return {
        **ack.__dict__,
        "stats": _wms_service.stats,
    }


@industrial_router.post("/mes/work_order")
async def mes_receive_work_order(req: Dict[str, Any]):
    """接收 MES 生产工单 → 转换为搬运任务"""
    global _wms_service
    if _wms_service is None:
        wms_mod = _import_module("app.api.wms_integration")
        _wms_service = wms_mod.WmsIntegrationService()

    wo = wms_mod.MesWorkOrder(**req)
    ack = _wms_service.receive_mes_work_order(wo)

    return {**ack.__dict__, "stats": _wms_service.stats}


@industrial_router.get("/wms/tasks")
async def wms_get_tasks(limit: int = Query(50, le=200)):
    """获取待处理的搬运任务列表 (按优先级排序)"""
    global _wms_service
    if _wms_service is None:
        return {"tasks": [], "total": 0}

    pending = _wms_service.get_pending_tasks(limit=limit)
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "external_id": t.external_id,
                "source": t.source,
                "pickup_node": t.pickup_node,
                "dropoff_node": t.dropoff_node,
                "priority": t.priority,
                "status": t.status,
                "assigned_agv": t.assigned_agv,
                "payload": t.payload,
            }
            for t in pending
        ],
        "total": len(pending),
    }


@industrial_router.get("/wms/stats")
async def wms_get_stats():
    """获取 WMS/MES 集成统计信息"""
    global _wms_service
    if _wms_service is None:
        return {"status": "not_initialized"}

    stats = _wms_service.stats
    recent = _wms_service.get_recent_orders(limit=5)
    return {
        **stats,
        "recent_orders": recent,
    }
