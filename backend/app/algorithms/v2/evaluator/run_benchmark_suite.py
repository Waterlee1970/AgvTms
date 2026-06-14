#!/usr/bin/env python3
"""
AGV-TMS 混合场景算法评测验证脚本
====================================

功能:
1. 生成多种 AGV+TMS 混合样本场景
2. 运行所有注册算法进行对比测试
3. 收集5维评价体系的详细指标
4. 输出对比分析报告 (Markdown + JSON)
5. 验证算法兼容性和系统稳定性

使用方式:
    cd /Users/water/Documents/AgvTms/backend
    python -m app.algorithms.v2.evaluator.run_benchmark_suite
    
    # 或指定参数
    python -m app.algorithms.v2.evaluator.run_benchmark_suite --seed 123 --variants 3
"""

import sys
import os
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

# 确保项目路径正确
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# ==================== 导入核心模块 ====================

try:
    from app.algorithms.v2.evaluator.registry import (
        get_registry, AlgorithmRegistry, BaseAlgorithm, AlgorithmResult
    )
    from app.algorithms.v2.evaluator.scenarios import (
        ScenarioGenerator, ScenarioType, ScenarioDifficulty,
        generate_scenario_suite, AGVTMS_Scenario
    )
    from app.algorithms.v2.evaluator.runner import (
        ScenarioRunner, ComparisonReport, BatchEvaluationResults,
        evaluate_algorithms_on_scenario, run_full_evaluation,
    )
    from app.algorithms.v2.evaluator.metrics import (
        MetricsEvaluator, ScoringEngine, ScoreCard,
        EvaluatorConfig, DimensionScore, create_score_card,
    )
    from app.algorithms.v2.evaluator.visualizer import ResultsVisualizer
except ImportError as e:
    print(f"[ERROR] 模块导入失败: {e}")
    print("请确保在 backend 目录下运行此脚本")
    sys.exit(1)


# ==================== 配置常量 ====================

BENCHMARK_SCENARIOS = [
    # (预设名称, 描述, 是否为混合场景)
    ("small_warehouse", "小型仓库(基准)", False),
    ("medium_warehouse", "中型仓库(标准)", False),
    ("large_warehouse", "大型仓库(压力)", False),
    ("factory_floor", "工厂车间", True),       # ★ 含输送线
    ("port_terminal", "港口码头", False),
    ("hospital_logistics", "医院物流", False),
    ("stress_test", "极限压力测试", False),
    ("mixed_agv_tms", "AGV+TMS混合(重点)", True),  # ★★ 核心混合场景
]

OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "benchmark_results"


def ensure_output_dir():
    """确保输出目录存在"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_header(title: str):
    """打印格式化标题"""
    width = 70
    print("\n" + "=" * width)
    print(f" {title}".center(width))
    print("=" * width)


def print_section(title: str):
    """打印小节标题"""
    print(f"\n--- {title} ---")


def run_single_scenario_test(
    scenario_name: str,
    is_mixed: bool,
    seed: int = 42,
) -> ComparisonReport | None:
    """
    运行单个场景的完整测试流程
    
    Args:
        scenario_name: 场景预设名称
        is_mixed: 是否为AGV+TMS混合场景
        seed: 随机种子
        
    Returns:
        ComparisonReport 或 None (如果失败)
    """
    print(f"\n[TEST] 场景: {scenario_name} {'(混合)' if is_mixed else ''}")
    
    try:
        # 1. 生成场景
        gen = ScenarioGenerator(seed=seed)
        scenario = gen.generate(preset_name=scenario_name)
        
        meta = scenario.metadata
        print(f"  ✓ 场景生成成功")
        print(f"    类型: {meta.scenario_type.value}, 难度: {meta.difficulty.value}")
        print(f"    节点: {meta.num_nodes}, 边: {meta.num_edges}")
        print(f"    AGV: {meta.num_agvs}, 任务: {meta.num_tasks}")
        if scenario.conveyor_tasks:
            print(f"    ★ 输送线任务: {len(scenario.conveyor_tasks)} 个")
            print(f"    ★ 输送线段: {len(scenario.conveyor_segments)} 段")

        # 2. 初始化运行器 (使用自定义权重配置)
        config = EvaluatorConfig(
            weights={
                "efficiency": 0.35,   # 效率优先
                "quality": 0.25,      # 质量
                "resource": 0.20,     # 资源利用
                "realtime": 0.15,     # 实时性 (提升)
                "robustness": 0.05,   # 鲁棒性
            },
            verbose=True,
        )
        runner = ScenarioRunner(config=config)

        # 3. 运行对比评估
        report = runner.run_comparison(scenario)
        
        return report

    except Exception as e:
        print(f"  ✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def analyze_algorithm_compatibility(
    reports: list[ComparisonReport],
) -> dict:
    """
    分析算法兼容性矩阵
    
    Returns:
        兼容性统计信息
    """
    stats = {
        "algorithms_tested": set(),
        "algorithms_succeeded": {},
        "algorithms_failed": {},
        "total_scenarios": len(reports),
        "compatibility_matrix": {},   # algo -> {success_count, fail_count, avg_score}
    }
    
    for report in reports:
        for algo_name, card in report.results.items():
            stats["algorithms_tested"].add(algo_name)
            
            if algo_name not in stats["compatibility_matrix"]:
                stats["compatibility_matrix"][algo_name] = {
                    "successes": 0,
                    "failures": 0,
                    "scores": [],
                    "errors": [],
                }
            
            mat = stats["compatibility_matrix"][algo_name]
            
            if card.error_info or card.total_score == 0:
                mat["failures"] += 1
                mat["errors"].append(card.error_info[:100] if card.error_info else "Zero score")
            else:
                mat["successes"] += 1
                mat["scores"].append(card.total_score)
    
    # 计算统计数据
    for algo, mat in stats["compatibility_matrix"].items():
        mat["avg_score"] = sum(mat["scores"]) / len(mat["scores"]) if mat["scores"] else 0
        mat["min_score"] = min(mat["scores"]) if mat["scores"] else 0
        mat["max_score"] = max(mat["scores"]) if mat["scores"] else 0
        mat["success_rate"] = mat["successes"] / stats["total_scenarios"] * 100
    
    return stats


def generate_analysis_report(
    reports: list[ComparisonReport],
    compat_stats: dict,
    test_params: dict,
) -> str:
    """
    生成完整的分析报告 (Markdown)
    
    包含:
    1. 执行摘要
    2. 各场景排名表
    3. 综合排行榜
    4. 维度分析对比
    5. 算法兼容性矩阵
    6. 推荐建议
    """
    
    lines = []
    lines.append("# AgvTms 多算法评测验证报告\n")
    
    # === 元数据 ===
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**随机种子**: {test_params.get('seed', 42)}\n")
    lines.append(f"**场景变体数**: {test_params.get('variants', 1)}\n")
    lines.append(f"**测试场景数**: {len(reports)}\n")
    
    valid_reports = [r for r in reports if r.results]
    if valid_reports:
        all_algos = set()
        for r in valid_reports:
            all_algos.update(r.results.keys())
        lines.append(f"**参评算法数**: {len(all_algos)}\n")
    
    lines.append("---\n")

    # === 1. 执行摘要 ===
    lines.append("## 1. 执行摘要\n")
    
    # 综合平均分计算
    overall_scores = {}
    algo_appearances = {}
    for report in valid_reports:
        for name, card in report.results.items():
            if not card.error_info and card.total_score > 0:
                overall_scores.setdefault(name, []).append(card.total_score)
                algo_appearances[name] = algo_appearances.get(name, 0) + 1

    avg_scores = {n: sum(s)/len(s) for n, s in overall_scores.items()}
    sorted_avg = sorted(avg_scores.items(), key=lambda x: -x[1])
    
    if sorted_avg:
        winner = sorted_avg[0]
        lines.append(f"- **最佳综合表现**: **{winner[0]}** (平均 {winner[1]:.1f} 分)\n")
        
        if len(sorted_avg) > 1:
            margin = winner[1] - sorted_avg[1][1]
            if margin > 10:
                lines.append(f"- 领先第二名 ({sorted_avg[1][0]}) {margin:.1f} 分，优势明显\n")
            elif margin > 3:
                lines.append(f"- 略胜第二名 ({sorted_avg[1][0]}) {margin:.1f} 分\n")
    
    # 统计各等级
    grade_dist = {}
    for scores in overall_scores.values():
        avg = sum(scores)/len(scores)
        grade = 'A+' if avg >= 90 else 'A' if avg >= 80 else 'B+' if avg >= 70 else 'B' if avg >= 60 else 'C' if avg >= 50 else 'D'
        grade_dist[grade] = grade_dist.get(grade, 0) + 1
    
    lines.append(f"- **评级分布**: {', '.join([f'{g}: {c}' for g, c in sorted(grade_dist.items())])}\n")
    lines.append("")

    # === 2. 各场景详细结果 ===
    lines.append("## 2. 各场景评测详情\n")
    
    for i, report in enumerate(valid_reports):
        lines.append(f"### 2.{i+1}. {report.scenario_name}\n")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 场景类型 | `{report.scenario_type}` |")
        lines.append(f"| 参评算法 | {len(report.results)} 个 |")
        lines.append(f"| 计算耗时 | {report.execution_time_total_ms:.0f} ms |")
        lines.append(f"| 获胜算法 | **{report.winner}** |")
        lines.append("")
        
        # 排名表
        lines.append(f"| 排名 | 算法 | 总分 | 等级 | 效率 | 质量 | 资源 | 实时 | 鲁棒 |")
        lines.append(f"|:----:|------|:----:|:----:|:----:|:----:|:----:|:----:|:----:|")
        
        for rank, (name, score) in enumerate(report.rankings, 1):
            sc = report.results.get(name)
            if sc and sc.dimension_scores:
                dims = sc.dimension_scores
                d_eff = dims.get('efficiency', DimensionScore(None,'',0)).score
                d_qual = dims.get('quality', DimensionScore(None,'',0)).score
                d_res = dims.get('resource', DimensionScore(None,'',0)).score
                d_rt = dims.get('realtime', DimensionScore(None,'',0)).score
                d_rob = dims.get('robustness', DimensionScore(None,'',0)).score
                
                marker = ' 🏆' if rank == 1 else ''
                lines.append(f"| #{rank} | {sc.algorithm_display_name}{marker} | "
                            f"**{sc.total_score:.1f}** | "
                            f"{sc.grade} | "
                            f"{d_eff:.0f} | {d_qual:.0f} | {d_res:.0f} | "
                            f"{d_rt:.0f} | {d_rob:.0f} |")
        
        lines.append("")
        
        # 分析摘要
        if report.summary:
            lines.append(f"> 💡 {report.summary}\n")
        
        lines.append("")

    # === 3. 综合排行榜 ===
    lines.append("## 3. 综合排行榜\n")
    lines.append("| 排名 | 算法 | 平均分 | 最高分 | 最低分 | 出场次数 | 成功率 |")
    lines.append("|:----:|------|:------:|:------:|:------:|:--------:|:------:|")
    
    for rank, (name, avg) in enumerate(sorted_avg, 1):
        mat = compat_stats.get("compatibility_matrix", {}).get(name, {})
        scores = mat.get("scores", [])
        hi = max(scores) if scores else 0
        lo = min(scores) if scores else 0
        appearances = mat.get("successes", 0) + mat.get("failures", 0)
        rate = mat.get("success_rate", 0)
        
        line = f"| #{rank} | **{name}** | **{avg:.1f}** | {hi:.1f} | {lo:.1f} | {appearances} | {rate:.0f}% |"
        lines.append(line)
    
    lines.append("")

    # === 4. 算法兼容性分析 ===
    lines.append("## 4. 算法兼容性与稳定性\n")
    lines.append("")
    lines.append("| 算法 | 成功/总 | 成功率 | 平均分 | 异常记录 |")
    lines.append("|------|:-------:|:------:|:------:|----------|")
    
    for name, mat in compat_stats.get("compatibility_matrix", {}).items():
        total = mat["successes"] + mat["failures"]
        rate = mat["success_rate"]
        errors = "; ".join(mat.get("errors", [])[:2])  # 只显示前两条错误
        
        status_icon = "✅" if rate >= 90 else "⚠️" if rate >= 70 else "❌"
        lines.append(f"| {name} | {mat['successes']}/{total} | {rate:.0f}% | {mat['avg_score']:.1f} | {errors or '-'} |")
    
    lines.append("")

    # === 5. 维度专项分析 ===
    lines.append("## 5. 五维能力深度分析\n")
    lines.append("")
    
    dim_labels = ["efficiency", "quality", "resource", "realtime", "robustness"]
    dim_names = ["效率", "质量", "资源利用", "实时性能", "鲁棒性"]
    
    for dim_key, dim_name in zip(dim_labels, dim_names):
        lines.append(f"### 5.{dim_labels.index(dim_key)+1}. {dim_name}维度\n")
        lines.append("")
        lines.append("| 算法 | 平均分 | 最高 | 最低 | 波动 |")
        lines.append("|------|:------:|:----:|:----:|:----:|")
        
        dim_scores = {}
        for report in valid_reports:
            for name, card in report.results.items():
                ds = card.dimension_scores.get(dim_key)
                if ds and ds.score > 0:
                    dim_scores.setdefault(name, []).append(ds.score)
        
        dim_avg = {n: sum(s)/len(s) for n, s in dim_scores.items()}
        sorted_dim = sorted(dim_avg.items(), key=lambda x: -x[1])
        
        for name, avg in sorted_dim[:6]:  # Top 6
            scores = dim_scores[name]
            hi = max(scores)
            lo = min(scores)
            var = max(scores) - min(scores)
            
            leader = " 👑" if avg == sorted_dim[0][1] else ""
            lines.append(f"| {name}{leader} | {avg:.1f} | {hi:.0f} | {lo:.0f} | {var:.1f} |")
        
        lines.append("")

    # === 6. 使用推荐 ===
    lines.append("## 6. 使用建议与结论\n")
    lines.append("")
    
    if sorted_avg:
        top_algo = sorted_avg[0][0]
        top_score = sorted_avg[0][1]
        
        lines.append(f"### 总体推荐\n")
        if top_score >= 80:
            lines.append(f"- ✅ **{top_algo}** 综合表现优秀({top_score:.1f}分)，适合作为默认调度策略\n")
        elif top_score >= 65:
            lines.append(f"- ⚡ **{top_algo}** 表现良好({top_score:.1f}分)，可作为主要调度策略\n")
        else:
            lines.append(f"- 🔧 **{top_algo}** 相对较优({top_score:.1f}分)，但整体仍有优化空间\n")
        
        # 按场景类型推荐
        best_by_type = {}
        for report in valid_reports:
            stype = report.scenario_type
            if stype and report.winner:
                best_by_type.setdefault(stype, {})[report.winner] = \
                    best_by_type.get(stype, {}).get(report.winner, 0) + \
                    report.results.get(report.winner, ScoreCard).__dict__.get('total_score', 0)
        
        type_recs = {}
        for stype, algo_scores in best_by_type.items():
            best_algo = max(algo_scores, key=algo_scores.get)
            type_recs[stype] = (best_algo, algo_scores[best_algo])
        
        if type_recs:
            lines.append(f"\n### 按场景类型推荐\n")
            for stype, (best, score) in sorted(type_recs.items()):
                rec_text = f"- **[{stype}]** → {best}"
                if best != top_algo:
                    rec_text += f" (在此类场景中优于默认选择)"
                lines.append(f"{rec_text}\n")
        
        # 性能敏感推荐
        fast_algos = [(n, s) for n, s in sorted_avg if any(
            r.results[n].dimension_scores.get('realtime', DimensionScore(None,'',0)).score > 75
            for r in valid_reports if n in r.results
        )]
        if fast_algos:
            fastest = min(fast_algos, key=lambda x: x[1])
            lines.append(f"\n### 特殊需求推荐\n")
            lines.append(f"- ⏱️ **低延迟需求**: **{fastest[0]}** (实时性维度得分较高)\n")

    lines.append("")
    lines.append("---\n")
    lines.append(f"*报告由 AgvTms Benchmark Suite 自动生成*\n")
    
    return "\n".join(lines)


def main():
    """主执行函数"""
    parser = argparse.ArgumentParser(description='AgvTms 多算法评测套件')
    parser.add_argument('--seed', type=int, default=42, help='随机种子 (默认: 42)')
    parser.add_argument('--variants', type=int, default=1, help='每种场景变体数 (默认: 1)')
    parser.add_argument('--output-dir', type=str, default=None, help='输出目录')
    parser.add_argument('--scenarios', nargs='+', default=None, help='指定要测试的场景 (默认全部)')
    args = parser.parse_args()

    # 参数处理
    seed = args.seed
    variants = args.variants
    if args.output_dir:
        global OUTPUT_DIR
        OUTPUT_DIR = Path(args.output_dir)
    
    target_scenarios = args.scenarios or [s[0] for s in BENCHMARK_SCENARIOS]
    
    ensure_output_dir()

    print_header("AgvTms 多算法兼容性评测验证套件")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"种子: {seed}")
    print(f"变体: {variants}/场景")
    print(f"目标场景: {len(target_scenarios)} 种")
    print(f"输出目录: {OUTPUT_DIR}")

    # 显示可用算法
    registry = get_registry()
    all_algos = registry.list_all()
    available = [a for a in all_algos if a.is_available]
    
    print_section("已注册算法")
    print(f"  总计: {len(all_algos)} 个, 可用: {len(available)} 个")
    for a in all_algos:
        status = "✓" if a.is_available else "✗"
        deps = f" (需: {', '.join(a.required_deps)})" if a.required_deps else ""
        print(f"    [{status}] {a.name:20s} - {a.display_name}{deps}")

    # ===== 执行评测 =====
    print_section("开始场景评测")
    
    all_reports = []
    start_time = time.time()
    
    for i, (preset_name, desc, is_mixed) in enumerate(BENCHMARK_SCENARIOS):
        if preset_name not in target_scenarios:
            continue
            
        print(f"\n[{i+1}/{len(target_scenarios)}] 处理: {desc} ({preset_name})")
        
        # 支持变体
        for v in range(variants):
            v_seed = seed + v * 1000
            report = run_single_scenario_test(preset_name, is_mixed, seed=v_seed)
            
            if report:
                all_reports.append(report)
                
                # 打印简要结果
                print(f"\n  结果摘要:")
                for rank, (name, score) in enumerate(report.rankings[:5], 1):
                    card = report.results.get(name)
                    grade = card.grade if card else '?'
                    print(f"    #{rank} {name}: {score:.1f}分 ({grade})")

    elapsed = time.time() - start_time

    # ===== 分析结果 =====
    print_section("分析评测结果")
    
    valid_reports = [r for r in all_reports if r.results]
    print(f"  有效报告: {len(valid_reports)}/{len(all_reports)}")
    
    # 兼容性分析
    compat_stats = analyze_algorithm_compatibility(valid_reports)
    
    print(f"\n  算法成功率:")
    for algo, mat in compat_stats.get("compatibility_matrix", {}).items():
        rate = mat.get("success_rate", 0)
        icon = "✅" if rate >= 90 else "⚠️" if rate >= 70 else "❌"
        print(f"    [{icon}] {algo:25s} : {mat['successes']}/{mat['successes']+mat['failures']} ({rate:.0f}%) - 均分:{mat['avg_score']:.1f}")

    # ===== 生成报告 =====
    print_section("生成输出文件")
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Markdown 报告
    md_content = generate_analysis_report(valid_reports, compat_stats, vars(args))
    md_path = OUTPUT_DIR / f"benchmark_report_{timestamp}.md"
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"  ✓ Markdown报告: {md_path}")
    
    # JSON 数据
    json_data = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "seed": seed,
            "variants_per_type": variants,
            "total_scenarios": len(valid_reports),
            "elapsed_seconds": round(elapsed, 2),
        },
        "algorithm_registry": [
            {"name": a.name, "display_name": a.display_name, "available": a.is_available}
            for a in all_algos
        ],
        "scenario_reports": [r.to_dict() for r in valid_reports],
        "overall_rankings": dict(avg_scores := {
            n: round(sum(s)/len(s), 2)
            for r in valid_reports 
            for n, s in ((nm, []),) if False  # placeholder
        }),
        "compatibility_stats": {
            k: {kk: vv if kk != 'errors' else vv[:3] for kk, vv in v.items()}
            for k, v in compat_stats.get("compatability_matrix", compat_stats.get("compatibility_matrix", {})).items()
        },
    }
    
    # 正确计算 overall_rankings
    _overall = {}
    for r in valid_reports:
        for name, card in r.results.items():
            if card.total_score > 0 and not card.error_info:
                _overall.setdefault(name, []).append(card.total_score)
    json_data["overall_rankings"] = {
        n: round(sum(s)/len(s), 2) for n, s in _overall.items()
    }
    
    json_path = OUTPUT_DIR / f"benchmark_data_{timestamp}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"  ✓ JSON数据: {json_path}")

    # ===== 完成 =====
    print_header("评测完成!")
    print(f"\n  总耗时: {elapsed:.1f}s")
    print(f"  有效场景: {len(valid_reports)}")
    print(f"  输出文件:")
    print(f"    - {md_path}")
    print(f"    - {json_path}")
    
    if _overall and sorted(_overall.items(), key=lambda x: -x[1]):
        winner_name, winner_score = sorted(_overall.items(), key=lambda x: -x[1])[0]
        print(f"\n  🏆 最佳算法: {winner_name} ({winner_score:.1f}分)")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
