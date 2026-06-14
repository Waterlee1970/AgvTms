#!/usr/bin/env python3
"""
多算法评估系统 - 完整测试与演示
=================================

运行方式:
    cd /Users/water/Documents/AgvTms
    python backend/tests/test_evaluator_full.py

功能:
  1. 注册所有算法 (FCFS, Greedy, V1-Hybrid, V2-MIP, V2-Orchestrator, RL-DQN)
  2. 生成多种混合场景 (仓库/工厂/港口/医院/高压/边界/AGV+TMS)
  3. 每个场景运行所有可用算法
  4. 计算5维15+指标并评分
  5. 生成对比报告 (Markdown + JSON + HTML)
"""

import sys
import os
import json
import time

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.app.algorithms.v2.evaluator import (
    AlgorithmRegistry,
    ScenarioGenerator,
    ScenarioRunner,
    ResultsVisualizer,
    EvaluatorConfig,
    generate_scenario_suite,
    evaluate_algorithms_on_scenario,
    run_full_evaluation,
    generate_evaluation_report_json,
)


def print_header(title):
    print(f"\n{'='*70}")
    print(f" {title}")
    print(f"{'='*70}")


def test_algorithm_registry():
    """测试1: 验证算法注册表"""
    print_header("Phase 1: Algorithm Registry")

    registry = AlgorithmRegistry.get_instance()
    all_algos = registry.list_all()
    available = registry.list_available()

    print(f"\n 已注册算法总数: {len(all_algos)}")
    for spec in all_algos:
        status = "✅" if spec.is_available else "❌ (依赖缺失)"
        deps = f"[需: {', '.join(spec.required_deps)}]" if not spec.is_available else ""
        print(f"   [{status}] {spec.name}: {spec.display_name} ({spec.category.value}) {deps}")

    return len(available) > 0


def test_scenario_generation():
    """测试2: 场景生成"""
    print_header("Phase 2: Scenario Generation")

    generator = ScenarioGenerator(seed=42)

    # 测试各类型场景生成
    presets_to_test = [
        ("small_warehouse", "小型仓库"),
        ("medium_warehouse", "中型仓库"),
        ("factory_floor", "工厂车间(含输送线)"),
        ("port_terminal", "港口码头"),
        ("hospital_logistics", "医院物流"),
        ("stress_test", "高压测试"),
        ("mixed_agv_tms", "AGV+TMS混合"),
    ]

    scenarios = []
    for preset_name, desc in presets_to_test:
        try:
            gen = ScenarioGenerator(seed=42 + hash(preset_name) % 10000)
            scene = gen.generate(preset_name=preset_name)

            has_conveyor = bool(scene.conveyor_tasks)
            info = (
                f"{scene.metadata.num_nodes}节点 | "
                f"{scene.metadata.num_edges}边 | "
                f"{scene.metadata.num_agvs} AGVs | "
                f"{scene.metadata.num_tasks} Tasks"
            )
            if has_conveyor:
                info += f" | {len(scene.conveyor_tasks)} 输送任务"

            print(f"\n   ✅ {desc}: {info}")
            print(f"      ID: {scene.metadata.scenario_id} | Tag: {', '.join(scene.metadata.tags or [])}")
            scenarios.append(scene)

        except Exception as e:
            print(f"\n   ❌ {desc}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n ✓ 成功生成 {len(scenarios)} 个场景")
    return scenarios


def run_single_scenario_comparison(scenario, algorithm_names=None):
    """测试3: 单场景多算法对比"""
    config = EvaluatorConfig(verbose=True, max_compute_time_ms=30000.0)
    runner = ScenarioRunner(config=config)

    print_header(f"Phase 3: Single Scenario Comparison - {scenario.metadata.name}")

    if algorithm_names is None:
        # 只使用不需要额外依赖的算法
        algorithm_names = ["fcfs", "greedy", "v1_hybrid", "v2_mip"]

    report = runner.run_comparison(scenario, algorithm_names=algorithm_names)

    print(f"\n{report.to_markdown()}")
    return report


def run_batch_evaluation():
    """测试4: 批量评估"""
    print_header("Phase 4: Batch Evaluation (All Scenarios × All Algorithms)")

    config = EvaluatorConfig(
        verbose=True,
        max_compute_time_ms=60000.0,
        weights={
            "efficiency": 0.40,
            "quality": 0.25,
            "resource": 0.20,
            "realtime": 0.10,
            "robustness": 0.05,
        }
    )

    runner = ScenarioRunner(config=config)

    # 选择要测试的算法和场景
    algo_names = ["fcfs", "greedy", "v1_hybrid", "v2_mip"]
    preset_list = [
        "small_warehouse",
        "medium_warehouse",
        "factory_floor",
        "port_terminal",
        "hospital_logistics",
        "stress_test",
        "mixed_agv_tms",
    ]

    batch_result = runner.run_batch(
        preset_names=preset_list,
        algorithm_names=algo_names,
        seed=42,
        variants_per_type=1,
    )

    print(f"\n\n{batch_result.summary_report}")

    return batch_result


def generate_reports(batch_result):
    """测试5: 生成报告"""
    print_header("Phase 5: Report Generation")

    visualizer = ResultsVisualizer()

    # JSON报告
    json_report = visualizer.export_json(batch_result)
    json_path = os.path.join(os.path.dirname(__file__), "..", "benchmark_results",
                            f"eval_report_{int(time.time())}.json")

    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, 'w', encoding='utf-8') as f:
        f.write(json_report)
    print(f"\n ✅ JSON报告已保存: {json_path}")

    # HTML报告
    html_content = visualizer.generate_html_report(batch_result)
    html_path = os.path.join(os.path.dirname(__file__), "..", "benchmark_results",
                            f"eval_report_{int(time.time())}.html")
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f" ✅ HTML报告已保存: {html_path}")

    # Markdown摘要
    md_lines = ["# AgvTms 多算法评估报告\n"]
    for report in batch_result.scenario_reports:
        md_lines.append(report.to_markdown())
        md_lines.append("\n---\n")

    md_path = os.path.join(os.path.dirname(__file__), "..", "docs",
                          "EVALUATION_REPORT.md")
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md_lines))
    print(f" ✅ Markdown报告已保存: {md_path}")

    # 图表数据
    radar_data = visualizer.radar_multi_scenario(batch_result.scenario_reports)
    bar_data = visualizer.bar_chart_total_scores(batch_result)
    heatmap_data = visualizer.heatmap_algorithm_matrix(batch_result)

    charts_path = os.path.join(os.path.dirname(json_path), "chart_data.json")
    with open(charts_path, 'w', encoding='utf-8') as f:
        json.dump({"radar": radar_data, "bar": bar_data, "heatmap": heatmap_data},
                  f, indent=2, ensure_ascii=False)
    print(f" ✅ 图表数据已保存: {charts_path}")

    return {"html": html_path, "json": json_path, "md": md_path}


def main():
    """主流程"""
    t_start = time.time()

    print("\n" + "=" * 70)
    print(" 🚀 AgvTms 多算法评价系统 v2.0")
    print(" Multi-Algorithm Evaluation Framework for AGV+TMS Hybrid Scheduling")
    print("=" * 70)

    # Phase 1: 算法注册
    ok = test_algorithm_registry()
    if not ok:
        print("\n ⚠️ 无可用算法，退出测试")
        return

    # Phase 2: 场景生成
    scenarios = test_scenario_generation()
    if not scenarios:
        print("\n ⚠️ 未成功生成任何场景，退出测试")
        return

    # Phase 3: 单场景快速验证（用最小场景）
    small_scene = next((s for s in scenarios if "small" in s.metadata.name.lower()), scenarios[0])
    single_report = run_single_scenario_comparison(small_scene, ["fcfs", "greedy"])

    # Phase 4: 完整批量评估
    batch_result = run_batch_evaluation()

    # Phase 5: 报告生成
    outputs = generate_reports(batch_result)

    elapsed = time.time() - t_start

    # 最终汇总
    print_header("Final Summary")
    
    winner = batch_result.overall_rankings
    top_algo = list(winner.keys())[0] if winner else "N/A"
    top_score = list(winner.values())[0] if winner else 0

    print(f"""
 ┌─────────────────────────────────────────────────────┐
 │              评估完成!                                │
 ├─────────────────────────────────────────────────────┤
 │ 总耗时:       {elapsed:>8.1f}s                            │
 │ 测试场景数:   {len(batch_result.scenario_reports):>8d}                           │
 │ 参评算法数:   {len(winner):>8d}                             │
 │                                                     │
 │ 🏆 最优算法:   {top_algo:<16s} ({top_score:.1f}分)          │
 │                                                     │
 │ 输出文件:                                            │
 │   HTML:  {outputs['html'][-50:]:>52s}     │
 │   JSON:  {outputs['json'][-52:]:>52s}  │
 │   MD:    {outputs['md'][-55:]:>53s}      │
 └─────────────────────────────────────────────────────┘
""")


if __name__ == "__main__":
    main()
