#!/usr/bin/env python3
"""
k6 风格 AGV-TMS 负载压测脚本

模拟 50-200 AGV 的调度负载, 测量:
- P50/P95/P99 调度延迟
- 任务完成率
- 系统吞吐量 (tasks/sec)
- 内存占用趋势

用法:
    # 基线测试 (50 AGV, 100 tasks, 5轮)
    python3 load_test_k6.py --agvs 50 --tasks 100 --rounds 5

    # 压力测试 (200 AGV, 500 tasks)
    python3 load_test_k6.py --agvs 200 --tasks 500 --rounds 3

    # 持续压力测试 (30min, 每2秒一批任务)
    python3 load_test_k6.py --agv 100 --mode sustained --duration 1800

输出:
    - JSON: results/load_test_YYYYMMDD_HHMMSS.json
    - Markdown: results/load_test_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc
import logging
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger("load_test")


@dataclass
class LoadTestConfig:
    """压测配置"""
    num_agvs: int = 50
    num_tasks: int = 100
    rounds: int = 5
    mode: str = "burst"          # burst | sustained | rampup
    duration_seconds: int = 300   # 仅sustained模式使用
    rampup_max_agvs: int = 200   # 仅rampup模式使用
    timeout_per_request: float = 30.0  # [G1-FIX] 单次调度最大等待时间(s)


@dataclass
class RoundResult:
    """单轮测试结果"""
    round_id: int
    agvs: int
    tasks: int
    algorithm: str
    success: bool = True
    compute_time_ms: float = 0.0
    assignments_count: int = 0
    paths_count: int = 0
    error_message: str = ""
    
    # 详细指标
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    completion_rate: float = 0.0
    
    # 资源占用
    peak_memory_mb: float = 0.0
    cpu_time_s: float = 0.0


@dataclass
class LoadTestReport:
    """完整测试报告"""
    config: Dict
    test_id: str
    timestamp: str
    rounds_results: List[RoundResult] = field(default_factory=list)
    
    # 汇总统计
    total_rounds: int = 0
    successful_rounds: int = 0
    success_rate: float = 0.0
    
    avg_compute_time_ms: float = 0.0
    max_compute_time_ms: float = 0.0
    min_compute_time_ms: float = 0.0
    
    overall_p50: float = 0.0
    overall_p95: float = 0.0
    overall_p99: float = 0.0
    
    throughput_tasks_per_sec: float = 0.0
    peak_memory_mb: float = 0.0
    
    # G1 目标检查
    g1_stable_30min: bool = False
    g1_p99_under_5s: bool = False
    g1_no_crashes: bool = True
    g1_ready: bool = False
    
    def to_dict(self) -> dict:
        d = {
            "config": self.config,
            "test_id": self.test_id,
            "timestamp": self.timestamp,
            "summary": {
                "total_rounds": self.total_rounds,
                "successful_rounds": self.successful_rounds,
                "success_rate": f"{self.success_rate:.1%}",
                "avg_compute_time_ms": round(self.avg_compute_time_ms, 1),
                "max_compute_time_ms": round(self.max_compute_time_ms, 1),
                "overall_p50_ms": round(self.overall_p50, 1),
                "overall_p95_ms": round(self.overall_p95, 1),
                "overall_p99_ms": round(self.overall_p99, 1),
                "throughput_tps": round(self.throughput_tasks_per_sec, 2),
                "peak_memory_mb": round(self.peak_memory_mb, 1),
                "g1_goals": {
                    "stable_30min": self.g1_stable_30min,
                    "p99_under_5s": self.g1_p99_under_5s,
                    "no_crashes": self.g1_no_crashes,
                    "g1_ready": self.g1_ready,
                }
            },
            "rounds": [asdict(r) for r in self.rounds_results],
        }
        return d


class LoadTestRunner:
    """AGV-TMS 负载压测运行器"""

    ALGORITHMS_TO_TEST = ["fcfs", "greedy", "v2_mip", "v2_orchestrator"]
    # v1_hybrid 太慢(120s), 默认不包含在快速压测中; 可通过 --algorithms 手动添加

    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.report = LoadTestReport(
            config=asdict(config),
            test_id=f"LT_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now().isoformat(),
        )

    def _generate_scenario(
        self, num_agvs: int, num_tasks: int, seed: int = 42
    ) -> Tuple[List[dict], List[dict], List[dict], List[dict]]:
        """生成随机测试场景 (nodes, edges, tasks, agvs)"""
        import random
        rng = random.Random(seed)

        # Grid map: 10x8 nodes (warehouse layout)
        nodes = []
        for r in range(8):
            for c in range(10):
                node_type = "STATION" if (r % 2 == 0 and c % 3 == 0) else "PATH"
                nodes.append({
                    "id": f"n_{r}_{c}",
                    "name": f"N{r}{c}",
                    "x": c * 5.0 + rng.uniform(-0.5, 0.5),
                    "y": r * 5.0 + rng.uniform(-0.5, 0.5),
                    "node_type": node_type,
                })

        # Edges: grid connections + some diagonal shortcuts
        edges = []
        for r in range(8):
            for c in range(10):
                nid = f"n_{r}_{c}"
                if c < 9:  # right neighbor
                    edges.append({"id": f"e_{nid}_right", "from_node": nid, "to_node": f"n_{r}_{c+1}", "distance": 5.0})
                if r < 7:  # bottom neighbor
                    edges.append({"id": f"e_{nid}_down", "from_node": nid, "to_node": f"n_{r+1}_{c}", "distance": 5.0})

        # AGVs at random positions
        station_nodes = [n["id"] for n in nodes if n["node_type"] == "STATION"]
        path_nodes = [n["id"] for n in nodes if n["node_type"] == "PATH"]
        agvs = []
        for i in range(num_agvs):
            pos = rng.choice(station_nodes or path_nodes)
            agvs.append({
                "id": f"AGV-{i+1:03d}",
                "current_node": pos,
                "battery": rng.uniform(60.0, 100.0),
                "status": "idle",
                "speed": 1.5,
                "capacity": 1,
                "x": next((n["x"] for n in nodes if n["id"] == pos), 0),
                "y": next((n["y"] for n in nodes if n["id"] == pos), 0),
            })

        # Tasks: pickup/dropoff pairs
        tasks = []
        stations = station_nodes * 3  # allow repetition
        for i in range(num_tasks):
            pickup = rng.choice(stations)
            dropoff = rng.choice([s for s in stations if s != pickup] or stations)
            priority_choices = [1, 5, 5, 5, 10, 20]  # mostly NORMAL
            tasks.append({
                "id": f"T-{i+1:04d}",
                "pickup_node_id": pickup,
                "dropoff_node_id": dropoff,
                "priority": rng.choice(priority_choices),
                "status": "pending",
            })

        return nodes, edges, tasks, agvs

    def run_single_algorithm(
        self, algo_name: str, nodes, edges, tasks, agvs, round_id: int
    ) -> RoundResult:
        """运行单个算法的单轮测试"""
        result = RoundResult(
            round_id=round_id,
            agvs=len(agvs),
            tasks=len(tasks),
            algorithm=algo_name,
        )

        tracemalloc.start()
        t_start = time.perf_counter()

        try:
            from backend.app.algorithms.v2.evaluator.runner import ScenarioRunner
            from backend.app.algorithms.v2.evaluator.scenarios import AGVTMS_Scenario, ScenarioMetadata, ScenarioType, ScenarioDifficulty

            # 构建场景对象
            scenario = AGVTMS_Scenario(
                nodes=nodes,
                edges=edges,
                tasks=tasks,
                agvs=agvs,
                metadata=ScenarioMetadata(
                    scenario_id=f"loadtest_r{round_id}_{algo_name}",
                    name=f"LoadTest_R{round_id}_{algo_name}",
                    scenario_type=ScenarioType.WAREHOUSE,
                    difficulty=ScenarioDifficulty.MEDIUM,
                    num_nodes=len(nodes),
                    num_edges=len(edges),
                    num_agvs=len(agvs),
                    num_tasks=len(tasks),
                    tags=["load_test"],
                )
            )

            runner = ScenarioRunner()
            report = runner.run_comparison(scenario, algorithm_names=[algo_name])

            elapsed = (time.perf_counter() - t_start) * 1000
            
            # 提取结果
            if algo_name in report.results:
                card = report.results[algo_name]
                result.compute_time_ms = elapsed
                result.success = card.error_info == "" if hasattr(card, 'error_info') else True
                result.assignments_count = len(card.algorithm_name)  # placeholder
                result.error_message = getattr(card, 'error_info', '')
                
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            result.peak_memory_mb = peak / 1024 / 1024

        except Exception as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            result.compute_time_ms = elapsed
            result.success = False
            result.error_message = str(e)[:200]
            
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            result.peak_memory_mb = peak / 1024 / 1024

        return result

    def run_burst_mode(self, algorithms: List[str]) -> LoadTestReport:
        """
        Burst模式: 快速发送多批请求，测量响应时间分布
        
        对应G1目标: 100AGV稳定运行30min不崩, P99<5s
        """
        logger.info(f"[Burst] Starting {self.config.rounds} rounds × {len(algorithms)} algorithms "
                     f"({self.config.num_agvs} AGV, {self.config.num_tasks} tasks)")

        all_times = []
        
        for rnd in range(1, self.config.rounds + 1):
            # 每轮重新生成场景 (避免缓存影响)
            nodes, edges, tasks, agvs = self._generate_scenario(
                self.config.num_agvs, self.config.num_tasks, seed=42 + rnd
            )

            for algo in algorithms:
                logger.info(f"[Burst] Round {rnd}/{self.config.rounds}, Algo={algo}")
                result = self.run_single_algorithm(algo, nodes, edges, tasks, agvs, rnd)
                self.report.rounds_results.append(result)
                all_times.append(result.compute_time_ms)

                status = "OK" if result.success else "FAIL"
                logger.info(f"  [{algo}] {status}: {result.compute_time_ms:.0f}ms, "
                            f"err={result.error_message[:80] if not result.success else ''}")

        # 计算汇总指标
        self._compute_summary(all_times)
        return self.report

    def run_sustained_mode(self, algorithms: List[str]) -> LoadTestReport:
        """
        Sustained模式: 持续发送请求指定时长
        
        用于验证G1目标: 100AGV稳定运行30min不崩溃
        """
        logger.info(f"[Sustained] Running {self.config.duration_seconds}s with {self.config.num_agvs} AGV")

        start_time = time.time()
        batch_interval = 2.0  # 每2秒发一批
        batch_num = 0
        crash_detected = False
        all_times = []

        while time.time() - start_time < self.config.duration_seconds and not crash_detected:
            batch_num += 1
            batch_seed = 42 + batch_num

            try:
                nodes, edges, tasks, agvs = self._generate_scenario(
                    min(self.config.num_agvs, 100),
                    min(20, self.config.num_tasks // 5),
                    seed=batch_seed,
                )

                for algo in algorithms[:2]:  # Sustained模式只跑快算法
                    result = self.run_single_algorithm(algo, nodes, edges, tasks, agvs, batch_num)
                    self.report.rounds_results.append(result)
                    all_times.append(result.compute_time_ms)
                    
                    if not result.success and "crash" in result.error_message.lower():
                        crash_detected = True
                        self.report.g1_no_crashes = False

            except Exception as e:
                logger.error(f"[Sustained] Crash at batch {batch_num}: {e}")
                crash_detected = True
                self.report.g1_no_crashes = False
                break

            # Wait until next interval
            elapsed_batch = time.time() - (start_time + (batch_num - 1) * batch_interval)
            if elapsed_batch < batch_interval:
                time.sleep(batch_interval - elapsed_batch)

        total_runtime = time.time() - start_time
        self.report.g1_stable_30min = (
            total_runtime >= 29 * 60 and 
            self.report.g1_no_crashes and 
            self.report.successful_rounds / max(len(self.report.rounds_results), 1) > 0.98
        )

        logger.info(f"[Sustained] Completed: {batch_num} batches, {total_runtime:.0f}s, "
                     f"{len(self.report.rounds_results)} runs, crashes={not self.report.g1_no_crashes}")

        self._compute_summary(all_times)
        return self.report

    def run_rampup_mode(self, algorithms: List[str]) -> LoadTestReport:
        """
        RampUp模式: 逐步增加AGV数量, 找到性能拐点
        
        从10 AGV开始, 每轮增加直到达到 rampup_max_agvs 或 P99 > 10s
        """
        logger.info(f"[RampUp] Scaling from 10 → {self.config.rampup_max_agvs} AGVs")
        
        all_times = []
        current_agvs = 10
        step = 10
        round_id = 0

        while current_agvs <= self.config.rampup_max_agvs:
            round_id += 1
            nodes, edges, tasks, agvs = self._generate_scenario(
                current_agvs, min(current_agvs * 2, 500), seed=42 + round_id
            )

            for algo in ["fcfs", "v2_mip"]:  # 只测两个代表算法
                result = self.run_single_algorithm(algo, nodes, edges, tasks, agvs, round_id)
                self.report.rounds_results.append(result)
                all_times.append(result.compute_time_ms)

                logger.info(f"[RampUp] AGVs={current_agvs:3d}, Algo={algo:15s}, "
                           f"Time={result.compute_time_ms:7.0f}ms, OK={result.success}")

            # Check if we've hit the performance wall
            recent_times = all_times[-4:] if len(all_times) >= 4 else all_times
            if len(recent_times) >= 4 and statistics.mean(recent_times) > 15000:  # 15s avg
                logger.warning(f"[RampUp] Performance wall at {current_agvs} AGVs (avg>15s), stopping")
                break

            current_agvs += step

        self._compute_summary(all_times)
        return self.report

    def _compute_summary(self, all_times: List[float]):
        """计算汇总指标"""
        self.report.total_rounds = len(self.report.rounds_results)
        self.report.successful_rounds = sum(1 for r in self.report.rounds_results if r.success)
        self.report.success_rate = self.report.successful_rounds / max(self.report.total_rounds, 1)

        times = [r.compute_time_ms for r in self.report.rounds_results]
        if times:
            self.report.avg_compute_time_ms = statistics.mean(times)
            self.report.max_compute_time_ms = max(times)
            self.report.min_compute_time_ms = min(times)
            
            sorted_t = sorted(times)
            n = len(sorted_t)
            self.report.overall_p50 = sorted_t[int(n * 0.5)] if n > 0 else 0
            self.report.overall_p95 = sorted_t[int(n * 0.95)] if n > 0 else sorted_t[-1]
            self.report.overall_p99 = sorted_t[min(int(n * 0.99), n - 1)] if n > 0 else sorted_t[-1]

        # Throughput estimate
        total_tasks = sum(r.tasks for r in self.report.rounds_results)
        total_time_s = sum(r.compute_time_ms / 1000 for r in self.report.rounds_results)
        self.report.throughput_tasks_per_sec = total_tasks / max(total_time_s, 0.001)

        # Memory
        mem_peaks = [r.peak_memory_mb for r in self.report.rounds_results if r.peak_memory_mb > 0]
        self.report.peak_memory_mb = max(mem_peaks) if mem_peaks else 0

        # G1 goal checks
        self.report.g1_p99_under_5s = self.report.overall_p99 < 5000  # 5000ms = 5s
        self.report.g1_ready = (
            self.report.g1_p99_under_5s and
            self.report.g1_no_crashes and
            self.report.success_rate >= 0.98
        )


def main():
    parser = argparse.ArgumentParser(description="AGV-TMS k6-style Load Test")
    parser.add_argument("--agvs", type=int, default=50, help="Number of AGVs (default: 50)")
    parser.add_argument("--tasks", type=int, default=100, help="Number of tasks per round (default: 100)")
    parser.add_argument("--rounds", type=int, default=5, help="Number of test rounds (default: 5)")
    parser.add_argument("--mode", choices=["burst", "sustained", "rampup"], default="burst",
                        help="Test mode (default: burst)")
    parser.add_argument("--duration", type=int, default=300, help="Duration seconds (sustained only)")
    parser.add_argument("--rampup-max", type=int, default=200, help="Max AGVs for rampup (default: 200)")
    parser.add_argument("--algorithms", nargs="+", default=None,
                        help="Algorithms to test (default: fcfs,greedy,v2_mip,v2_orchestrator)")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S"
    )

    config = LoadTestConfig(
        num_agvs=args.agvs,
        num_tasks=args.tasks,
        rounds=args.rounds,
        mode=args.mode,
        duration_seconds=args.duration,
        rampup_max_agvs=args.rampup_max,
    )

    algorithms = args.algorithms or LoadTestRunner.ALGORITHMS_TO_TEST

    os.makedirs(args.output_dir, exist_ok=True)

    runner = LoadTestRunner(config)

    t_total = time.time()

    if config.mode == "burst":
        report = runner.run_burst_mode(algorithms)
    elif config.mode == "sustained":
        report = runner.run_sustained_mode(algorithms)
    elif config.mode == "rampup":
        report = runner.run_rampup_mode(algorithms)
    else:
        raise ValueError(f"Unknown mode: {config.mode}")

    wall_time = time.time() - t_total

    # Save JSON report
    json_path = os.path.join(args.output_dir, f"load_test_{report.test_id}.json")
    with open(json_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
    logger.info(f"\nJSON report saved: {json_path}")

    # Print summary
    print("\n" + "=" * 70)
    print(f" AGV-TMS Load Test Report — {report.test_id}")
    print("=" * 70)
    print(f" Mode:       {config.mode.upper()}")
    print(f" Config:     {config.num_agvs} AGVs × {config.num_tasks} tasks × {config.rounds} rounds")
    print(f" Algorithms: {', '.join(algorithms)}")
    print(f" Wall Time:  {wall_time:.1f}s")
    print("-" * 70)
    print(f" Success Rate:   {report.success_rate:.1%} ({report.successful_rounds}/{report.total_rounds})")
    print(f" Avg Latency:    {report.avg_compute_time_ms:.0f}ms")
    print(f" Max Latency:    {report.max_compute_time_ms:.0f}ms")
    print(f" P50 / P95 / P99: {report.overall_p50:.0f} / {report.overall_p95:.0f} / {report.overall_p99:.0f}ms")
    print(f" Throughput:     {report.throughput_tasks_per_sec:.1f} tasks/s")
    print(f" Peak Memory:    {report.peak_memory_mb:.1f}MB")
    print("-" * 70)
    print(f" G1 Goals:")
    print(f"   P99 < 5s:      {'PASS' if report.g1_p99_under_5s else 'FAIL'} ({report.overall_p99/1000:.1f}s)")
    print(f"   No Crashes:    {'PASS' if report.g1_no_crashes else 'FAIL'}")
    print(f"   G1 Ready:      {'YES!' if report.g1_ready else 'NO — more work needed'}")
    print("=" * 70)

    # Save markdown report
    md_path = os.path.join(args.output_dir, "load_test_report.md")
    with open(md_path, "w") as f:
        f.write(report.to_markdown())
    logger.info(f"Markdown report saved: {md_path}")


# Monkey-patch LoadTestReport with to_markdown method
def _to_markdown(self: LoadTestReport) -> str:
    lines = [
        f"# AGV-TMS Load Test Report",
        f"",
        f"- **Test ID**: {self.test_id}",
        f"- **Time**: {self.timestamp}",
        f"- **Mode**: {self.config.get('mode', '?')}",
        f"- **Config**: {self.config.get('num_agvs')} AGVs × {self.config.get('num_tasks')} tasks",
        f"",
        f"## Summary",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Success Rate | {self.success_rate:.1%} |",
        f"| Avg Latency | {self.avg_compute_time_ms:.0f}ms |",
        f"| P50 / P95 / P99 | {self.overall_p50:.0f} / {self.overall_p95:.0f} / {self.overall_p99:.0f}ms |",
        f"| Throughput | {self.throughput_tasks_per_sec:.1f} tps |",
        f"| Peak Memory | {self.peak_memory_mb:.1f}MB |",
        f"",
        f"## G1 Goal Status",
        f"| Goal | Status |",
        f"|------|--------|",
        f"| P99 < 5s | {'PASS' if self.g1_p99_under_5s else 'FAIL'} |",
        f"| No Crashes | {'PASS' if self.g1_no_crashes else 'FAIL'} |",
        f"| G1 Demo Ready | {'YES' if self.g1_ready else 'NO'} |",
    ]
    return "\n".join(lines)

LoadTestReport.to_markdown = _to_markdown


if __name__ == "__main__":
    main()
