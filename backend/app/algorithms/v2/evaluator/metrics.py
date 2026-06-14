"""
多维度评价指标体系
==================

评价维度 (5大维度, 15+指标):

  1. 效率指标 (Efficiency) - 40%权重
     - makespan: 最大完成时间 (越短越好)
     - throughput: 任务吞吐量/小时
     - avg_completion_time: 平均任务完成时间

  2. 质量指标 (Quality) - 25%权重
     - total_distance: AGV总行驶距离
     - collision_rate: 碰撞率 (0-1)
     - deadlocks_resolved: 死锁解决数

  3. 资源利用率 (Resource) - 20%权重
     - agv_utilization: AGV平均利用率
     - battery_efficiency: 能耗效率
     - path_efficiency: 路径效率比 (最短路/实际路)

  4. 实时性 (Real-time) - 10%权重
     - compute_time_ms: 计算耗时
     - scalability_score: 规模伸缩得分

  5. 鲁棒性 (Robustness) - 5%权重
     - fault_tolerance: 故障容忍度
     - adaptability: 动态适应能力
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


class MetricCategory(Enum):
    """指标分类"""
    EFFICIENCY = "efficiency"
    QUALITY = "quality"
    RESOURCE = "resource"
    REALTIME = "realtime"
    ROBUSTNESS = "robustness"


@dataclass
class EvaluatorConfig:
    """评估器配置"""
    # 权重配置 (总和应为1.0)
    weights: Dict[str, float] = field(default_factory=lambda: {
        "efficiency": 0.40,
        "quality": 0.25,
        "resource": 0.20,
        "realtime": 0.10,
        "robustness": 0.05,
    })

    # 阈值设置
    max_acceptable_makespan: float = 3600.0      # 秒，超过此值视为失败
    max_compute_time_ms: float = 5000.0           # 毫秒，超时阈值
    max_collision_rate: float = 0.05              # 5%
    min_utilization_threshold: float = 0.30       # 30%

    # 是否启用详细日志
    verbose: bool = True


@dataclass
class DimensionScore:
    """单维度评分"""
    category: MetricCategory
    name: str
    score: float              # 0-100
    max_score: float = 100.0
    details: Dict[str, float] = field(default_factory=dict)

    @property
    def normalized(self) -> float:
        return self.score / self.max_score * 100 if self.max_score > 0 else 0.0

    def to_dict(self):
        return {
            "category": self.category.value,
            "name": self.name,
            "score": round(self.score, 2),
            "max_score": self.max_score,
            "normalized": round(self.normalized, 2),
            "details": {k: round(v, 4) for k, v in self.details.items()},
        }


@dataclass
class ScoreCard:
    """
    算法评分卡
    """

    algorithm_name: str
    algorithm_display_name: str
    scenario_name: str
    scenario_type: str
    total_score: float = 0.0          # 加权总分 0-100
    rank: int = 0                     # 在同场景中的排名
    dimension_scores: Dict[str, DimensionScore] = field(default_factory=dict)
    raw_metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_info: str = ""
    timestamp: str = ""

    @property
    def grade(self) -> str:
        if self.total_score >= 90: return "A+"
        elif self.total_score >= 80: return "A"
        elif self.total_score >= 70: return "B+"
        elif self.total_score >= 60: return "B"
        elif self.total_score >= 50: return "C"
        else: return "D"

    def to_dict(self):
        return {
            "algorithm_name": self.algorithm_name,
            "display_name": self.algorithm_display_name,
            "scenario": self.scenario_name,
            "scenario_type": self.scenario_type,
            "total_score": round(self.total_score, 2),
            "rank": self.rank,
            "grade": self.grade,
            "dimensions": {k: v.to_dict() for k, v in self.dimension_scores.items()},
            "raw_metrics": {k: (round(v, 4) if isinstance(v, (int,float)) else v)
                           for k, v in self.raw_metrics.items()},
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    def to_summary(self) -> str:
        lines = [
            f"{'='*60}",
            f" ScoreCard: {self.algorithm_display_name} | Scenario: {self.scenario_name}",
            f"{'='*60}",
            f" Total Score: {self.total_score:.1f}/100 | Grade: {self.grade} | Rank: #{self.rank}",
            f"{'-'*60}",
        ]
        for cat, ds in self.dimension_scores.items():
            lines.append(f"  [{cat:>12}] {ds.score:6.1f}/100  ({ds.name})")
            for mk, mv in ds.details.items():
                lines.append(f"               - {mk}: {mv:.4f}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)


@dataclass
class EvaluationMetrics:
    """
    原始指标计算结果（未归一化）

    由 Evaluator 从 AlgorithmResult + 场景数据中提取并计算
    """

    # === 效率指标 ===
    makespan: float = 0.0                    # 最大完成时间(秒)
    throughput_per_hour: float = 0.0         # 吞吐量(任务/小时)
    avg_completion_time: float = 0.0         # 平均完成时间
    completion_rate: float = 0.0             # 完成率 (0-1)

    # === 质量指标 ===
    total_distance: float = 0.0             # 总行驶距离(米)
    avg_distance_per_task: float = 0.0       # 平均每任务距离
    collision_count: int = 0                 # 碰撞次数
    collision_rate: float = 0.0              # 碰撞率
    deadlock_count: int = 0                  # 检测到的死锁
    deadlocks_resolved: int = 0              # 已解决死锁

    # === 资源利用 ===
    agv_utilization: float = 0.0            # 平均AGV利用率(0-1)
    avg_battery_consumed: float = 0.0         # 平均耗电量
    battery_efficiency: float = 0.0          # 距离/能耗比
    path_efficiency_ratio: float = 1.0       # 最短路/实际路 (>1表示绕行)
    idle_agv_count: int = 0                  # 空闲AGV数量

    # === 实时性 ===
    compute_time_ms: float = 0.0            # 计算耗时(ms)
    memory_usage_mb: float = 0.0             # 内存使用(MB)
    scalability_factor: float = 1.0          # 规模因子

    # === 鲁棒性 ===
    fault_recovered: bool = False            # 是否从故障恢复
    adaptation_events: int = 0               # 动态调整次数

    # === 元数据 ===
    num_agvs_used: int = 0                   # 实际使用的AGV数
    num_tasks_assigned: int = 0              # 实际分配的任务数
    success: bool = False                    # 执行是否成功

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()
                if not k.startswith("_")}

    def summary(self) -> Dict[str, float]:
        """返回用于评分的关键指标摘要"""
        return {
            "makespan": self.makespan,
            "throughput": self.throughput_per_hour,
            "completion_rate": self.completion_rate,
            "total_distance": self.total_distance,
            "collision_rate": self.collision_rate,
            "agv_utilization": self.agv_utilization,
            "compute_time_ms": self.compute_time_ms,
            "path_efficiency": self.path_efficiency_ratio,
            "fault_recovered": 1.0 if self.fault_recovered else 0.0,
        }


class MetricsEvaluator:
    """
    指标计算器

    从 AlgorithmResult 和场景数据中计算原始评价指标
    """

    def __init__(self, config: Optional[EvaluatorConfig] = None):
        self.config = config or EvaluatorConfig()

    def evaluate(
        self,
        result,  # AlgorithmResult
        scenario,  # AGVTMS_Scenario
    ) -> EvaluationMetrics:
        """
        计算完整指标集
        """
        metrics = EvaluationMetrics()
        metrics.success = result.success

        if not result.success:
            metrics.compute_time_ms = result.compute_time_ms
            return metrics

        nodes = scenario.nodes
        edges = scenario.edges
        tasks = scenario.tasks
        agvs = scenario.agvs

        n_tasks_total = len(tasks)
        n_agvs_total = len(agvs)

        # --- 基本统计 ---
        assignments = result.assignments or {}
        paths = result.paths or {}
        n_assigned = len(assignments)
        n_agv_used = len(paths)

        metrics.num_tasks_assigned = n_assigned
        metrics.num_agvs_used = n_agv_used
        metrics.completion_rate = n_assigned / n_tasks_total if n_tasks_total > 0 else 0
        metrics.compute_time_ms = result.compute_time_ms

        # --- 构建辅助映射 ---
        pos_map = {n["id"]: (n.get("x", 0), n.get("y", 0)) for n in nodes}
        edge_weights = {(e["from"], e["to"]): e.get("weight", 1.0) for e in edges}

        # --- 效率指标 ---
        metrics.makespan = self._estimate_makespan(result, tasks, agvs, pos_map)
        metrics.throughput_per_hour = (n_assigned / metrics.makespan * 3600) if metrics.makespan > 0 else 0
        metrics.avg_completion_time = metrics.makespan / 2 if n_assigned > 0 else 0  # 简化估计

        # --- 质量指标 ---
        total_dist = 0.0
        for agv_id, path_nodes in paths.items():
            for j in range(len(path_nodes) - 1):
                w = edge_weights.get((path_nodes[j], path_nodes[j+1]), 1.0)
                total_dist += w

        metrics.total_distance = total_dist
        metrics.avg_distance_per_task = total_dist / n_assigned if n_assigned > 0 else 0
        metrics.collision_rate = 0.0  # 需要碰撞检测模块，暂设为0
        metrics.collision_count = 0

        # --- 资源利用率 ---
        metrics.agv_utilization = n_agv_used / n_agvs_total if n_agvs_total > 0 else 0
        metrics.idle_agv_count = n_agvs_total - n_agv_used
        metrics.battery_consumed = sum(a.get("battery_level", 100) - 80 for a in agvs[:n_agv_used]) / max(n_agv_used, 1)
        metrics.path_efficiency_ratio = self._calc_path_efficiency(paths, nodes, edges)

        # --- 实时性 ---
        metrics.scalability_factor = self._calc_scalability(result, scenario)

        # --- 鲁棒性 ---
        metrics.fault_recovered = result.metadata.get("fault_recovered", False)
        metrics.adaptation_events = result.metadata.get("replan_count", 0)

        return metrics

    def _estimate_makespan(self, result, tasks, agvs, pos_map):
        """估算最大完工时间"""
        assignments = result.assignments or {}
        paths = result.paths or {}
        agv_map = {a["id"]: a for a in agvs}

        task_end_times = {}
        agv_free_at = {a["id"]: 0.0 for a in agvs}

        # 按任务顺序处理
        for task in tasks:
            tid = task.get("id", "")
            if tid not in assignments:
                continue

            aid = assignments[tid]
            path = paths.get(aid, [])

            if aid not in agv_free_at:
                continue

            start_time = agv_free_at[aid]

            # 路径时间 = 节点数 * 平均移动时间(简化)
            travel_time = len(path) * 2.0  # 假设每个节点2秒
            pickup_time = task.get("estimated_duration", 30)
            end_time = start_time + travel_time + pickup_time

            task_end_times[tid] = end_time
            agv_free_at[aid] = end_time

        return max(task_end_times.values()) if task_end_times else 0.0

    def _calc_path_efficiency(self, paths, nodes, edges):
        """计算路径效率比"""
        if not paths:
            return 1.0

        ratios = []
        pos_map = {n["id"]: (n.get("x",0), n.get("y",0)) for n in nodes}

        for path_nodes in paths.values():
            if len(path_nodes) < 2:
                continue

            actual_len = len(path_nodes) - 1  # 边数

            # 直线距离（欧几里得）
            start = pos_map.get(path_nodes[0], (0, 0))
            end = pos_map.get(path_nodes[-1], (0, 0))
            straight = ((start[0]-end[0])**2 + (start[1]-end[1])**2)**0.5

            if straight > 0:
                ratios.append(actual_len / straight)

        return sum(ratios) / len(ratios) if ratios else 1.0

    def _calc_scalability(self, result, scenario):
        """计算规模伸缩因子"""
        n = len(scenario.tasks) + len(scenario.agvs)
        t = result.compute_time_ms
        if t <= 0:
            return 100.0
        # O(n^2) 参考基准
        expected = n ** 2 * 0.001
        return min(expected / t * 100, 100) if expected > 0 else 100.0


class ScoringEngine:
    """
    评分引擎

    将原始指标归一化到 0-100 分制，按维度加权求和
    """

    def __init__(self, config: Optional[EvaluatorConfig] = None):
        self.config = config or EvaluatorConfig()

    def score(self, metrics: EvaluationMetrics) -> ScoreCard:
        """
        根据原始指标计算评分卡
        """
        dim_scores = {}

        # 1. 效率维度 (40%)
        dim_scores["efficiency"] = self._score_efficiency(metrics)

        # 2. 质量维度 (25%)
        dim_scores["quality"] = self._score_quality(metrics)

        # 3. 资源维度 (20%)
        dim_scores["resource"] = self._score_resource(metrics)

        # 4. 实时性维度 (10%)
        dim_scores["realtime"] = self._score_realtime(metrics)

        # 5. 鲁棒性维度 (5%)
        dim_scores["robustness"] = self._score_robustness(metrics)

        # 加权总分
        weights = self.config.weights
        total = (
            dim_scores["efficiency"].score * weights.get("efficiency", 0.40) +
            dim_scores["quality"].score * weights.get("quality", 0.25) +
            dim_scores["resource"].score * weights.get("resource", 0.20) +
            dim_scores["realtime"].score * weights.get("realtime", 0.10) +
            dim_scores["robustness"].score * weights.get("robustness", 0.05)
        )

        from datetime import datetime
        return ScoreCard(
            algorithm_name=metrics.algorithm_name if hasattr(metrics, 'algorithm_name') else "unknown",
            algorithm_display_name=getattr(metrics, 'algorithm_display_name', 'Unknown'),
            scenario_name=getattr(metrics, 'scenario_name', ''),
            scenario_type=getattr(metrics, 'scenario_type', ''),
            total_score=min(max(total, 0), 100),
            dimension_scores=dim_scores,
            raw_metrics=metrics.to_dict(),
            timestamp=datetime.now().isoformat(),
        )

    def _score_efficiency(self, m: EvaluationMetrics) -> DimensionScore:
        details = {}

        # 完成率: [0,1] → [0, 100]
        comp_score = m.completion_rate * 100
        details["completion_rate"] = comp_score

        # Makespan (相对评分): 假设参考范围 60s-3600s
        ref_min, ref_max = 60.0, 3600.0
        if m.makespan <= ref_min:
            make_score = 100.0
        elif m.makespan >= ref_max:
            make_score = 0.0
        else:
            make_score = 100.0 - ((m.makespan - ref_min) / (ref_max - ref_min)) * 100.0
        details["makespan"] = make_score

        # 吞吐量: 相对评分
        thr_score = min(m.throughput_per_hour / 50.0 * 100, 100)
        details["throughput"] = thr_score

        avg_score = (comp_score * 0.4 + make_score * 0.35 + thr_score * 0.25)
        return DimensionScore(MetricCategory.EFFICIENCY, "效率指标", avg_score, details=details)

    def _score_quality(self, m: EvaluationMetrics) -> DimensionScore:
        details = {}

        # 总距离: 越低越好 (假设参考范围)
        ref_low, ref_high = 10.0, 5000.0
        dist_norm = (m.total_distance - ref_low) / (ref_high - ref_low) if ref_high > ref_low else 0.5
        dist_score = max(0, 100.0 - dist_norm * 100.0)
        details["total_distance"] = dist_score

        # 碰撞率惩罚
        coll_penalty = m.collision_rate * 200  # 5%碰撞扣100分
        coll_score = max(0, 100.0 - coll_penalty)
        details["collision_rate"] = coll_score

        # 路径效率: ratio越接近1越好
        eff_deviation = abs(m.path_efficiency_ratio - 1.0) * 50
        eff_score = max(0, 100.0 - eff_deviation)
        details["path_efficiency"] = eff_score

        avg_score = dist_score * 0.40 + coll_score * 0.35 + eff_score * 0.25
        return DimensionScore(MetricCategory.QUALITY, "质量指标", avg_score, details=details)

    def _score_resource(self, m: EvaluationMetrics) -> DimensionScore:
        details = {}

        # AGV利用率: 最佳区间 60%-85%
        util = m.agv_utilization
        if 0.6 <= util <= 0.85:
            util_score = 100.0
        elif util < 0.6:
            util_score = util / 0.6 * 80.0
        else:
            util_score = max(0, 100.0 - (util - 0.85) * 150)
        details["agv_utilization"] = util_score

        # 路径效率比 (复用)
        peff = m.path_efficiency_ratio
        peff_score = max(0, 100.0 - (peff - 1.0) * 30)
        details["path_efficiency_ratio"] = peff_score

        avg_score = util_score * 0.6 + peff_score * 0.4
        return DimensionScore(MetricCategory.RESOURCE, "资源利用", avg_score, details=details)

    def _score_realtime(self, m: EvaluationMetrics) -> DimensionScore:
        details = {}

        # 计算时间: 越快越好
        t = m.compute_time_ms
        if t <= 10:
            time_score = 100.0
        elif t <= 100:
            time_score = 95.0 - (t - 10) / 90 * 20
        elif t <= 1000:
            time_score = 75.0 - (t - 100) / 900 * 50
        elif t <= self.config.max_compute_time_ms:
            time_score = 25.0 - (t - 1000) / (self.config.max_compute_time_ms - 1000) * 25
        else:
            time_score = 0.0
        details["compute_time"] = time_score

        # 可伸缩性
        scal_score = min(m.scalability_factor, 100.0)
        details["scalability"] = scal_score

        avg_score = time_score * 0.7 + scal_score * 0.3
        return DimensionScore(MetricCategory.REALTIME, "实时性能", avg_score, details=details)

    def _score_robustness(self, m: EvaluationMetrics) -> DimensionScore:
        details = {}

        # 故障恢复
        rec_score = 100.0 if m.fault_recovered else 50.0
        details["fault_recovery"] = rec_score

        # 自适应次数 (适度自适应是好的，过多说明不稳定)
        adap = m.adaptation_events
        if adap == 0:
            adap_score = 70.0
        elif adap <= 3:
            adap_score = 90.0
        elif adap <= 8:
            adap_score = 75.0
        else:
            adap_score = max(30.0, 80.0 - (adap - 8) * 5)
        details["adaptation"] = adap_score

        avg_score = rec_score * 0.5 + adap_score * 0.5
        return DimensionScore(MetricCategory.ROBUSTNESS, "鲁棒性", avg_score, details=details)


def create_score_card(
    algorithm_name: str,
    algorithm_display: str,
    scenario_name: str,
    scenario_type: str,
    result,
    scenario,
    config=None,
) -> ScoreCard:
    """
    便捷函数：直接生成评分卡
    """
    evaluator = MetricsEvaluator(config)
    scorer = ScoringEngine(config)
    metrics = evaluator.evaluate(result, scenario)
    metrics.algorithm_name = algorithm_name
    metrics.algorithm_display_name = algorithm_display
    metrics.scenario_name = scenario_name
    metrics.scenario_type = scenario_type
    return scorer.score(metrics)
