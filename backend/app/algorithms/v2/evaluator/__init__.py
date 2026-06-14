"""
多算法评价体系 - 统一算法注册、执行、评估
==========================================

支持算法:
  - V1: ACO, SA, NLP, HybridScheduler (规则+元启发式)
  - V2: MIPAssigner, BidirectionalAStar, TimeWindowAStar, SIPP (优化引擎)
  - RL: DQNAgent, PPOAgent (强化学习，可选)

评价指标:
  - 效率: makespan, throughput, avg_completion_time
  - 质量: total_distance, collision_rate, deadlocks_resolved
  - 资源: agv_utilization, battery_consumption, path_efficiency_ratio
  - 实时性: compute_time_ms, scalability_score
  - 鲁棒性: robustness_score (故障恢复能力)

使用方式:
    from backend.app.algorithms.v2.evaluator import AlgorithmEvaluator, ScenarioRunner

    evaluator = AlgorithmEvaluator()
    results = evaluator.evaluate_all(scenario_data)
    report = results.generate_report()
"""

from .registry import AlgorithmRegistry, AlgorithmSpec
from .scenarios import (
    ScenarioGenerator,
    ScenarioType,
    AGVTMS_Scenario,
    generate_scenario_suite,
)
from .metrics import (
    EvaluationMetrics,
    MetricCategory,
    ScoreCard,
    DimensionScore,
    EvaluatorConfig,
)
from .runner import (
    ScenarioRunner,
    EvaluationResult,
    ComparisonReport,
    evaluate_algorithms_on_scenario,
    run_full_evaluation,
    generate_evaluation_report_json,
)
from .visualizer import ResultsVisualizer

__all__ = [
    "AlgorithmRegistry",
    "AlgorithmSpec",
    "ScenarioGenerator",
    "ScenarioType",
    "AGVTMS_Scenario",
    "generate_scenario_suite",
    "EvaluationMetrics",
    "MetricCategory",
    "ScoreCard",
    "DimensionScore",
    "EvaluatorConfig",
    "ScenarioRunner",
    "EvaluationResult",
    "ComparisonReport",
    "evaluate_algorithms_on_scenario",
    "run_full_evaluation",
    "generate_evaluation_report_json",
    "ResultsVisualizer",
]
