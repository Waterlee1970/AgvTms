"""
评估运行器 - 多算法对比执行引擎
=================================

核心功能:
  1. 在同一场景上运行多个算法
  2. 收集每个算法的原始结果和评分
  3. 生成对比报告（排名、雷达图数据、推荐结论）
  4. 支持批量场景验证
"""

from __future__ import annotations

import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed

# 单个算法最大执行时间（秒）— 防止某个算法卡死导致整体无限等待
ALGORITHM_TIMEOUT_SECONDS = 60

logger = logging.getLogger(__name__)

from .registry import AlgorithmRegistry, AlgorithmResult, BaseAlgorithm, get_registry
from .scenarios import AGVTMS_Scenario, ScenarioGenerator, ScenarioType, generate_scenario_suite
from .metrics import (
    EvaluationMetrics, MetricsEvaluator, ScoringEngine,
    ScoreCard, EvaluatorConfig, DimensionScore,
    create_score_card,
)


@dataclass
class ComparisonReport:
    """
    算法对比报告

    单个场景 × 多算法 的完整对比结果
    """

    scenario_name: str
    scenario_type: str
    scenario_metadata: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, ScoreCard] = field(default_factory=dict)   # algo_name → ScoreCard
    rankings: List[Tuple[str, float]] = field(default_factory=list)  # (name, score) 排序
    winner: str = ""
    summary: str = ""
    timestamp: str = ""
    execution_time_total_ms: float = 0.0

    def to_dict(self):
        return {
            "scenario": self.scenario_name,
            "type": self.scenario_type,
            "metadata": self.scenario_metadata,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "rankings": [(n, round(s, 2)) for n, s in self.rankings],
            "winner": self.winner,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "execution_time_ms": round(self.execution_time_total_ms, 1),
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """生成 Markdown 格式的对比报告"""
        lines = [
            f"# 算法对比报告: {self.scenario_name}",
            f"",
            f"**场景类型**: {self.scenario_type} | **测试时间**: {self.timestamp}",
            f"**总耗时**: {self.execution_time_total_ms:.0f}ms",
            f"**获胜算法**: **{self.winner}** ({self.results[self.winner].total_score:.1f}分)",
            f"",
            f"## 排名",
            f"",
            f"| 排名 | 算法 | 总分 | 等级 | 效率 | 质量 | 资源 | 实时性 | 鲁棒性 |",
            f"|------|------|------|------|------|------|------|--------|--------|",
        ]
        for rank, (name, score) in enumerate(self.rankings, 1):
            sc = self.results.get(name)
            if sc:
                dims = sc.dimension_scores
                d_vals = {
                    "效率": dims.get("efficiency", DimensionScore(None,"",0)).score,
                    "质量": dims.get("quality", DimensionScore(None,"",0)).score,
                    "资源": dims.get("resource", DimensionScore(None,"",0)).score,
                    "实时性": dims.get("realtime", DimensionScore(None,"",0)).score,
                    "鲁棒性": dims.get("robustness", DimensionScore(None,"",0)).score,
                }
                lines.append(
                    f"| #{rank} | {sc.algorithm_display_name or name} | "
                    f"{score:.1f} | **{sc.grade}** | "
                    f"{d_vals['效率']:.0f} | {d_vals['质量']:.0f} | "
                    f"{d_vals['资源']:.0f} | {d_vals['实时性']:.0f} | {d_vals['鲁棒性']:.0f} |"
                )

        if self.summary:
            lines.extend(["", f"## 分析总结", "", self.summary])

        return "\n".join(lines)

    def radar_data(self) -> Dict[str, List[float]]:
        """
        生成雷达图数据 (用于前端可视化)
        
        Returns:
            {"labels": [...], "datasets": [{"name": "...", "values": [...]}, ...]}
        """
        labels = ["效率", "质量", "资源利用", "实时性能", "鲁棒性"]
        datasets = []
        for name, sc in self.results.items():
            vals = [
                sc.dimension_scores.get("efficiency", DimensionScore(None,"",0)).normalized,
                sc.dimension_scores.get("quality", DimensionScore(None,"",0)).normalized,
                sc.dimension_scores.get("resource", DimensionScore(None,"",0)).normalized,
                sc.dimension_scores.get("realtime", DimensionScore(None,"",0)).normalized,
                sc.dimension_scores.get("robustness", DimensionScore(None,"",0)).normalized,
            ]
            datasets.append({
                "name": sc.algorithm_display_name or name,
                "values": [round(v, 1) for v in vals],
                "score": round(sc.total_score, 1),
            })

        return {"labels": labels, "datasets": datasets}


@dataclass
class EvaluationResult:
    """单次评估的完整输出"""
    algorithm_name: str
    result: AlgorithmResult
    metrics: EvaluationMetrics
    score_card: ScoreCard


@dataclass
class BatchEvaluationResults:
    """
    批量评估结果 - 多场景 × 多算法

    用于生成综合分析报告
    """

    scenario_reports: List[ComparisonReport] = field(default_factory=list)
    overall_rankings: Dict[str, float] = field(default_factory=dict)  # algo_name → avg_score
    scenario_type_stats: Dict[str, Dict[str, float]] = field(default_factory=dict)
    best_by_scenario_type: Dict[str, str] = field(default_factory=dict)  # type → winner_algo
    recommendations: List[str] = field(default_factory=list)
    summary_report: str = ""

    def to_dict(self):
        return {
            "num_scenarios_tested": len(self.scenario_reports),
            "overall_rankings": {k: round(v, 2) for k, v in sorted(self.overall_rankings.items(), key=lambda x:-x[1])},
            "best_by_scenario_type": self.best_by_scenario_type,
            "recommendations": self.recommendations,
            "summary": self.summary_report,
        }


class ScenarioRunner:
    """
    场景运行器 - 核心调度引擎

    使用方式:
        runner = ScenarioRunner()
        report = runner.run_comparison(scenario, algorithms=["fcfs","greedy","v2_mip"])
        print(report.to_markdown())

        # 批量运行
        batch = runner.run_batch(preset_names=["medium_warehouse","factory_floor","port_terminal"])
        print(batch.summary_report)
    """

    def __init__(
        self,
        config: Optional[EvaluatorConfig] = None,
        registry=None,
    ):
        self.config = config or EvaluatorConfig()
        self.registry = registry or get_registry()
        self.metrics_evaluator = MetricsEvaluator(self.config)
        self.scorer = ScoringEngine(self.config)
        self._history: List[ComparisonReport] = []

    def run_single(
        self,
        algorithm: BaseAlgorithm,
        scenario: AGVTMS_Scenario,
        **algo_kwargs,
    ) -> EvaluationResult:
        """运行单个算法在单个场景上（带超时保护）"""
        t_start = time.perf_counter()

        try:
            # 使用线程池 + 超时机制，防止算法卡死
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    algorithm.execute,
                    nodes=scenario.nodes,
                    edges=scenario.edges,
                    tasks=scenario.tasks,
                    agvs=scenario.agvs,
                    conveyor_tasks=scenario.conveyor_tasks,
                    conveyor_segments=scenario.conveyor_segments,
                    **algo_kwargs,
                )
                result = future.result(timeout=ALGORITHM_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            result = AlgorithmResult(
                success=False,
                error=f"算法执行超时 (>{ALGORITHM_TIMEOUT_SECONDS}s)，可能存在无限循环或死锁",
                compute_time_ms=ALGORITHM_TIMEOUT_SECONDS * 1000,
            )
            if self.config.verbose:
                print(f"  [{algorithm.name}] ⏰ 超时! 执行时间超过 {ALGORITHM_TIMEOUT_SECONDS}s")
        except Exception as e:
            result = AlgorithmResult(success=False, error=str(e))

        elapsed = (time.perf_counter() - t_start) * 1000
        if result.compute_time_ms <= 0:
            result.compute_time_ms = elapsed

        # 计算指标
        metrics = self.metrics_evaluator.evaluate(result, scenario)
        metrics.algorithm_name = algorithm.name
        metrics.algorithm_display_name = algorithm.spec.display_name
        metrics.scenario_name = scenario.metadata.name
        metrics.scenario_type = scenario.metadata.scenario_type.value

        # 评分
        card = self.scorer.score(metrics)
        card.algorithm_name = algorithm.name
        card.algorithm_display_name = algorithm.spec.display_name
        card.scenario_name = scenario.metadata.name
        card.scenario_type = scenario.metadata.scenario_type.value

        return EvaluationResult(
            algorithm_name=algorithm.name,
            result=result,
            metrics=metrics,
            score_card=card,
        )

    def run_comparison(
        self,
        scenario: AGVTMS_Scenario,
        algorithm_names: Optional[List[str]] = None,
        **common_algo_kwargs,
    ) -> ComparisonReport:
        """
        在单个场景上运行多算法对比（并行执行 + 死锁检测）

        Args:
            scenario: 测试场景
            algorithm_names: 要测试的算法列表，None表示全部可用算法
            **common_algo_kwargs: 传给所有算法的公共参数
        """
        t_total = time.perf_counter()

        if algorithm_names is None:
            available = self.registry.list_available()
            algorithm_names = [a.name for a in available]

        results_map: Dict[str, ScoreCard] = {}
        eval_results: List[EvaluationResult] = []

        # ===== P0-1.1 改造: 并行执行所有算法 (替代原来的串行 for 循环) =====
        max_workers = min(len(algorithm_names), 6)  # 最多6个并行

        def _run_one(algo_name: str) -> Tuple[str, Optional[EvaluationResult]]:
            """单个算法执行的包装函数（供线程池调用）"""
            try:
                algo = self.registry.create(algo_name, **common_algo_kwargs)
                ev_result = self.run_single(algo, scenario, **common_algo_kwargs)
                return algo_name, ev_result
            except Exception as e:
                if self.config.verbose:
                    print(f"  [{algo_name}] ❌ Error: {e}")
                return algo_name, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_run_one, name): name
                for name in algorithm_names
            }

            for future in as_completed(futures):
                algo_name = futures[future]
                try:
                    name, ev_result = future.result()
                    if ev_result is not None:
                        eval_results.append(ev_result)
                        results_map[name] = ev_result.score_card

                        if self.config.verbose:
                            print(f"  [{name}] ✅ Score={ev_result.score_card.total_score:.1f} "
                                  f"| Time={ev_result.result.compute_time_ms:.1f}ms "
                                  f"| Assigned={ev_result.result.num_assignments}/{len(scenario.tasks)}")
                    else:
                        # 异常产生的零分卡
                        dummy = ScoreCard(
                            algorithm_name=algo_name, algorithm_display_name=algo_name,
                            scenario_name=scenario.metadata.name,
                            scenario_type=scenario.metadata.scenario_type.value,
                            total_score=0.0, error_info="Execution failed",
                            timestamp=datetime.now().isoformat(),
                        )
                        results_map[algo_name] = dummy

                except Exception as e:
                    if self.config.verbose:
                        print(f"  [{algo_name}] ❌ Unexpected: {e}")
                    dummy = ScoreCard(
                        algorithm_name=algo_name, algorithm_display_name=algo_name,
                        scenario_name=scenario.metadata.name,
                        scenario_type=scenario.metadata.scenario_type.value,
                        total_score=0.0, error_info=str(e),
                        timestamp=datetime.now().isoformat(),
                    )
                    results_map[algo_name] = dummy

        # ===== P0-1.2 集成死锁检测 (ZoneManager) =====
        self._check_scenario_deadlock(scenario)

        total_elapsed = (time.perf_counter() - t_total) * 1000

        # 排名
        ranked = sorted(results_map.items(), key=lambda x: x[1].total_score, reverse=True)
        winner = ranked[0][0] if ranked else ""

        # 更新排名信息
        for rank, (name, card) in enumerate(ranked, 1):
            card.rank = rank

        # 生成摘要
        summary = self._generate_summary(ranked, scenario)

        report = ComparisonReport(
            scenario_name=scenario.metadata.name,
            scenario_type=scenario.metadata.scenario_type.value,
            scenario_metadata={
                "nodes": scenario.metadata.num_nodes,
                "edges": scenario.metadata.num_edges,
                "agvs": scenario.metadata.num_agvs,
                "tasks": scenario.metadata.num_tasks,
                "difficulty": scenario.metadata.difficulty.value,
                "tags": scenario.metadata.tags,
            },
            results=results_map,
            rankings=[(n, s.total_score) for n, s in ranked],
            winner=winner,
            summary=summary,
            timestamp=datetime.now().isoformat(),
            execution_time_total_ms=total_elapsed,
        )

        self._history.append(report)
        return report

    def _check_scenario_deadlock(self, scenario: AGVTMS_Scenario) -> None:
        """
        P0-1.2: 场景级死锁风险检测。

        使用 ZoneManager 的 ResourceAllocationGraph 分析场景拓扑，
        检测可能导致死锁的地图结构（如环形通道、单车道瓶颈）。
        """
        try:
            from ..traffic_control.zone_controller import ZoneManager
            zm = ZoneManager()
            zm.auto_discover_zones(scenario.nodes, scenario.edges)

            # 模拟分配: 将 AGV 分配到各区域，检查是否可能死锁
            for agv in scenario.agvs[:min(len(scenario.agvs), 20)]:
                agv_id = agv.get("id", agv.get("agv_id", ""))
                pos = agv.get("current_node", agv.get("pos", ""))
                zone_id = zm.get_zone_for_node(pos)
                if zone_id:
                    zm.rag.hold(agv_id, zone_id)

            deadlock = zm.check_deadlock()
            if deadlock:
                logger.warning(
                    f"[DeadlockDetector] ⚠️ 场景 '{scenario.metadata.name}' 检测到潜在死锁风险: "
                    f"涉及AGV {deadlock}. 建议增加路径或调整区域容量."
                )
            else:
                if self.config.verbose:
                    print(f"  [DeadlockDetector] ✅ 无死锁风险 ({len(zm.zones)} 个交通区域)")

        except Exception as e:
            logger.debug(f"[DeadlockDetector] 检测跳过: {e}")

    def run_batch(
        self,
        preset_names: Optional[List[str]] = None,
        algorithm_names: Optional[List[str]] = None,
        seed: int = 42,
        variants_per_type: int = 2,
        **kwargs,
    ) -> BatchEvaluationResults:
        """
        批量运行：多种场景 × 多种算法

        Args:
            preset_names: 预设场景名称列表
            algorithm_names: 算法名列表
            seed: 随机种子
            variants_per_type: 每种场景生成的变体数
        """
        scenarios = generate_scenario_suite(
            preset_names=preset_names,
            seed=seed,
            count_per_type=variants_per_type,
        )

        reports = []
        all_scores: Dict[str, List[float]] = {}

        for scenario in scenarios:
            if self.config.verbose:
                print(f"\n{'='*60}")
                print(f"📊 Scenario: {scenario.metadata.name}")
                print(f"   Type={scenario.metadata.scenario_type.value}, "
                      f"AGVs={scenario.metadata.num_agvs}, Tasks={scenario.metadata.num_tasks}")

            report = self.run_comparison(scenario, algorithm_names, **kwargs)
            reports.append(report)

            # 收集分数用于总体排名
            for name, card in report.results.items():
                all_scores.setdefault(name, []).append(card.total_score)

        # 计算总体平均排名
        overall = {name: sum(scores)/len(scores) for name, scores in all_scores.items()}
        overall_sorted = dict(sorted(overall.items(), key=lambda x: -x[1]))

        # 按场景类型统计最佳算法
        type_best: Dict[str, Dict[str, int]] = {}
        type_scores: Dict[str, Dict[str, List[float]]] = {}
        for report in reports:
            stype = report.scenario_type
            type_scores.setdefault(stype, {})
            for name, card in report.results.items():
                type_scores[stype].setdefault(name, []).append(card.total_score)

        best_by_type = {}
        for stype, algo_scores in type_scores.items():
            avg = {n: sum(s)/len(s) for n, s in algo_scores.items()}
            best = max(avg, key=avg.get)
            best_by_type[stype] = best

        # 生成建议
        recs = self._generate_recommendations(reports, overall_sorted, best_by_type)

        batch_result = BatchEvaluationResults(
            scenario_reports=reports,
            overall_rankings=overall_sorted,
            scenario_type_stats={st: {n: round(sum(s)/len(s), 2)
                                      for n, s in asc.items()}
                                  for st, asc in type_scores.items()},
            best_by_scenario_type=best_by_type,
            recommendations=recs,
            summary_report=self._generate_batch_summary(reports, overall_sorted, best_by_type),
        )

        return batch_result

    def _generate_summary(self, ranked, scenario):
        """生成单场景对比摘要"""
        if not ranked:
            return "无有效结果"

        def _get_score(item):
            """安全获取分数: ScoreCard对象返回total_score, 其他类型尝试转换或返回0"""
            val = item[1]
            if hasattr(val, 'total_score'):
                return float(val.total_score)
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        winner_name, winner_card = ranked[0]
        winner_score = _get_score(ranked[0])
        runner_up = ranked[1] if len(ranked) > 1 else None
        runner_up_score = _get_score(runner_up) if runner_up else 0.0
        margin = winner_score - runner_up_score
        runner_up_name = runner_up[0] if runner_up else None

        lines = [
            f"**{winner_name}** 以 {winner_score:.1f} 分获得第一名",
        ]

        if margin > 10:
            lines.append(f"领先第二名({runner_up_name}) {margin:.1f} 分，优势明显")
        elif margin > 3:
            lines.append(f"略胜{runner_up_name} ({runner_up_score:.1f}分)，差距较小")
        elif len(ranked) > 1 and runner_up:
            lines.append(f"与{runner_up_name}({runner_up_score:.1f}分)旗鼓相当")

        # 维度亮点（仅当获胜者有维度数据时）
        wc = ranked[0][1]
        if hasattr(wc, 'dimension_scores') and isinstance(wc.dimension_scores, dict):
            top_dims = sorted(wc.dimension_scores.items(),
                             key=lambda x: x[1].score if hasattr(x[1], 'score') else 0,
                             reverse=True)[:2]
            if top_dims:
                dim_names = [cat_name for cat_name, ds in top_dims]
                lines.append(f"核心优势维度: {', '.join(dim_names)}")

        return " ".join(lines)

    def _generate_recommendations(self, reports, overall, best_by_type):
        """生成使用建议"""
        recs = []

        if not overall:
            return ["无法生成建议：无有效数据"]

        top_algo = list(overall.keys())[0]

        # 总体推荐
        recs.append(f"[总体最优] **{top_algo}** 综合表现最佳，适合作为默认调度策略")

        # 按场景推荐
        for stype, best in best_by_type.items():
            if best != top_algo:
                recs.append(f"[{stype}] **{best}** 在此类场景中表现更优")

        # 性能敏感场景
        fast_algos = [(n, s) for n, s in overall.items() if any(
            r.results[n].dimension_scores.get("realtime", DimensionScore(None,"",0)).score > 80
            for r in reports if n in r.results)]
        if fast_algos:
            fastest = min(fast_algos, key=lambda x: x[1])
            recs.append(f"[低延迟需求] **{fastest[0]}** 计算速度最快，适合实时性要求高的场景")

        # 大规模场景推荐
        large_reports = [r for r in reports if r.scenario_metadata.get("agvs", 0) >= 20]
        if large_reports:
            large_overall = {}
            for r in large_reports:
                for n, c in r.results.items():
                    large_overall.setdefault(n, []).append(c.total_score)
            large_avg = {n: sum(s)/len(s) for n, s in large_overall.items()}
            if large_avg:
                large_best = max(large_avg, key=large_avg.get)
                if large_best != top_algo:
                    recs.append(f"[大规模部署] **{large_best}** 在大规模场景(≥20 AGV)下更稳定")

        return recs

    def _generate_batch_summary(self, reports, overall, best_by_type):
        """生成批量测试总结报告"""
        lines = [
            "=" * 70,
            " AgvTms 多算法批量评估报告",
            f" 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f" 测试场景数: {len(reports)}",
            f" 参评算法数: {len(overall)}",
            "=" * 70,
            "",
            "## 综合排行榜",
            "",
            "| 排名 | 算法 | 平均分 | 出场次数 | 最佳场景 | 最差场景 |",
            "|------|------|--------|---------|---------|---------|",
        ]

        for rank, (name, score) in enumerate(overall.items(), 1):
            appearances = sum(1 for r in reports if name in r.results)
            best_sc = max((r.results[name].total_score for r in reports if name in r.results), default=0)
            worst_sc = min((r.results[name].total_score for r in reports if name in r.results), default=0)
            grade = reports[0].results[name].grade if name in reports[0].results else "?"
            lines.append(
                f"| #{rank} | **{name}** | **{score:.1f}** | {appearances} | "
                f"{best_sc:.1f} | {worst_sc:.1f} |"
            )

        lines.extend([
            "",
            "## 各场景类型最佳算法",
            "",
        ])
        for stype, best in best_by_type.items():
            score = overall.get(best, 0)
            lines.append(f"- **{stype}**: {best} ({score:.1f}分)")

        lines.extend(["", "---"])

        return "\n".join(lines)


# ==================== API层 ====================

def evaluate_algorithms_on_scenario(
    scenario: AGVTMS_Scenario,
    algorithms: Optional[List[str]] = None,
    config: Optional[EvaluatorConfig] = None,
) -> ComparisonReport:
    """
    便捷API：在指定场景上评估算法
    """
    runner = ScenarioRunner(config=config)
    return runner.run_comparison(scenario, algorithms)


def run_full_evaluation(
    presets: Optional[List[str]] = None,
    algorithms: Optional[List[str]] = None,
    seed: int = 42,
    variants: int = 2,
) -> BatchEvaluationResults:
    """
    便捷API：运行完整评估流程
    """
    runner = ScenarioRunner()
    return runner.run_batch(
        preset_names=presets,
        algorithm_names=algorithms,
        seed=seed,
        variants_per_type=variants,
    )


def generate_evaluation_report_json(batch_result: BatchEvaluationResults) -> str:
    """导出JSON格式报告"""
    data = batch_result.to_dict()
    return json.dumps(data, indent=2, ensure_ascii=False)
