"""
DQN Agent for AGV Task Assignment.

Architecture:
- Double DQN with target network
- Experience replay buffer (prioritized experience replay optional)
- Epsilon-greedy exploration with decay
- Supports discrete action space from AgvSchedulingEnv

Training loop integrated — call train() to run episodes.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    logger.warning("PyTorch not available. Install: pip install torch")


# =============================================================================
# Neural Network Architecture
# =============================================================================

class DQNNetwork(nn.Module):
    """Dueling-style DQN network for AGV scheduling."""

    def __init__(
        self,
        obs_dim: int,
        action_dim: int,
        hidden_dims: Tuple[int, ...] = (256, 256, 128),
    ):
        super().__init__()

        layers = []
        prev = obs_dim
        for h in hidden_dims:
            layers.extend([
                nn.Linear(prev, h),
                nn.LayerNorm(h),
                nn.ReLU(),
            ])
            prev = h

        self.feature_extractor = nn.Sequential(*layers)

        # Dueling architecture
        self.value_stream = nn.Sequential(
            nn.Linear(prev, 128),
            nn.ReLU(),
            nn.Linear(128, 1),
        )
        self.advantage_stream = nn.Sequential(
            nn.Linear(prev, 128),
            nn.ReLU(),
            nn.Linear(prev, action_dim),
        )

        # Initialize last layer small
        for m in self.advantage_stream[-1].modules():
            if isinstance(m, nn.Linear):
                nn.init.uniform_(m.weight, -0.03, 0.03)
                nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.feature_extractor(x)
        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        q_values = value + advantage - advantage.mean(dim=-1, keepdim=True)
        return q_values


# =============================================================================
# Replay Buffer
# =============================================================================

@dataclass
class Transition:
    """Single transition tuple."""
    state: np.ndarray
    action: int
    reward: float
    next_state: np.ndarray
    done: bool
    truncated: bool
    action_mask: Optional[np.ndarray] = None
    legal_actions: Optional[np.ndarray] = None


class ReplayBuffer:
    """Uniform experience replay buffer."""

    def __init__(self, capacity: int = 50000):
        self.buffer: List[Transition] = []
        self.capacity = capacity
        self.position = 0

    def push(self, transition: Transition) -> None:
        """Add a transition to buffer."""
        if len(self.buffer) < self.capacity:
            self.buffer.append(transition)
        else:
            self.buffer[self.position] = transition
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int) -> List[Transition]:
        """Sample a batch uniformly."""
        indices = np.random.choice(len(self.buffer),
                                   size=min(batch_size, len(self.buffer)),
                                   replace=False)
        return [self.buffer[i] for i in indices]

    def __len__(self) -> int:
        return len(self.buffer)


# =============================================================================
# DQN Agent Configuration & Agent
# =============================================================================

@dataclass
class DQNConfig:
    """Configuration for DQN training."""
    # Network
    hidden_dims: Tuple[int, ...] = (256, 256, 128)

    # Training hyperparameters
    learning_rate: float = 3e-4
    gamma: float = 0.99              # Discount factor
    epsilon_start: float = 1.0       # Initial exploration rate
    epsilon_end: float = 0.05        # Final exploration rate
    epsilon_decay_steps: int = 50000  # Steps over which to decay epsilon

    # Replay buffer
    buffer_size: int = 80000
    batch_size: int = 64

    # Training control
    target_update_freq: int = 500    # Steps between target network updates
    train_freq: int = 4              # Learn every N env steps
    gradient_clip: float = 10.0

    # Evaluation
    eval_interval: int = 20          # Evaluate every N episodes
    eval_episodes: int = 5           # Number of eval episodes per evaluation


class DQNAgent:
    """
    Deep Q-Network agent for AGV task assignment.

    Usage:
        agent = DQNAgent(obs_dim=XXX, action_dim=YYY)
        metrics = agent.train(env, total_timesteps=100_000)
        agent.save_model("dqn_agv.pt")
    """

    def __init__(self, obs_dim: int, action_dim: int, config: Optional[DQNConfig] = None):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch required for DQN agent")
        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.cfg = config or DQNConfig()

        self.device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )

        # Networks
        self.q_network = DQNNetwork(
            obs_dim, action_dim, self.cfg.hidden_dims
        ).to(self.device)
        self.target_network = DQNNetwork(
            obs_dim, action_dim, self.cfg.hidden_dims
        ).to(self.device)
        self.target_network.load_state_dict(self.q_network.state_dict())
        self.target_network.eval()

        # Optimizer
        self.optimizer = optim.AdamW(
            self.q_network.parameters(), lr=self.cfg.learning_rate,
            weight_decay=1e-5,
        )

        # Replay buffer
        self.replay_buffer = ReplayBuffer(self.cfg.buffer_size)

        # Training state
        self.total_steps = 0
        self.epsilon = self.cfg.epsilon_start
        self.episode_rewards: List[float] = []
        self.eval_rewards: List[float] = []

    @property
    def eps_threshold(self) -> float:
        """Compute current epsilon based on linear decay."""
        return max(
            self.cfg.epsilon_end,
            self.cfg.epsilon_start -
            (self.cfg.epsilon_start - self.cfg.epsilon_end) *
            min(self.total_steps / self.cfg.epsilon_decay_steps, 1.0),
        )

    def select_action(
        self,
        state: np.ndarray,
        action_mask: Optional[np.ndarray] = None,
        training: bool = True,
    ) -> int:
        """Select action using epsilon-greedy policy with action masking."""
        if training and np.random.random() < self.eps_threshold:
            # Random action from valid ones
            if action_mask is not None:
                valid_indices = np.where(action_mask)[0]
                if len(valid_indices) == 0:
                    # All actions masked — return skip action (last one)
                    return self.action_dim - 1
                return int(np.random.choice(valid_indices))
            return int(np.random.randint(self.action_dim))

        # Greedy action
        with torch.no_grad():
            state_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            q_values = self.q_network(state_t).cpu().squeeze(0).numpy()

        if action_mask is not None:
            q_values = np.where(action_mask, q_values, -np.inf)

        return int(np.argmax(q_values))

    def push_transition(self, transition: Transition) -> None:
        """Store a transition in the replay buffer."""
        self.replay_buffer.push(transition)

    def update(self) -> Optional[float]:
        """Perform one gradient update step."""
        if len(self.replay_buffer) < self.cfg.batch_size:
            return None

        transitions = self.replay_buffer.sample(self.cfg.batch_size)

        # Batch tensors
        states = torch.FloatTensor(
            np.array([t.state for t in transitions])
        ).to(self.device)
        actions = torch.LongTensor(
            [t.action for t in transitions]
        ).to(self.device)
        rewards = torch.FloatTensor(
            [t.reward for t in transitions]
        ).to(self.device)
        next_states = torch.FloatTensor(
            np.array([t.next_state for t in transitions])
        ).to(self.device)
        dones = torch.BoolTensor(
            [t.done or t.truncated for t in transitions]
        ).to(self.device)

        # Current Q values
        q_values = self.q_network(states).gather(1, actions.unsqueeze(1)).squeeze(1)

        # Target Q values (Double DQN)
        with torch.no_grad():
            next_actions = self.q_network(next_states).argmax(dim=1)
            next_q_targets = self.target_network(next_states).gather(
                1, next_actions.unsqueeze(1)
            ).squeeze(1)
            target_q = rewards + self.cfg.gamma * next_q_targets * (~dones)

        # Loss (Huber loss)
        loss = nn.SmoothL1Loss()(q_values, target_q)

        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q_network.parameters(), self.cfg.gradient_clip)
        self.optimizer.step()

        # Update target network periodically
        if self.total_steps % self.cfg.target_update_freq == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())

        return loss.item()

    def train(
        self,
        env,
        total_timesteps: int = 100_000,
        callback: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """
        Train the DQN agent.

        Args:
            env: AgvSchedulingEnv instance
            total_timesteps: Maximum number of environment steps
            callback: Optional callback after each episode

        Returns:
            Training metrics dictionary
        """
        logger.info(f"Starting DQN training: {total_timesteps} timesteps")

        episode = 0
        episode_reward = 0.0
        episode_length = 0
        best_eval_reward = -float('inf')

        state, info = env.reset(seed=42)

        while self.total_steps < total_timesteps:
            # Get valid action mask
            action_mask = env.get_action_mask() if hasattr(env, 'get_action_mask') else None

            # Select action
            action = self.select_action(state, action_mask=action_mask, training=True)

            # Environment step
            next_state, reward, done, truncated, info = env.step(action)

            # Store transition
            self.push_transition(Transition(
                state=state, action=action, reward=reward,
                next_state=next_state, done=done, truncated=truncated,
                action_mask=action_mask,
            ))

            # Update
            self.total_steps += 1
            episode_reward += reward
            episode_length += 1

            if self.total_steps % self.cfg.train_freq == 0:
                loss = self.update()

            state = next_state

            if done or truncated:
                episode += 1
                self.episode_rewards.append(episode_reward)

                log_msg = (
                    f"Ep {episode:4d} | "
                    f"reward={episode_reward:8.1f} | "
                    f"len={episode_length:4d} | "
                    f"eps={self.eps_threshold:.3f}"
                )
                if self.total_steps % self.cfg.train_freq == 0 and loss is not None:
                    log_msg += f" | loss={loss:.4f}"

                logger.debug(log_msg)

                if callback:
                    callback({
                        "episode": episode, "reward": episode_reward,
                        "length": episode_length, "epsilon": self.eps_threshold,
                        "info": info,
                    })

                # Periodic evaluation
                if episode > 0 and episode % self.cfg.eval_interval == 0:
                    eval_r = self.evaluate(env, self.cfg.eval_episodes)
                    self.eval_rewards.append(eval_r)
                    if eval_r > best_eval_reward:
                        best_eval_reward = eval_r
                    logger.info(
                        f"Evaluation ep {episode}: mean reward = {eval_r:.1f}, "
                        f"best = {best_eval_reward:.1f}"
                    )

                state, _ = env.reset()
                episode_reward = 0.0
                episode_length = 0

        return {
            "total_steps": self.total_steps,
            "episodes": episode,
            "mean_final_reward": (
                np.mean(self.episode_rewards[-100:]) if self.episode_rewards else 0
            ),
            "best_eval_reward": best_eval_reward,
            "final_epsilon": self.eps_threshold,
        }

    def evaluate(self, env, num_episodes: int = 10) -> float:
        """Evaluate the agent without exploration."""
        rewards = []
        for _ in range(num_episodes):
            state, _ = env.reset()
            done = False
            ep_reward = 0.0
            while not done:
                action_mask = env.get_action_mask() if hasattr(env, 'get_action_mask') else None
                action = self.select_action(state, action_mask=action_mask, training=False)
                state, reward, done, _, _ = env.step(action)
                ep_reward += reward
            rewards.append(ep_reward)
        return float(np.mean(rewards))

    def save_model(self, path: str) -> None:
        """Save model checkpoint."""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
        torch.save({
            'q_network': self.q_network.state_dict(),
            'target_network': self.target_network.state_dict(),
            'optimizer': self.optimizer.state_dict(),
            'total_steps': self.total_steps,
            'epsilon': self.epsilon,
        }, path)
        logger.info(f"Model saved to {path}")

    def load_model(self, path: str) -> None:
        """Load model checkpoint."""
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)
        self.q_network.load_state_dict(checkpoint['q_network'])
        self.target_network.load_state_dict(checkpoint['target_network'])
        self.optimizer.load_state_dict(checkpoint['optimizer'])
        self.total_steps = checkpoint.get('total_steps', 0)
        self.epsilon = checkpoint.get('epsilon', self.cfg.epsilon_end)
        logger.info(f"Model loaded from {path}")

    @property
    def stats(self) -> dict:
        return {
            "device": str(self.device),
            "total_steps": self.total_steps,
            "epsilon": round(self.eps_threshold, 4),
            "buffer_size": len(self.replay_buffer),
            "params_total": sum(p.numel() for p in self.q_network.parameters()),
            "trainable_params": sum(p.numel() for p in self.q_network.parameters() if p.requires_grad),
        }
