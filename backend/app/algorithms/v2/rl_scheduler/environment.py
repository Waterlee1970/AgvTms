"""
AGV Scheduling Gym Environment — for RL agent training.

Implements OpenAI Gym-compatible environment:
- State: fleet state + pending tasks + map features
- Action: assign task_i to AGV_j
- Reward: -makespan, -energy, +throughput, collision penalty

Supports both:
1. Discrete action space: task_id -> agv_id mapping (for DQN)
2. Multi-discrete action space (for multi-agent RL)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

try:
    import gymnasium as gym
    from gymnasium import spaces
    HAS_GYM = True
except ImportError:
    try:
        import gym
        from gym import spaces
        HAS_GYM = True
    except ImportError:
        HAS_GYM = False
        logger.warning("gym/gymnasium not installed. Install with: pip install gymnasium")


# =============================================================================
# Data Classes
# =============================================================================

class AgvState:
    """Simplified AGV state for the RL environment."""
    __slots__ = ('id', 'x', 'y', 'node_id', 'battery', 'speed',
                 'capacity', 'status', 'current_task_id')

    def __init__(self, id: str, x: float, y: float, node_id: str,
                 battery: float, speed: float, capacity: int,
                 status: str = "idle", current_task_id: Optional[str] = None):
        self.id = id; self.x = x; self.y = y; self.node_id = node_id
        self.battery = battery; self.speed = speed; self.capacity = capacity
        self.status = status; self.current_task_id = current_task_id


class TaskState:
    """Simplified task state."""
    __slots__ = ('id', 'pickup_x', 'pickup_y', 'pickup_node',
                 'dropoff_x', 'dropoff_y', 'dropoff_node',
                 'priority', 'created_time', 'deadline')

    def __init__(self, id: str, pickup_x: float, pickup_y: float,
                 pickup_node: str, dropoff_x: float, dropoff_y: float,
                 dropoff_node: str, priority: int = 5,
                 created_time: float = 0, deadline: Optional[float] = None):
        self.id = id; self.pickup_x = pickup_x; self.pickup_y = pickup_y
        self.pickup_node = pickup_node
        self.dropoff_x = dropoff_x; self.dropoff_y = dropoff_y
        self.dropoff_node = dropoff_node
        self.priority = priority; self.created_time = created_time; self.deadline = deadline


# =============================================================================
# Environment Configuration
# =============================================================================

@dataclass
class AgvEnvConfig:
    """Configuration for the AGV scheduling environment."""
    max_agvs: int = 20          # Max AGVs in simulation
    max_tasks: int = 50         # Max pending tasks
    map_size: Tuple[int, int] = (100, 100)  # Map dimensions (meters)
    max_steps_per_episode: int = 200
    time_step_seconds: float = 5.0   # Each env step = 5s of real time
    # Reward weights
    reward_makespan_weight: float = -0.01
    reward_completion_weight: float = 10.0
    reward_distance_weight: float = -0.001
    reward_idle_penalty: float = -0.05
    reward_collision_penalty: float = -50.0
    reward_battery_bonus: float = 0.02
    # Observation normalization
    normalize_obs: bool = True


# =============================================================================
# Main Environment Class
# =============================================================================

class AgvSchedulingEnv(gym.Env if HAS_GYM else object):
    """
    AGV Fleet Scheduling Environment for Reinforcement Learning.

    State Space (observation):
        - AGV features: [x, y, battery, speed, capacity, is_busy, current_task_progress]
        - Task features: [pickup_x, pickup_y, dropoff_x, dropoff_y, priority, time_since_creation]
        - Global: [time, num_idle_agvs, num_pending_tasks, avg_congestion]

    Action Space (discrete):
        For each task, choose which AGV to assign it to.
        action = task_idx * num_agvs + agv_idx
        Special: action = num_tasks * num_agvs means "skip/delay this task"

    Reward:
        r = w_complete * completed - w_makespan * makespan
            - w_dist * total_distance - w_idle * idle_time
            + w_battery * avg_battery
    """

    metadata = {'render_modes': ['human', 'rgb_array']}

    def __init__(self, config: Optional[AgvEnvConfig] = None):
        if not HAS_GYM:
            raise RuntimeError(
                "Requires gym or gymnasium. Run: pip install gymnasium"
            )
        self.cfg = config or AgvEnvConfig()

        # Will be set on reset()
        self._agvs: List[AgvState] = []
        self._tasks: List[TaskState] = []
        self._completed_tasks: List[str] = []
        self._assignments: Dict[str, str] = {}       # task_id -> agv_id
        self._distance_matrix: Dict[Tuple[str, str], float] = {}
        self._node_positions: Dict[str, Tuple[float, float]] = {}

        self._step_count: int = 0
        self._episode_time: float = 0.0
        self._total_distance: float = 0.0
        self._collision_count: int = 0

        # Define spaces (lazy — actual sizes depend on reset)
        self.action_space = None   # Set in _build_spaces
        self.observation_space = None

        # Rendering
        self._render_mode = None
        self._fig = None
        self._ax = None

    def _build_spaces(self) -> None:
        """Build action and observation spaces based on current config."""
        # Use FIXED dimensions based on config (not current episode state)
        # This ensures action_dim stays constant across episodes for DQN
        n_agv = self.cfg.max_agvs   # Fixed: not len(self._agvs)
        n_task = self.cfg.max_tasks  # Fixed: not len(self._tasks)

        # Action space: for each of max_tasks tasks, assign one of max_agvs AGVs (or skip)
        # Total actions = max_tasks * max_agvs + 1 (skip all)
        # Extra actions are masked out via get_action_mask()
        n_actions = n_task * n_agv + 1
        self.action_space = spaces.Discrete(n_actions)

        # Observation space
        agv_feat_dim = 7      # x, y, battery, speed, capacity, is_busy, progress
        task_feat_dim = 6     # px, py, dx, dy, priority, age
        global_feat_dim = 4   # time, idle_agv, pending, congestion

        obs_dim = self.cfg.max_agvs * agv_feat_dim + \
                  self.cfg.max_tasks * task_feat_dim + global_feat_dim

        low = np.zeros(obs_dim, dtype=np.float32)
        high = np.ones(obs_dim, dtype=np.float32)
        high[:self.cfg.max_agvs * agv_feat_dim + self.cfg.max_tasks * task_feat_dim] = 100.0
        high[self.cfg.max_agvs * agv_feat_dim + self.cfg.max_tasks * task_feat_dim:] = 10000.0

        self.observation_space = spaces.Box(low=low, high=high, dtype=np.float32)

    def seed(self, seed: int = None) -> List[int]:
        """Set random seed."""
        rng = np.random.RandomState(seed)
        seeds = [rng.randint(0, 2**31 - 1)]
        return seeds

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict] = None,
    ) -> Tuple[np.ndarray, Dict]:
        """Reset the environment for a new episode."""
        super().reset(seed=seed)

        rng = np.random.default_rng(seed)

        # Generate random AGVs
        n_agv = rng.integers(3, min(self.cfg.max_agvs + 1, 21))
        self._agvs = []
        for i in range(n_agv):
            node = f"N{i:04d}"
            x = rng.uniform(0, self.cfg.map_size[0])
            y = rng.uniform(0, self.cfg.map_size[1])
            self._agvs.append(AgvState(
                id=f"AGV{i}", x=x, y=y, node_id=node,
                battery=rng.uniform(60, 100),
                speed=rng.uniform(1.0, 2.0),
                capacity=rng.choice([1, 2]),
                status="idle",
            ))
            self._node_positions[node] = (x, y)

        # Generate random tasks
        n_task = rng.integers(3, min(self.cfg.max_tasks + 1, 31))
        self._tasks = []
        for j in range(n_task):
            pick_node = f"P{j:04d}"
            drop_node = f"D{j:04d}"
            px = rng.uniform(0, self.cfg.map_size[0])
            py = rng.uniform(0, self.cfg.map_size[1])
            dx = rng.uniform(0, self.cfg.map_size[0])
            dy = rng.uniform(0, self.cfg.map_size[1])
            self._tasks.append(TaskState(
                id=f"T{j}", pickup_x=px, pickup_y=py, pickup_node=pick_node,
                dropoff_x=dx, dropoff_y=dy, dropoff_node=drop_node,
                priority=rng.integers(1, 11),
                created_time=0.0,
            ))
            self._node_positions[pick_node] = (px, py)
            self._node_positions[drop_node] = (dx, dy)

        # Build distance matrix (Euclidean)
        self._distance_matrix.clear()
        all_nodes = list(self._node_positions.keys())
        for na in all_nodes:
            for nb in all_nodes:
                pa = self._node_positions[na]
                pb = self._node_positions[nb]
                dist = ((pa[0]-pb[0])**2 + (pa[1]-pb[1])**2)**0.5
                self._distance_matrix[(na, nb)] = dist

        # Reset episode state
        self._step_count = 0
        self._episode_time = 0.0
        self._total_distance = 0.0
        self._collision_count = 0
        self._completed_tasks = []
        self._assignments = {}

        # Build spaces now that we know sizes
        self._build_spaces()

        obs = self._get_observation()
        info = {
            "num_agvs": n_agv,
            "num_tasks": n_task,
            "distance_matrix_size": len(self._distance_matrix),
        }
        return obs, info

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        """
        Execute one step.

        Decodes action into task->AGV assignments, simulates execution,
        computes reward.
        """
        self._step_count += 1
        self._episode_time += self.cfg.time_step_seconds

        n_agv_max = self.cfg.max_agvs    # Fixed grid width for decoding
        n_task_max = self.cfg.max_tasks   # Fixed grid height for decoding
        n_agv_actual = len(self._agvs)
        n_task_actual = len(self._tasks)

        # Decode action using FIXED grid dimensions
        newly_assigned = 0
        assigned_distance = 0.0
        collisions = 0

        skip_action = n_task_max * n_agv_max  # The "skip all" action index

        if action < skip_action:  # Not the skip action
            task_idx = action // n_agv_max
            agv_idx = action % n_agv_max

            if task_idx < n_task_actual and agv_idx < n_agv_actual:
                task = self._tasks[task_idx]
                agv = self._agvs[agv_idx]
                if task.id not in self._assignments:
                    # Assign
                    self._assignments[task.id] = agv.id
                    newly_assigned += 1

                    # Compute distance
                    d = self._distance_matrix.get(
                        (agv.node_id, task.pickup_node), 50.0
                    ) + self._distance_matrix.get(
                        (task.pickup_node, task.dropoff_node), 50.0
                    )
                    assigned_distance += d
                    self._total_distance += d

                    # Simulate completion (probabilistic based on distance/speed)
                    completion_prob = min(1.0, self.cfg.time_step_seconds / max(d / agv.speed, 0.01))
                    if np.random.random() < completion_prob:
                        self._completed_tasks.append(task.id)
                        agv.status = "idle"
                        agv.current_task_id = None
                    else:
                        agv.status = "busy"
                        agv.current_task_id = task.id

        # Check for collisions (simple proximity check)
        busy_agvs = [(a.x, a.y) for a in self._agvs if a.status == "busy"]
        for i in range(len(busy_agvs)):
            for j in range(i+1, len(busy_agvs)):
                d = ((busy_agvs[i][0]-busy_agvs[j][0])**2 +
                     (busy_agvs[i][1]-busy_agvs[j][1])**2)**0.5
                if d < 2.0:  # Within 2 meters
                    collisions += 1
        self._collision_count += collisions

        # --- Compute reward ---
        reward = 0.0

        # Completion bonus
        reward += self.cfg.reward_completion_weight * newly_assigned

        # Distance cost
        reward += self.cfg.reward_distance_weight * assigned_distance

        # Collision penalty
        reward += self.cfg.reward_collision_penalty * collisions

        # Idle penalty
        n_idle = sum(1 for a in self._agvs if a.status == "idle")
        reward += self.cfg.reward_idle_penalty * n_idle

        # Battery bonus (prefer higher battery AGVs being used)
        avg_batt = np.mean([a.battery for a in self._agvs]) if self._agvs else 50
        reward += self.cfg.reward_battery_bonus * avg_batt

        # Makespan proxy (penalize long episodes)
        reward += self.cfg.reward_makespan_weight * self._episode_time

        # --- Terminal conditions ---
        terminated = False
        truncated = False
        n_task_actual = len(self._tasks)  # Use actual task count

        # All tasks assigned
        if len(self._assignments) >= n_task_actual:
            # Wait a few steps for completions
            if len(self._completed_tasks) >= n_task_actual * 0.9:
                terminated = True

        # Max steps
        if self._step_count >= self.cfg.max_steps_per_episode:
            truncated = True

        # Too many collisions
        if self._collision_count > 10:
            truncated = True

        obs = self._get_observation()

        info = {
            "newly_assigned": newly_assigned,
            "assigned_distance": assigned_distance,
            "collisions": collisions,
            "completed": len(self._completed_tasks),
            "total_completed": len(self._completed_tasks),
            "idle_agvs": n_idle,
            "avg_battery": round(avg_batt, 1),
        }

        return obs, reward, terminated, truncated, info

    def _get_observation(self) -> np.ndarray:
        """Build normalized observation vector."""
        obs_parts = []

        # AGV features (padded to max_agvs)
        for agv in self._agvs[:self.cfg.max_agvs]:
            progress = 0.5 if agv.status == "busy" else 0.0
            is_busy = 1.0 if agv.status == "busy" else 0.0
            obs_parts.extend([
                agv.x / self.cfg.map_size[0],
                agv.y / self.cfg.map_size[1],
                agv.battery / 100.0,
                agv.speed / 3.0,
                agv.capacity / 3.0,
                is_busy,
                progress,
            ])
        # Pad remaining
        pad_len = (self.cfg.max_agvs - len(self._agvs)) * 7
        obs_parts.extend([0.0] * pad_len)

        # Task features (padded to max_tasks)
        for task in self._tasks[:self.cfg.max_tasks]:
            age = self._episode_time - task.created_time
            obs_parts.extend([
                task.pickup_x / self.cfg.map_size[0],
                task.pickup_y / self.cfg.map_size[1],
                task.dropoff_x / self.cfg.map_size[0],
                task.dropoff_y / self.cfg.map_size[1],
                task.priority / 10.0,
                min(age / 300.0, 1.0),  # Normalize age to 5min window
            ])
        pad_len = (self.cfg.max_tasks - len(self._tasks)) * 6
        obs_parts.extend([0.0] * pad_len)

        # Global features
        n_idle = sum(1 for a in self._agvs if a.status == "idle")
        obs_parts.extend([
            self._episode_time / 3600.0,  # Hours
            n_idle / max(self.cfg.max_agvs, 1),
            len(self._tasks) / max(self.cfg.max_tasks, 1),
            min(self._collision_count / 10.0, 1.0),  # Normalized collision count
        ])

        return np.array(obs_parts, dtype=np.float32)

    def render(self, mode='human'):
        """Render the environment state (matplotlib)."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            logger.info("matplotlib not installed, skipping render")
            return

        if not hasattr(self, '_fig') or self._fig is None:
            self._fig, self._ax = plt.subplots(figsize=(10, 8))

        self._ax.clear()

        # Draw AGVs
        colors = {'idle': 'green', 'busy': 'red'}
        for agv in self._agvs:
            color = colors.get(agv.status, 'gray')
            self._ax.scatter(agv.x, agv.y, c=color, s=150, marker='>',
                            edgecolors='black')
            self._ax.text(agv.x, agv.y + 2, f'{agv.id}\n{agv.battery:.0f}%',
                         ha='center', fontsize=7)

        # Draw tasks
        for task in self._tasks:
            assigned = task.id in self._assignments
            color = 'blue' if assigned else 'orange'
            marker = '*' if assigned else 'o'
            self._ax.scatter(task.pickup_x, task.pickup_y, c=color, s=80,
                           marker=marker, alpha=0.6)
            self._ax.scatter(task.dropoff_x, task.dropoff_y, c='purple',
                           s=60, marker='s', alpha=0.4)
            self._ax.annotate('', xy=(task.dropoff_x, task.dropoff_y),
                             xytext=(task.pickup_x, task.pickup_y),
                             arrowprops=dict(arrowstyle='->', color='gray',
                                           lw=0.5, alpha=0.4))

        self._ax.set_xlim(-5, self.cfg.map_size[0] + 5)
        self._ax.set_ylim(-5, self.cfg.map_size[1] + 5)
        self._ax.set_title(
            f'AGV Scheduling Env | Step={self._step_count} | '
            f'Time={self._episode_time:.0f}s | '
            f'Completed={len(self._completed_tasks)}/{len(self._tasks)} | '
            f'Collisions={self._collision_count}'
        )
        self._ax.set_xlabel('X (m)')
        self._ax.set_ylabel('Y (m)')
        self._ax.grid(True, alpha=0.3)
        self._ax.set_aspect('equal')
        plt.pause(0.1)

    def close(self):
        """Clean up rendering resources."""
        if hasattr(self, '_fig') and self._fig is not None:
            plt.close(self._fig)
            self._fig = None
            self._ax = None

    def get_action_mask(self) -> np.ndarray:
        """
        Return boolean mask of valid actions.

        Uses FIXED grid: max_tasks * max_agvs + 1 actions.
        Invalid actions are masked out:
          - Tasks beyond current count (t_idx >= len(tasks))
          - AGVs beyond current count (a_idx >= len(agvs))
          - Already-assigned tasks
          - Busy AGVs with no capacity
        """
        n_agv_max = self.cfg.max_agvs   # Fixed grid width
        n_task_max = self.cfg.max_tasks  # Fixed grid height

        mask = np.ones(n_task_max * n_agv_max + 1, dtype=bool)  # +1 for skip action

        n_agv_actual = len(self._agvs)
        n_task_actual = len(self._tasks)

        # Mask out non-existent task slots (tasks beyond actual count)
        if n_task_actual < n_task_max:
            start_blocked = n_task_actual * n_agv_max
            mask[start_blocked:] = False  # Block all actions for non-existent tasks

        # Mask out non-existent AGV slots within valid task rows
        if n_agv_actual < n_agv_max and n_task_actual <= n_task_max:
            for t_idx in range(min(n_task_actual, n_task_max)):
                mask[t_idx * n_agv_max + n_agv_actual : t_idx * n_agv_max + n_agv_max] = False

        # Mask already-assigned tasks
        for t_idx, task in enumerate(self._tasks):
            if task.id in self._assignments:
                start = t_idx * n_agv_max
                mask[start:start + min(n_agv_actual, n_agv_max)] = False

            # Mask busy AGVs with no capacity
            for a_idx, agv in enumerate(self._agvs):
                if a_idx >= n_agv_max:
                    break
                if agv.status == "busy" and agv.capacity <= 0:
                    action_idx = t_idx * n_agv_max + a_idx
                    if action_idx < len(mask):
                        mask[action_idx] = False

        return mask
