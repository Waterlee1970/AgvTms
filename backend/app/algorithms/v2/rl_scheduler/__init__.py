"""
RL Scheduler Module — integrates DQN and PPO agents.

Provides unified interface for the orchestrator to use RL-based dispatch:
- DQN for discrete task→AGV assignment (fast, scalable)
- PPO for continuous control decisions (speed, charging, etc.)
- Model management (save/load/evaluate)
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from .environment import AgvSchedulingEnv, AgvEnvConfig
from .dqn_agent import DQNAgent, DQNConfig
from .ppo_agent import PPOAgent, PPOConfig


class RLSchedulerMode(Enum):
    """RL scheduler operation mode."""
    DQN_ONLY = "dqn"               # Use only DQN for task assignment
    PPO_ONLY = "ppo"               # Use only PPO for continuous control
    HYBRID = "hybrid"            # DQN for assignment, PPO for control
    RULE_BASED = "rule_based"      # Fallback to heuristic rules
    INFERENCE_ONLY = "inference"   # Only use pre-trained models


class RLScheduler:
    """
    Unified RL-based scheduler interface.

    Coordinates DQN and PPO agents, manages models,
    and provides a clean API for integration with the orchestrator.
    """

    def __init__(
        self,
        mode: RLSchedulerMode = RLSchedulerMode.DQN_ONLY,
        dqn_config: Optional[DQNConfig] = None,
        ppo_config: Optional[PPOConfig] = None,
        env_config: Optional[AgvEnvConfig] = None,
        model_dir: str = "models/rl_scheduler",
    ):
        self.mode = mode
        self.model_dir = model_dir
        self.env_cfg = env_config or AgvEnvConfig()

        self._dqn_agent: Optional[DQNAgent] = None
        self._ppo_agent: Optional[PPOAgent] = None
        self._env: Optional[AgvSchedulingEnv] = None
        self._is_initialized = False
        self._has_trained_model = False

    def initialize(
        self,
        obs_dim: int = 0,
        action_dim: int = 0,
    ) -> None:
        """Initialize agents with observation/action space sizes."""
        os.makedirs(self.model_dir, exist_ok=True)

        # Create environment to determine dims
        if self._env is None:
            self._env = AgvSchedulingEnv(config=self.env_cfg)

        # Get actual dims from environment
        dummy_obs, _ = self._env.reset(seed=0)
        actual_obs_dim = dummy_obs.shape[0]
        actual_action_dim = self._env.action_space.n

        final_obs = obs_dim or actual_obs_dim
        final_action = action_dim or actual_action_dim

        if self.mode in (RLSchedulerMode.DQN_ONLY, RLSchedulerMode.HYBRID):
            self._dqn_agent = DQNAgent(final_obs, final_action, config=DQNConfig())

        if self.mode in (RLSchedulerMode.PPO_ONLY, RLSchedulerMode.HYBRID):
            # For PPO: action could be hybrid (discrete + continuous)
            self._ppo_agent = PPOAgent(final_obs, final_action, config=PPOConfig())

        self._is_initialized = True
        logger.info(
            f"RL Scheduler initialized: mode={self.mode.value}, obs_dim={final_obs}, action_dim={final_action}"
        )

    def train_dqn(self, total_timesteps: int = 100_000) -> Dict[str, Any]:
        """Train DQN agent."""
        if not self._is_initialized:
            self.initialize()
        assert self._dqn_agent and self._env
        result = self._dqn_agent.train(self._env, total_timesteps=total_timesteps)
        self._has_trained_model = True
        return result

    def train_ppo(self, total_timesteps: int = 100_000) -> Dict[str, Any]:
        """Train PPO agent."""
        if not self._is_initialized:
            self.initialize()
        assert self._ppo_agent and self._env
        result = self._ppo_agent.train(self._env, total_timesteps=total_timesteps)
        self._has_trained_model = True
        return result

    def dispatch(
        self,
        state: np.ndarray,
        action_mask: Optional[np.ndarray] = None,
    ) -> int:
        """
        Make a dispatch decision using the trained RL agent.

        Args:
            state: Current observation vector
            action_mask: Boolean mask of valid actions

        Returns:
            Selected action (integer)
        """
        if not self._is_initialized:
            raise RuntimeError("Call initialize() before dispatch()")
        if not self._has_trained_model:
            logger.warning("No trained model available, falling back to rule-based")
            if action_mask is not None:
                valid = np.where(action_mask)[0]
                return int(valid[0]) if len(valid) > 0 else 0
            return 0

        if self._dqn_agent:
            action = self._dqn_agent.select_action(
                state, action_mask=action_mask, training=False,
            )
            return action

        elif self._ppo_agent:
            action, _, _ = self._ppo_agent.select_action(state, deterministic=True)
            return int(action[0])

        return 0

    def evaluate(self, num_episodes: int = 20) -> Dict[str, float]:
        """Evaluate current model performance."""
        if self._env is None:
            self.initialize()

        results = {}
        if self._dqn_agent:
            results['dqn'] = self._dqn_agent.evaluate(self._env, num_episodes)
        if self._ppo_agent:
            results['ppo'] = self._ppo_agent.evaluate(self._env, num_episodes)
        return results

    def save_models(self, prefix: str = "model") -> None:
        """Save all trained models."""
        if self._dqn_agent:
            self._dqn_agent.save_model(os.path.join(self.model_dir, f"{prefix}_dqn.pt"))
        if self._ppo_agent:
            self._ppo_agent.save_model(os.path.join(self.model_dir, f"{prefix}_ppo.pt"))
        logger.info(f"Models saved to {self.model_dir}/")

    def load_models(self, prefix: str = "model") -> bool:
        """Load trained models from disk."""
        success = False
        dqn_path = os.path.join(self.model_dir, f"{prefix}_dqn.pt")
        ppo_path = os.path.join(self.model_dir, f"{prefix}_ppo.pt")

        if os.path.exists(dqn_path) and self._dqn_agent:
            self._dqn_agent.load_model(dqn_path)
            success = True
        if os.path.exists(ppo_path) and self._ppo_agent:
            self._ppo_agent.load_model(ppo_path)
            success = True

        if success:
            self._has_trained_model = True
            logger.info(f"Models loaded from {self.model_dir}/")
        return success

    @property
    def stats(self) -> Dict[str, Any]:
        s = {
            "mode": self.mode.value,
            "initialized": self._is_initialized,
            "has_trained_model": self._has_trained_model,
            "model_dir": self.model_dir,
            "torch_available": HAS_TORCH,
        }
        if self._dqn_agent:
            s["dqn"] = self._dqn_agent.stats
        if self._ppo_agent:
            s["ppo"] = self._ppo_agent.stats
        return s


# Fix HYBRID typo (was \"hybridid\") — done via class definition above
# Note: Enum values cannot be reassigned after creation, fixed in class def

__all__ = [
    'RLScheduler', 'RLSchedulerMode',
    'AgvSchedulingEnv', 'AgvEnvConfig',
    'DQNAgent', 'DQNConfig',
    'PPOAgent', 'PPOConfig',
]
