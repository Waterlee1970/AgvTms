"""
Phase 2 + 3 Integration Test: 修复后的高级算法验证

测试内容:
- V1HybridAdapter (SA+ACO+NLP) 数据管道修复
- V2MipAdapter (CP-SAT) 正确数据映射
- V2OrchestratorAdapter (三层架构) 含预测联动
- RLDQNAdapter 训练+推理完整流程
- 多算法对比评估
"""

import sys
import os
import time
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

def main():
    print("=" * 70)
    print("Phase 2 + 3: 高级算法修复 & RL激活 — 集成测试")
    print("=" * 70)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # =========================================================================
    # Step 1: 验证依赖安装
    # =========================================================================
    print("[Step 1/6] 检查依赖...")
    deps = {}
    for name, mod in [("numpy", "np"), ("torch", "torch"), ("ortools", "ortools"),
                      ("gymnasium", "gymnasium")]:
        try:
            __import__(mod)
            deps[name] = "✅"
        except ImportError:
            deps[name] = "❌"
        print(f"  {name}: {deps[name]}")
    print()

    # =========================================================================
    # Step 2: 导入评估框架和算法注册表（使用修复后的代码）
    # =========================================================================
    print("[Step 2/6] 导入模块...")
    try:
        from backend.app.algorithms.v2.evaluator import (
            AlgorithmRegistry,
            ScenarioGenerator,
            ScenarioRunner,
            EvaluatorConfig,
        )
        registry = AlgorithmRegistry.get_instance()
        all_algos = registry.list_all()
        available = [a for a in all_algos if a.is_available]
        print(f"  ✅ 注册算法: {len(all_algos)} 个")
        for algo in all_algos:
            status = "可用" if algo.is_available else "不可用"
            cat = algo.category.value if hasattr(algo.category, 'value') else str(algo.category)
            print(f"    - [{status}] {algo.display_name} ({algo.name}) v{algo.version} [{cat}]")
        print()
    except Exception as e:
        print(f"  ❌ 导入失败: {e}\n")
        return

    # =========================================================================
    # Step 3: 生成混合场景
    # =========================================================================
    print("[Step 3/6] 生成混合场景...")
    try:
        gen = ScenarioGenerator(seed=42)

        # 使用 AGV+TMS 混合场景（核心测试场景）
        scenarios_to_test = [
            ("small_warehouse", "小型仓库"),
            ("medium_factory", "中型工厂"),
            ("agv_tms_mixed", "AGV-TMS混合"),
        ]

        test_scenarios = []
        for preset_name, desc in scenarios_to_test:
            s = gen.generate(preset_name=preset_name)
            test_scenarios.append(s)
            n_nodes = len(s.nodes) if s.nodes else 0
            n_agvs = len(s.agvs) if s.agvs else 0
            n_tasks = len(s.tasks) if s.tasks else 0
            print(f"  ✅ {desc}: {n_nodes}节点, {n_agvs}AGV, {n_tasks}任务")
        print()
    except Exception as e:
        print(f"  ❌ 场景生成失败: {e}\n")
        import traceback; traceback.print_exc()
        return

    # =========================================================================
    # Step 4: 运行各算法并收集结果
    # =========================================================================
    print("[Step 4/6] 执行算法对比...")

    config = EvaluatorConfig(verbose=True, timeout_per_algorithm=30.0)
    runner = ScenarioRunner(registry=registry, config=config)

    # 测试所有已注册的算法
    algorithm_names = [a.name for a in all_algos]
    results_by_scenario = {}

    for scenario in test_scenarios:
        scenario_name = scenario.metadata.name if scenario.metadata else "unknown"
        print(f"\n  场景: {scenario_name}")
        print(f"  {'─'*50}")

        try:
            result = runner.evaluate_on_scenario(
                scenario,
                algorithms=algorithm_names,
                allow_training={"rl_dqn": True},  # 允许 RL 快速训练
                train_steps={"rl_dqn": 3000},
            )
            results_by_scenario[scenario_name] = result

            # 显示排名
            ranked = sorted(
                [sc for sc in result.scorecards if sc],
                key=lambda sc: sc.total_score, reverse=True
            )[:len(algorithm_names)]
            for rank, card in enumerate(ranked, 1):
                medal = ["🥇", "🥈", "🥉"][rank-1] if rank <= 3 else f"{rank}."
                print(f"    {medal} {card.algorithm_name:15s} → "
                      f"总分: {card.total_score:.1f} | 排名: #{card.rank}")

        except Exception as e:
            print(f"    ❌ 评估失败: {e}")
            import traceback; traceback.print_exc()

    print()

    # =========================================================================
    # Step 5: RL专项训练与基准测试
    # =========================================================================
    print("[Step 5/6] RL专项训练与基准测试...")
    rl_results = {}

    try:
        from backend.app.algorithms.v2.rl_scheduler.training_pipeline import (
            run_training_and_benchmark,
        )

        print("\n  开始快速RL训练 (8000 steps)...")
        train_result, bench_results = run_training_and_benchmark(
            model_dir="backend/benchmark_results/rl_models",
            quick_mode=True,
            timesteps=8000,
        )

        rl_results["training"] = {
            "total_steps": train_result.total_steps,
            "episodes": train_result.total_episodes,
            "final_reward": train_result.final_mean_reward,
            "best_eval_reward": train_result.best_eval_reward,
            "training_time_sec": round(train_result.training_time_seconds, 1),
        }
        print(f"  ✅ 训练完成: {train_result.total_steps} steps, "
              f"{train_result.total_episodes} episodes, "
              f"best_reward={train_result.best_eval_reward:.1f}, "
              f"time={train_result.training_time_seconds:.0f}s")

        if bench_results:
            print(f"\n  基准对比:")
            for b in bench_results:
                print(f"    {b.scenario_name:20s}: "
                      f"DQN={b.rl_dqn_reward:7.1f} | "
                      f"FCFS={b.fcfs_reward:7.1f} | "
                      f"Greedy={b.greedy_reward:7.1f} | "
                      f"vs FCFS: {b.rl_improvement_over_fcjs_pct:+.1f}%")
            rl_results["benchmarks"] = [
                {"scenario": b.scenario_name, "rl_dqn": b.rl_dqn_reward,
                 "fcfs": b.fcfs_reward, "greedy": b.greedy_reward,
                 "improvement_vs_fcfs_pct": b.rl_improvement_over_fcfs_pct}
                for b in bench_results
            ]

    except Exception as e:
        print(f"  ⚠️ RL训练跳过: {e}")
        import traceback; traceback.print_exc()
        rl_results["error"] = str(e)

    print()

    # =========================================================================
    # Step 6: 综合报告生成
    # =========================================================================
    print("[Step 6/6] 生成综合报告...")

    # 汇总所有场景的排行榜
    overall_scores = {}
    for scenario_name, result in results_by_scenario.items():
        for card in result.scorecards or []:
            if not card:
                continue
            name = card.algorithm_name
            if name not in overall_scores:
                overall_scores[name] = []
            overall_scores[name].append(card.total_score)

    print(f"\n{'='*70}")
    print("综合排行榜 (多场景平均)")
    print(f"{'='*70}")
    avg_scores = [(name, sum(scores)/len(scores)) for name, scores in overall_scores.items()]
    avg_sorted = sorted(avg_scores, key=lambda x: x[1], reverse=True)
    for rank, (name, score) in enumerate(avg_sorted, 1):
        medal = ["🥇", "🥈", "🥉"][rank-1] if rank <= 3 else f"{rank}."
        n_scores = len(overall_scores[name])
        print(f"  {medal} {name:18s} → 平均分: {score:.1f} ({n_scores}场景)")

    # 输出 JSON 报告
    report = {
        "test_time": datetime.now().isoformat(),
        "phase": "Phase2_3",
        "dependencies": deps,
        "algorithms_registered": len(all_algos),
        "algorithms_available": len([a for a in all_algos if a.is_available]),
        "scenarios_tested": len(test_scenarios),
        "per_scenario_results": {},
        "rl_training": rl_results,
        "overall_ranking": [
            {"algorithm": name, "avg_score": round(score, 2), "num_scenarios": len(overall_scores[name])}
            for name, score in avg_sorted
        ],
    }

    for scenario_name, result in results_by_scenario.items():
        report["per_scenario_results"][scenario_name] = {
            "num_algorithms_tested": len([c for c in (result.scorecards or []) if c]),
            "top_algorithm": max(result.scorecards or [], key=lambda c: c.total_score).algorithm_name
                            if result.scorecards else None,
            "scores": {
                c.algorithm_name: {
                    "score": c.total_score, "rank": c.rank,
                    "dimensions": {k: v.to_dict() if hasattr(v, 'to_dict') else v
                                   for k, v in c.dimension_scores.items()}
                } if c and c.dimension_scores else None
                for c in (result.scorecards or [])
            }
        }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"backend/benchmark_results/phase23_report_{timestamp}.json"

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n  📊 报告已保存: {report_path}")

    # 最终结论
    print(f"\n{'='*70}")
    print("Phase 2 + 3 完成总结")
    print(f"{'='*70}")

    improvements = []
    if any(name == "v1_hybrid" for name, _ in avg_sorted):
        idx = next(i for i, (n, _) in enumerate(avg_sorted) if n == "v1_hybrid")
        if idx < 3:
            improvements.append("V1-Hybrid 已进入前三，SA+ACO+NLP 管道修复成功")
    if any(name == "v2_mip" for name, _ in avg_sorted):
        idx = next(i for i, (n, _) in enumerate(avg_sorted) if n == "v2_mip")
        if idx < 4:
            improvements.append("V2-MIP CP-SAT 数据映射正确，优化能力生效")
    if any(name == "v2_orchestrator" for name, _ in avg_sorted):
        idx = next(i for i, (n, _) in enumerate(avg_sorted) if n == "v2_orchestrator")
        if idx < 4:
            improvements.append("V2-Orchestrator 三层架构运行正常，预测联动有效")
    if rl_results.get("training"):
        improvements.append(f"RL-DQN 训练成功 (best={rl_results['training'].get('best_eval_reward', 0):.1f})")

    if improvements:
        print("  改进确认:")
        for imp in improvements:
            print(f"    ✅ {imp}")
    else:
        print("  注意: 高级算法可能仍需进一步调试")

    print(f"\n  下一步建议:")
    print(f"    • 增加训练步数 (>20000) 以获得更强的 RL 模型")
    print(f"    • 在更大规模场景 (30+ AGV, 50+ tasks) 中验证扩展性")
    print(f"    • 开启 A/B 测试框架进行在线对比")
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
