"""
PPO Policy Network for continuous/hybrid AGV control.

Phase 2 RL scheduler: PPO for fine-grained decisions:
- Speed selection
- Path preference (among alternatives)
- Charging decision timing
- Priority adjustment

Uses Actor-Critic architecture with GAE (Generalized Advantage Estimation).
"""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    import torch.distributions as distributions
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


# =============================================================================
# PPO Network
# =============================================================================

class ActorCritic(nn.Module):
    """Shared backbone actor-critic network."""

    def __init__(
        self,
        obs_dim: int,
        action_dim: int,
        discrete_actions: Optional[int] = None,  # If hybrid: some discrete, some continuous
        hidden_dims: Tuple[int, ...] = (256, 128),
    ):
        super().__init__()
        self.discrete_actions = discrete_actions

        # Shared feature extractor
        layers = []
        prev = obs_dim
        for h in hidden_dims:
            layers.extend([
                nn.Linear(prev, h),
                nn.LayerNorm(h),
                nn.Tanh(),
            ])
            prev = h
        self.backbone = nn.Sequential(*layers)

        # Actor head
        if discrete_actions:
            # Hybrid: discrete part + optional continuous
            self.actor_discrete = nn.Linear(prev, discrete_actions)
            log_std = -0.5 * np.ones(action_dim, dtype=np.float32)
            self.log_std = nn.Parameter(torch.tensor(log_std))
        else:
            self.actor_discrete = None
            log_std = -0.5 * np.ones(action_dim, dtype=np.float32)
            self.log_std = nn.Parameter(torch.tensor(log_std))

        # Critic head
        self.critic = nn.Sequential(nn.Linear(prev, 64), nn.Tanh(), nn.Linear(64, 1))

    def forward(
        self, x: torch.Tensor
    ) -> Tuple[
        Optional[distributions.Categorical],
        Optional[distributions.Normal],
        torch.Tensor,
    ]:
        features = self.backbone(x)
        value = self.critic(features).squeeze(-1)

        dist_disc = None
        dist_cont = None

        if self.actor_discrete is not None:
            logits = self.actor_discrete(features)
            dist_disc = distributions.Categorical(logits=logits)

        std = torch.exp(torch.clamp(self.log_std, -2, 2))
        dist_cont = distributions.Normal(
            torch.zeros(x.shape[0], self.log_std.shape[0]).to(x.device),
            std.expand(x.shape[0], -1),
        ) if self.log_std.shape[0] > 0 else None

        return dist_disc, dist_cont, value

    def get_action(
        self, x: torch.Tensor, deterministic: bool = False
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Sample actions and return (action, log_prob, value)."""
        dist_disc, dist_cont, value = self.forward(x)

        actions = []
        log_probs = []

        if dist_disc is not None:
            if deterministic:
                action_d = dist_disc.probs.argmax(dim=-1)
            else:
                action_d = dist_disc.sample()
            actions.append(action_d.float().unsqueeze(-1))
            log_probs.append(dist_disc.log_prob(action_d))

        if dist_cont is not None:
            if deterministic:
                action_c = torch.zeros_like(dist_cont.mean)
            else:
                action_c = dist_cont.sample()
            actions.append(action_c)
            log_probs.append(dist_cont.log_prob(action_c).sum(-1))

        action = torch.cat(actions, dim=-1) if actions else torch.empty(x.shape[0], 0)
        log_prob = sum(log_probs) if log_probs else torch.zeros(x.shape[0])

        return action, log_prob, value

    def evaluate(
        self, x: torch.Tensor, actions: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Evaluate given actions to get (log_prob, entropy, value, dist_disc_probs)."""
        dist_disc, dist_cont, value = self.forward(x)

        log_probs = []
        entropies = []

        idx = 0
        if dist_disc is not None:
            act_d = actions[:, idx].long()
            log_p = dist_disc.log_prob(act_d)
            log_probs.append(log_p)
            entropies.append(dist_disc.entropy())
            idx += 1

        if dist_cont is not None:
            act_c = actions[:, idx:]
            log_p = dist_cont.log_prob(act_c).sum(-1)
            log_probs.append(log_p)
            entropies.append(dist_cont.entropy())

        total_log_prob = sum(log_probs) if log_probs else torch.zeros(x.shape[0])
        total_entropy = sum(entropies) if entropies else torch.zeros(x.shape[0])

        disc_probs = None
        if dist_disc is not None:
            disc_probs = dist_disc.probs.detach()

        return total_log_prob, total_entropy, value, disc_probs


# =============================================================================
# Rollout Buffer
# =============================================================================

@dataclass
class RolloutSample:
    """Single rollout step."""
    obs: np.ndarray
    actions: np.ndarray
    old_log_probs: np.ndarray
    values: np.ndarray
    rewards: np.ndarray
    advantages: np.ndarray
    returns: np.ndarray
    dones: np.ndarray


class PPOBuffer:
    """Rollout storage for PPO."""

    def __init__(self, horizon: int, obs_dim: int, action_dim: int, device: str = "cpu"):
        self.horizon = horizon
        self.device = device
        self.ptr = 0

        self.obs = np.zeros((horizon, obs_dim), dtype=np.float32)
        self.actions = np.zeros((horizon, action_dim), dtype=np.float32)
        self.log_probs = np.zeros(horizon, dtype=np.float32)
        self.values = np.zeros(horizon, dtype=np.float32)
        self.rewards = np.zeros(horizon, dtype=np.float32)
        self.dones = np.zeros(horizon, dtype=np.bool_)
        self.advantages = np.zeros(horizon, dtype=np.float32)
        self.returns = np.zeros(horizon, dtype=np.float32)

    def push(
        self, obs, actions, log_prob, value, reward, done,
    ):
        """Store a single step."""
        idx = self.ptr % self.horizon
        self.obs[idx] = obs
        self.actions[idx] = actions
        self.log_probs[idx] = log_prob
        self.values[idx] = value
        self.rewards[idx] = reward
        self.dones[idx] = done
        self.ptr += 1

    def finish_rollout(self, last_value: float = 0.0, gamma: float = 0.99,
                       gae_lambda: float = 0.95):
        """Compute advantages using GAE after rollout completes."""
        n = min(self.ptr, self.horizon)
        gae = 0.0
        for i in reversed(range(n)):
            if i == n - 1:
                next_val = last_value
            else:
                next_val = self.values[i + 1]
            delta = self.rewards[i] + gamma * next_val * (~self.dones[i]) - self.values[i]
            gae = delta + gamma * gae_lambda * (~self.dones[i]) * gae
            self.advantages[i] = gae
            self.returns[i] = gae + self.values[i]

        # Normalize advantages
        if n > 1:
            adv_mean = self.advantages[:n].mean()
            adv_std = self.advantages[:n].std()
            if adv_std > 1e-6:
                self.advantages[:n] -= adv_mean
                self.advantages[:n] /= adv_std

    def get(self) -> RolloutSample:
        """Return all stored data up to ptr."""
        n = min(self.ptr, self.horizon)
        return RolloutSample(
            obs=self.obs[:n],
            actions=self.actions[:n],
            old_log_probs=self.log_probs[:n],
            values=self.values[:n],
            rewards=self.rewards[:n],
            advantages=self.advantages[:n],
            returns=self.returns[:n],
            dones=self.dones[:n],
        )


# =============================================================================
# PPO Agent Configuration
# =============================================================================

@dataclass
class PPOConfig:
    """PPO hyperparameters."""
    hidden_dims: Tuple[int, ...] = (256, 128)

    # PPO core
    learning_rate: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_epsilon: float = 0.2
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 0.5

    # Training
    rollout_horizon: int = 2048
    num_epochs: int = 10         # PPO epochs per rollout
    minibatch_size: int = 64

    # Evaluation
    eval_interval: int = 30
    eval_episodes: int = 5


# =============================================================================
# PPO Agent
# =============================================================================

class PPOAgent:
    """
    Proximal Policy Optimization agent for AGV control.

    Suitable for continuous/hybrid action spaces where DQN would struggle.
    """

    def __init__(self, obs_dim: int, action_dim: int,
                 discrete_action_count: Optional[int] = None,
                 config: Optional[PPOConfig] = None):
        if not HAS_TORCH:
            raise RuntimeError("PyTorch required for PPO agent")

        self.obs_dim = obs_dim
        self.action_dim = action_dim
        self.cfg = config or PPOConfig()

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.policy = ActorCritic(
            obs_dim, action_dim,
            discrete_actions=discrete_action_count,
            hidden_dims=self.cfg.hidden_dims,
        ).to(self.device)

        self.optimizer = optim.Adam(
            self.policy.parameters(), lr=self.cfg.learning_rate,
            eps=1e-5,
        )

        self.buffer = PPOBuffer(
            self.cfg.rollout_horizon, obs_dim, action_dim,
            device=str(self.device),
        )

        self.total_steps = 0
        self.episode_rewards: deque = deque(maxlen=100)

    def select_action(self, state: np.ndarray, deterministic: bool = False) -> Tuple[np.ndarray, float, float]:
        """Select action, returning (action, log_prob, value)."""
        with torch.no_grad():
            obs_t = torch.FloatTensor(state).unsqueeze(0).to(self.device)
            action, log_prob, value = self.policy.get_action(obs_t, deterministic)
            return (
                action.cpu().numpy()[0],
                log_prob.item(),
                value.item(),
            )

    def store(self, obs, action, log_prob, value, reward, done):
        """Store transition in rollout buffer."""
        self.buffer.push(obs, action, log_prob, value, reward, done)

    def update(self) -> Dict[str, float]:
        """Run PPO update on collected rollout."""
        # Finish rollout (compute GAE)
        self.buffer.finish_rollout(
            gamma=self.cfg.gamma, gae_lambda=self.cfg.gae_lambda,
        )
        data = self.buffer.get()

        obs_t = torch.FloatTensor(data.obs).to(self.device)
        act_t = torch.FloatTensor(data.actions).to(self.device)
        old_lp_t = torch.FloatTensor(data.old_log_probs).to(self.device)
        ret_t = torch.FloatTensor(data.returns).to(self.device)
        adv_t = torch.FloatTensor(data.advantages).to(self.device)

        dataset = torch.utils.data.TensorDataset(obs_t, act_t, old_lp_t, ret_t, adv_t)
        loader = torch.utils.data.DataLoader(
            dataset, batch_size=self.cfg.minibatch_size, shuffle=True,
        )

        total_loss = 0.0
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0

        for epoch in range(self.cfg.num_epochs):
            for batch in loader:
                b_obs, b_act, b_old_lp, b_ret, b_adv = [
                    x.to(self.device) for x in batch
                ]

                new_log_prob, entropy, value, _ = self.policy.evaluate(b_obs, b_act)

                ratio = torch.exp(new_log_prob - b_old_lp)
                surr1 = ratio * b_adv
                surr2 = torch.clamp(ratio, 1 - self.cfg.clip_epsilon,
                                    1 + self.cfg.clip_epsilon) * b_adv
                policy_loss = -torch.min(surr1, surr2).mean()

                value_loss = nn.MSELoss()(value.squeeze(), b_ret)

                entropy_loss = -entropy.mean()

                loss = policy_loss + self.cfg.value_coef * value_loss + self.cfg.entropy_coef * entropy_loss

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.policy.parameters(), self.cfg.max_grad_norm)
                self.optimizer.step()

                total_loss += loss.item()
                total_policy_loss += policy_loss.item()
                total_value_loss += value_loss.item()
                total_entropy += entropy.item()

        n_updates = self.cfg.num_epochs * (len(dataset) // self.cfg.minibatch_size + 1)
        return {
            "loss": total_loss / n_updates,
            "policy_loss": total_policy_loss / n_updates,
            "value_loss": total_value_loss / n_updates,
            "entropy": total_entropy / n_updates,
        }

    def train(self, env, total_timesteps: int = 100_000, **kwargs):
        """Train PPO agent."""
        logger.info(f"Starting PPO training: {total_timesteps} timesteps")
        episode = 0
        ep_reward = 0.0
        state, _ = env.reset(seed=42)

        while self.total_steps < total_timesteps:
            action, log_prob, value = self.select_action(state, training=True)

            next_state, reward, done, truncated, info = env.step(int(action[0]))

            self.store(state, action, log_prob, value, reward, done or truncated)

            self.total_steps += 1
            ep_reward += reward

            if done or truncated:
                episode += 1
                self.episode_rewards.append(ep_reward)

                # Update when rollout is full
                if self.buffer.ptr >= self.cfg.rollout_horizon:
                    metrics = self.update()
                    logger.debug(f"PPO Update ep {episode}: {metrics}")

                state, _ = env.reset()
                ep_reward = 0.0
            else:
                state = next_state

        return {"total_steps": self.total_steps, "episodes": episode}

    def save_model(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
        torch.save({'policy': self.policy.state_dict()}, path)

    def load_model(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.policy.load_state_dict(ckpt['policy'])

    @property
    def stats(self) -> dict:
        return {"device": str(self.device), "total_steps": self.total_steps}
