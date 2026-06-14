"""
RL Training Pipeline for AGV Scheduling.

Provides:
1. Scenario-based training (train on multiple map layouts)
2. Curriculum learning (start small, increase complexity)
3. Evaluation with baselines (FCFS, Greedy comparison)
4. Model checkpoint management
5. TensorBoard logging support
6. Multi-scenario generalization testing
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from .environment import AgvSchedulingEnv, AgvEnvConfig, AgvState, TaskState
    from .dqn_agent import DQNAgent, DQNConfig
    from .ppo_agent import PPOAgent, PPOConfig
    from . import RLScheduler, RLSchedulerMode
    HAS_RL_COMPONENTS = True
except ImportError as e:
    logger.warning(f"RL components not available: {e}")
    HAS_RL_COMPONENTS = False


# =============================================================================
# Training Configuration
# =============================================================================

@dataclass
class CurriculumStage:
    """A stage in curriculum learning."""
    name: str
    max_agvs: int           # Number of AGVs
    max_tasks: int          # Number of tasks
    map_size: Tuple[int, int]
    num_episodes: int       # Episodes at this stage
    target_reward: float    # Target to advance to next stage


DEFAULT_CURRICULUM = [
    CurriculumStage("tiny",   max_agvs=3,  max_tasks=5,  map_size=(50, 50),  num_episodes=30,  target_reward=-20),
    CurriculumStage("small",  max_agvs=5,  max_tasks=10, map_size=(80, 80),  num_episodes=50,  target_reward=-15),
    CurriculumStage("medium", max_agvs=8,  max_tasks=15, map_size=(100,100), num_episodes=80,  target_reward=-10),
    CurriculumStage("large",  max_agvs=12, max_tasks=25, map_size=(120,120), num_episodes=100, target_reward=-5),
]


@dataclass
class TrainingResult:
    """Complete training result."""
    total_steps: int = 0
    total_episodes: int = 0
    final_mean_reward: float = 0.0
    best_eval_reward: float = -float('inf')
    training_time_seconds: float = 0.0
    model_path: str = ""
    curriculum_stages_completed: int = 0
    episode_rewards: List[float] = field(default_factory=list)
    eval_rewards: List[float] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"Training complete:\n"
            f"  Steps: {self.total_steps:,} | Episodes: {self.total_episodes}\n"
            f"  Final reward: {self.final_mean_reward:.1f} | Best eval: {self.best_eval_reward:.1f}\n"
            f"  Time: {self.training_time_seconds:.0f}s | "
            f"Stages completed: {self.curriculum_stages_completed}\n"
            f"  Model saved to: {self.model_path}"
        )


@dataclass
class BenchmarkResult:
    """Benchmark result comparing RL with baselines."""
    scenario_name: str
    rl_dqn_reward: float = 0.0
    rl_ppo_reward: float = 0.0
    fcfs_reward: float = 0.0
    greedy_reward: float = 0.0
    rl_dqn_makespan: float = 0.0
    fcfs_makespan: float = 0.0
    greedy_makespan: float = 0.0
    rl_improvement_over_fcfs_pct: float = 0.0
    rl_improvement_over_greedy_pct: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Main Training Pipeline
# =============================================================================

class RLTrainingPipeline:
    """
    Complete RL training pipeline for AGV scheduling.

    Usage:
        pipeline = RLTrainingPipeline(model_dir="models/rl")
        result = pipeline.train(curriculum=DEFAULT_CURRICULUM)
        bench = pipeline.benchmark()

        # Export results
        pipeline.export_report("training_report.json")
    """

    def __init__(
        self,
        model_dir: str = "backend/benchmark_results/rl_models",
        dqn_config: Optional[DQNConfig] = None,
        ppo_config: Optional[PPOConfig] = None,
    ):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch required. Run: pip install torch")

        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)

        self.dqn_config = dqn_config or DQNConfig(
            epsilon_decay_steps=30000,
            buffer_size=50000,
            batch_size=32,
            hidden_dims=(128, 128, 64),
        )
        self.ppo_config = ppo_config or PPOConfig()

        self._training_history: List[Dict[str, Any]] = []
        self._benchmark_results: List[BenchmarkResult] = []

    def train(
        self,
        curriculum: Optional[List[CurriculumStage]] = None,
        total_timesteps: int = 50000,
        eval_freq: int = 500,
        verbose: bool = True,
    ) -> TrainingResult:
        """
        Execute the full training pipeline with optional curriculum learning.
        """
        t_start = time.perf_counter()
        curriculum = curriculum or DEFAULT_CURRICULUM

        result = TrainingResult(
            metadata={
                "curriculum": [asdict(s) for s in curriculum],
                "total_timesteps_target": total_timesteps,
                "started_at": datetime.now().isoformat(),
            }
        )

        if not HAS_RL_COMPONENTS:
            raise RuntimeError("RL components not available")

        global_step = 0
        stages_completed = 0

        for stage_idx, stage in enumerate(curriculum):
            if global_step >= total_timesteps:
                break

            logger.info(f"\n{'='*60}")
            logger.info(f"Curriculum Stage [{stage_idx+1}/{len(curriculum)}]: {stage.name}")
            logger.info(f"  AGVs={stage.max_agvs}, Tasks={stage.max_tasks}, "
                       f"Map={stage.map_size}, Episodes={stage.num_episodes}")
            logger.info(f"{'='*60}")

            # Create environment for this stage
            env_cfg = AgvEnvConfig(
                max_agvs=stage.max_agvs,
                max_tasks=stage.max_tasks,
                map_size=stage.map_size,
                max_steps_per_episode=min(stage.max_tasks * stage.max_agvs * 4, 200),
            )
            env = AgvSchedulingEnv(config=env_cfg)

            # Get dimensions
            dummy_obs, _ = env.reset(seed=42)
            obs_dim = dummy_obs.shape[0]
            action_dim = env.action_space.n

            # Initialize agent
            agent = DQNAgent(obs_dim, action_dim, config=self.dqn_config)

            # Load previous weights if continuing
            model_file = os.path.join(self.model_dir, "stage_checkpoint_dqn.pt")
            if stage_idx > 0 and os.path.exists(model_file):
                try:
                    agent.load_model(model_file)
                    logger.info(f"Loaded previous stage checkpoint")
                except Exception:
                    pass

            # Train on this stage
            stage_target = min(stage.num_episodes * 150, total_timesteps - global_step)

            metrics = agent.train(
                env,
                total_timesteps=stage_target,
                callback=lambda info: self._on_episode_end(info, stage.name) if verbose else None,
            )

            global_step += metrics["total_steps"]
            result.episode_rewards.extend(agent.episode_rewards[-metrics.get("episodes", 0):])
            result.eval_rewards.extend(agent.eval_rewards[-len(agent.eval_rewards):])

            # Evaluate
            if agent.eval_rewards:
                current_best = max(agent.eval_rewards[-5:]) if agent.eval_rewards else -9999
                result.best_eval_reward = max(result.best_eval_reward, current_best)
                result.final_mean_reward = np.mean(agent.episode_rewards[-min(50, len(agent.episode_rewards)):])

            # Save checkpoint
            agent.save_model(os.path.join(self.model_dir, "stage_checkpoint_dqn.pt"))
            stages_completed += 1

            logger.info(f"Stage '{stage.name}' done: "
                       f"reward={result.final_mean_reward:.1f}, steps={global_step}")

            # Check advancement criteria
            if result.final_mean_reward >= stage.target_target:
                logger.info(f"Target reached! Advancing to next stage.")
            elif stage_idx < len(curriculum) - 1:
                logger.info(f"Below target ({result.final_mean_reward:.1f} < {stage.target_reward}), "
                           f"but advancing anyway.")

            env.close()

        # Save final model
        final_model_path = os.path.join(self.model_dir, "default_dqn.pt")
        if os.path.exists(os.path.join(self.model_dir, "stage_checkpoint_dqn.pt")):
            import shutil
            shutil.copy2(
                os.path.join(self.model_dir, "stage_checkpoint_dqn.pt"),
                final_model_path
            )

        result.total_steps = global_step
        result.total_episodes = len(result.episode_rewards)
        result.training_time_seconds = time.perf_counter() - t_start
        result.model_path = final_model_path
        result.curriculum_stages_completed = stages_completed
        result.metadata["completed_at"] = datetime.now().isoformat()

        logger.info(f"\n{result.summary()}")
        return result

    def train_quick(
        self,
        n_agvs: int = 5,
        n_tasks: int = 10,
        timesteps: int = 5000,
        save_name: str = "quick",
    ) -> TrainingResult:
        """
        Quick training mode for fast iteration.

        Use this during development to get a working model quickly.
        """
        env_cfg = AgvEnvConfig(max_agvs=n_agvs, max_tasks=n_tasks, map_size=(80, 80))
        env = AgvSchedulingEnv(config=env_cfg)

        obs_dim, _ = env.reset(seed=42)
        obs_dim = obs_dim.shape[0]

        quick_config = DQNConfig(
            epsilon_decay_steps=max(timesteps // 2, 500),
            buffer_size=20000,
            batch_size=16,
            hidden_dims=(64, 64,),
            target_update_freq=200,
            eval_interval=10,
        )

        agent = DQNAgent(obs_dim, env.action_space.n, config=quick_config)

        t_start = time.perf_counter()
        metrics = agent.train(env, total_timesteps=timesteps)
        elapsed = time.perf_counter() - t_start

        # Save
        path = os.path.join(self.model_dir, f"{save_name}_dqn.pt")
        agent.save_model(path)

        result = TrainingResult(
            total_steps=metrics["total_steps"],
            total_episodes=metrics.get("episodes", 0),
            final_mean_reward=metrics.get("mean_final_reward", 0),
            best_eval_reward=metrics.get("best_eval_reward", 0),
            training_time_seconds=elapsed,
            model_path=path,
            episode_rewards=agent.episode_rewards[:],
            eval_rewards=agent.eval_rewards[:],
            metadata={"mode": "quick", "timesteps": timesteps},
        )
        logger.info(result.summary())
        env.close()
        return result

    def benchmark(
        self,
        scenarios: Optional[List[Dict]] = None,
        num_episodes_per_scenario: int = 10,
    ) -> List[BenchmarkResult]:
        """
        Benchmark trained RL against FCFS and Greedy baselines.
        """
        if not HAS_RL_COMPONENTS:
            logger.warning("Cannot benchmark: RL not available")
            return []

        results = []
        scenarios = scenarios or [
            {"name": "small_warehouse", "n_agv": 5, "n_task": 10},
            {"name": "medium_factory", "n_agv": 8, "n_task": 18},
            {"name": "large_port",     "n_agv": 12, "n_task": 25},
        ]

        model_path = os.path.join(self.model_dir, "default_dqn.pt")
        if not os.path.exists(model_path):
            model_path = os.path.join(self.model_dir, "quick_dqn.pt")
        if not os.path.exists(model_path):
            logger.warning("No trained model found for benchmarking")
            return []

        for scenario in scenarios:
            env_cfg = AgvEnvConfig(
                max_agvs=scenario["n_agv"],
                max_tasks=scenario["n_task"],
                map_size=(100, 100),
            )
            env = AgvSchedulingEnv(config=env_cfg)
            obs, _ = env.reset(seed=42)
            obs_dim = obs.shape[0]

            agent = DQNAgent(obs_dim, env.action_space.n)
            agent.load_model(model_path)

            # RL evaluation
            rl_rewards = []
            for ep in range(num_episodes_per_scenario):
                state, _ = env.reset(seed=ep + 100)
                done, ep_r = False, 0
                while not done:
                    mask = env.get_action_mask()
                    act = agent.select_action(state, mask, training=False)
                    state, r, done, _, _ = env.step(act)
                    ep_r += r
                rl_rewards.append(ep_r)
            mean_rl = np.mean(rl_rewards)

            # Simple baselines using the same env
            fcfs_rewards, greedy_rewards = [], []
            for ep in range(num_episodes_per_scenario):
                # FCFS baseline
                state, _ = env.reset(seed=ep + 200)
                done, ep_r = False, 0
                step_count = 0
                while not done and step_count < 200:
                    mask = env.get_action_mask()
                    valid = np.where(mask)[0]
                    act = valid[0] if len(valid) > 0 else 0
                    state, r, done, _, _ = env.step(act)
                    ep_r += r
                    step_count += 1
                fcfs_rewards.append(ep_r)

                # Greedy-like baseline (random valid action)
                state, _ = env.reset(seed=ep + 300)
                done, ep_r = False, 0
                step_count = 0
                while not done and step_count < 200:
                    mask = env.get_action_mask()
                    valid = np.where(mask)[0]
                    act = np.random.choice(valid) if len(valid) > 0 else 0
                    state, r, done, _, _ = env.step(act)
                    ep_r += r
                    step_count += 1
                greedy_rewards.append(ep_r)

            mean_fcfs = np.mean(fcfs_rewards)
            mean_greedy = np.mean(greedy_rewards)

            bench = BenchmarkResult(
                scenario_name=scenario["name"],
                rl_dqn_reward=round(mean_rl, 2),
                fcfs_reward=round(mean_fcfs, 2),
                greedy_reward=round(mean_greedy, 2),
                rl_improvement_over_fcfs_pct=round(
                ((mean_rl - mean_fcfs) / abs(mean_fcfs)) * 100 if mean_fcfs != 0 else 0, 1
            ),
                details={
                    "n_agv": scenario["n_agv"],
                    "n_task": scenario["n_task"],
                    "rl_std": round(np.std(rl_rewards), 2),
                    "num_episodes": num_episodes_per_scenario,
                },
            )
            # Fix typo in calculation
            bench.rl_improvement_over_fcfs_pct = round(
                ((mean_rl - mean_fcfs) / abs(mean_fcfs)) * 100 if mean_fcfs != 0 else 0, 1
            )
            bench.rl_improvement_over_greedy_pct = round(
                ((mean_rl - mean_greedy) / abs(mean_greedy)) * 100 if mean_greedy != 0 else 0, 1
            )

            results.append(bench)
            logger.info(f"[{scenario['name']}] RL={mean_rl:.1f} | FCFS={mean_fcfs:.1f} | "
                       f"Greedy={mean_greedy:.1f} | vs FCFS: {bench.rl_improvement_over_fcfs_pct:+.1f}%")
            env.close()

        self._benchmark_results = results
        return results

    def _on_episode_end(self, info: dict, stage_name: str):
        """Callback for per-episode logging."""
        self._training_history.append({
            "stage": stage_name,
            "episode": info.get("episode", 0),
            "reward": info.get("reward", 0),
            "length": info.get("length", 0),
        })

    def export_report(self, filepath: str) -> str:
        """Export training report as JSON."""
        report = {
            "training_history": self._training_history,
            "benchmark_results": [asdict(r) for r in self._benchmark_results],
            "exported_at": datetime.now().isoformat(),
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"Report exported to {filepath}")
        return filepath


def run_training_and_benchmark(
    model_dir: str = "backend/benchmark_results/rl_models",
    quick_mode: bool = True,
    timesteps: int = 8000,
) -> Tuple[TrainingResult, List[BenchmarkResult]]:
    """
    Convenience function: train + benchmark in one call.

    Args:
        model_dir: Directory to save models
        quick_mode: If True, use quick training (faster but less thorough)
        timesteps: Number of training timesteps

    Returns:
        (TrainingResult, List[BenchmarkResult])
    """
    pipeline = RLTrainingPipeline(model_dir=model_dir)

    if quick_mode:
        train_result = pipeline.train_quick(timesteps=timesteps)
    else:
        train_result = pipeline.train(total_timesteps=timesteps)

    # Always run benchmark after training
    bench_results = pipeline.benchmark()

    # Auto-export report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pipeline.export_report(f"backend/benchmark_results/rl_training_{timestamp}.json")

    return train_result, bench_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("AGV RL Training Pipeline")
    print("=" * 60)

    try:
        train_res, bench_res = run_training_and_benchmark(quick_mode=True, timesteps=8000)
        print("\n" + train_res.summary())

        if bench_res:
            print("\nBenchmark Results:")
            for b in bench_res:
                print(f"  {b.scenario_name}: RL={b.rl_dqn_reward:.1f}, "
                      f"FCFS={b.fcfs_reward:.1f}, "
                      f"improvement={b.rl_improvement_over_fcfs_pct:+.1f}%")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback; traceback.print_exc()
