"""
算法评估 API 接口
==================

提供 REST API 供前端调用:
  GET    /api/v2/evaluator/algorithms              - 列出所有注册算法
  POST   /api/v2/evaluator/scenarios/generate       - 生成测试场景
  POST   /api/v2/evaluator/evaluate/single          - 单场景对比
  POST   /api/v2/evaluator/evaluate/batch           - 批量评估
  GET    /api/v2/evaluator/reports                  - 获取报告
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import os

router = APIRouter(prefix="/api/v2/evaluator", tags=["algorithm-evaluation"])


# ==================== 数据模型 ====================

class GenerateScenarioRequest(BaseModel):
    """生成场景请求"""
    preset_name: str = Field(..., description="预设名称: small_warehouse, medium_warehouse, factory_floor, etc.")
    seed: int = Field(42, description="随机种子")
    overrides: Dict[str, Any] = Field(default_factory=dict, description="参数覆盖")


class EvaluateSingleRequest(BaseModel):
    """单场景评估请求"""
    scenario_data: Dict[str, Any] = Field(..., description="场景数据 (AGVTMS_Scenario.to_dict()格式)")
    algorithm_names: Optional[List[str]] = Field(None, description="要评估的算法列表，None=全部")


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


# ==================== API端点 ====================

@router.get("/algorithms", response_model=AlgorithmListResponse)
async def list_algorithms():
    """
    列出所有已注册的调度算法
    
    返回每个算法的名称、分类、能力标签、依赖状态等
    """
    from backend.app.algorithms.v2.evaluator.registry import get_registry

    registry = get_registry()
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


@router.post("/scenarios/generate")
async def generate_scenario(req: GenerateScenarioRequest):
    """
    生成指定类型的测试场景
    
    支持的场景类型: warehouse, factory, port, hospital, cross_docking, stress_test, mixed
    """
    from backend.app.algorithms.v2.evaluator.scenarios import ScenarioGenerator
    
    try:
        gen = ScenarioGenerator(seed=req.seed)
        scene = gen.generate(preset_name=req.preset_name, **req.overrides)
        
        return {
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"场景生成失败: {str(e)}")


@router.post("/evaluate/single")
async def evaluate_single(req: EvaluateSingleRequest):
    """
    在单个场景上运行多算法对比
    
    返回每个算法的评分卡和排名信息
    """
    from backend.app.algorithms.v2.evaluator import (
        AGVTMS_Scenario, ScenarioRunner, EvaluatorConfig,
    )
    
    try:
        # 重建场景对象
        scenario = AGVTMS_Scenario.from_dict(req.scenario_data)
        
        config = EvaluatorConfig(verbose=False)
        runner = ScenarioRunner(config=config)
        
        report = runner.run_comparison(
            scenario,
            algorithm_names=req.algorithm_names,
        )
        
        return {
            "success": True,
            "report": report.to_dict(),
            "radar_data": report.radar_data(),
            "markdown": report.to_markdown(),
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"评估失败: {str(e)}\n{traceback.format_exc()}")


@router.post("/evaluate/batch")
async def evaluate_batch(req: EvaluateBatchRequest, background_tasks=None):
    """
    批量评估：多种场景 × 多种算法
    
    可选择后台运行（异步），返回任务ID后轮询结果
    """
    from backend.app.algorithms.v2.evaluator.runner import run_full_evaluation
    from backend.app.algorithms.v2.evaluator.visualizer import ResultsVisualizer
    
    task_id = f"eval_{int(__import__('time').time()*1000)}"
    
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
        # TODO: 后台任务实现
        pass


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
        data = __import__('json').load(f)
    
    return {
        "has_report": True,
        "file_path": latest,
        "generated_at": os.path.getmtime(latest),
        "data": data,
    }


@router.get("/presets")
async def list_presets():
    """
    列出所有可用的场景预设配置
    """
    from backend.app.algorithms.v2.evaluator.scenarios import ScenarioGenerator
    
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
