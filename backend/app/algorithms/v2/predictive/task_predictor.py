"""
Task Arrival Predictor: Predict future task arrivals for anticipatory scheduling.

Uses:
- Poisson process model for baseline arrival rate
- Exponential smoothing (Holt-Winters) for time-series patterns
- Time-of-day / day-of-week pattern learning
- Online update with sliding window
"""

from __future__ import annotations

import logging
import time
import math
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TaskArrivalEvent:
    """Record of a task arrival event."""
    timestamp: float       # Unix timestamp
    task_id: str
    pickup_node: str
    dropoff_node: str
    priority: int = 5


@dataclass
class ArrivalPrediction:
    """Prediction result for task arrivals."""
    time_window_start: float   # Unix timestamp of window start
    time_window_end: float     # Unix timestamp of window end
    predicted_count: float     # Expected number of arrivals (can be fractional)
    confidence: float          # Confidence score [0, 1]
    predicted_pickups: Dict[str, float] = field(default_factory=dict)  # node -> expected count
    predicted_dropoffs: Dict[str, float] = field(default_factory=dict)
    historical_avg: float = 0.0
    trend_factor: float = 1.0  # >1 means increasing trend


class TaskArrivalPredictor:
    """
    Predicts future task arrivals using a hybrid approach.

    Architecture:
    1. Poisson Process: models random arrivals with rate lambda(t)
    2. Holt-Winters: captures level + trend + seasonality
    3. Spatial Pattern: predicts pickup/dropoff hotspots
    """

    def __init__(
        self,
        history_window_seconds: float = 3600.0,  # Look back 1 hour by default
        prediction_horizon_seconds: float = 300.0,  # Predict next 5 min
        num_time_buckets: int = 12,  # Divide horizon into buckets
        smoothing_alpha: float = 0.3,  # Level smoothing
        smoothing_beta: float = 0.1,   # Trend smoothing
        smoothing_gamma: float = 0.1,  # Seasonal smoothing
        seasonality_period: int = 12,  # e.g., 12 x 30min = 6h cycle
    ):
        self.history_window = history_window_seconds
        self.prediction_horizon = prediction_horizon_seconds
        self.num_time_buckets = num_time_buckets
        self.alpha = smoothing_alpha
        self.beta = smoothing_beta
        self.gamma = smoothing_gamma
        self.season_period = seasonality_period

        # History buffer
        self._history: deque[TaskArrivalEvent] = deque(maxlen=10000)

        # Holt-Winters state
        self._hw_level: float = 0.0
        self._hw_trend: float = 0.0
        self._hw_seasonal: List[float] = [1.0] * seasonality_period
        self._hw_initialized: bool = False

        # Bucketed counts for recent history (sliding window)
        self._bucket_size = prediction_horizon_seconds / num_time_buckets
        self._bucket_counts: deque = deque(maxlen=num_time_buckets * 3)

        # Spatial pattern counters
        self._pickup_counts: Dict[str, int] = defaultdict(int)
        self._dropoff_counts: Dict[str, int] = defaultdict(int)
        self._total_arrivals: int = 0

        # Time-of-day pattern (24 hours -> 96 x 15-min slots)
        self._tod_pattern: np.ndarray = np.zeros(96)
        self._tod_counts: np.ndarray = np.zeros(96)

        self._last_update_time: float = 0.0

    def record_arrival(self, event: TaskArrivalEvent) -> None:
        """Record a new task arrival event."""
        self._history.append(event)
        self._total_arrivals += 1
        self._pickup_counts[event.pickup_node] += 1
        self._dropoff_counts[event.dropoff_node] += 1

        # Update time-of-day pattern
        dt = datetime.fromtimestamp(event.timestamp)
        slot = (dt.hour * 4) + (dt.minute // 15)  # 15-minute granularity
        if slot < 96:
            self._tod_pattern[slot] += 1
            self._tod_counts[slot] += 1

        # Update bucket count
        current_time = time.time()
        bucket_idx = int((current_time - event.timestamp) / self._bucket_size)
        while len(self._bucket_counts) <= bucket_idx:
            self._bucket_counts.append(0)
        if bucket_idx < len(self._bucket_counts):
            self._bucket_counts[bucket_idx] += 1

        # Periodic model update
        if current_time - self._last_update_time > 10.0:  # Update every 10s
            self._update_holt_winters()
            self._last_update_time = current_time

    def _update_holt_winters(self) -> None:
        """Update Holt-Winters triple exponential smoothing model."""
        if len(self._bucket_counts) < self.season_period * 2:
            return

        counts = list(self._bucket_counts)[-self.season_period * 2:]

        if not self._hw_initialized:
            # Initialize with simple average and linear regression
            n = len(counts)
            self._hw_level = sum(counts[:n // 2]) / (n // 2)
            second_half = sum(counts[n // 2:]) / (n // 2)
            self._hw_trend = (second_half - self._hw_level) / (n // 2)

            # Initial seasonal indices
            for i in range(self.season_period):
                idx = i % n
                if abs(self._hw_level) > 1e-6:
                    self._hw_seasonal[i] = counts[idx] / self._hw_level
                else:
                    self._hw_seasonal[i] = 1.0
            self._hw_initialized = True
            return

        # Incremental update with latest observation
        obs = counts[-1]
        s_idx = len(counts) % self.season_period

        prev_level = self._hw_level
        prev_trend = self._hw_trend
        seasonal_idx = s_idx % self.season_period

        # HW formulas
        new_level = self.alpha * (obs / max(self._hw_seasonal[seasonal_idx], 1e-6)) + \
                   (1 - self.alpha) * (prev_level + prev_trend)
        new_trend = self.beta * (new_level - prev_level) + (1 - self.beta) * prev_trend
        new_seasonal = self.gamma * (obs / max(new_level, 1e-6)) + \
                       (1 - self.gamma) * self._hw_seasonal[seasonal_idx]

        self._hw_level = new_level
        self._hw_trend = new_trend
        self._hw_seasonal[seasonal_idx] = new_seasonal

    def predict(
        self,
        horizon_seconds: Optional[float] = None,
        current_time: Optional[float] = None,
    ) -> ArrivalPrediction:
        """
        Predict task arrivals over the given horizon.

        Returns expected count, spatial distribution, confidence.
        """
        now = current_time or time.time()
        horizon = horizon_seconds or self.prediction_horizon

        # --- Method 1: Recent rate extrapolation ---
        recent_events = [
            e for e in self._history
            if now - e.timestamp <= self.history_window
        ]
        recent_rate = len(recent_events) / max(self.history_window, 1.0)
        base_prediction = recent_rate * horizon

        # --- Method 2: Holt-Winters forecast ---
        hw_forecast = base_prediction  # Fallback to rate-based
        if self._hw_initialized:
            steps = int(horizon / self._bucket_size)
            forecast = 0.0
            level = self._hw_level
            trend = self._hw_trend
            for step in range(steps):
                seasonal = self._hw_seasonal[step % self.season_period]
                val = (level + step * trend) * seasonal
                forecast += val
            hw_forecast = max(forecast, 0.0)

        # --- Method 3: Time-of-day adjustment ---
        tod_multiplier = 1.0
        dt = datetime.fromtimestamp(now)
        slot = (dt.hour * 4) + (dt.minute // 15)
        future_slot = ((dt.hour * 4 + dt.minute // 15) +
                       int(horizon / 900.0)) % 96
        if self._tod_counts[slot] > 5:  # Enough data
            avg_rate_now = self._tod_pattern[slot] / self._tod_counts[slot]
            if self._tod_counts[future_slot] > 5:
                avg_rate_future = self._tod_pattern[future_slot] / self._tod_counts[future_slot]
                if avg_rate_now > 1e-6:
                    tod_multiplier = avg_rate_future / avg_rate_now

        # --- Ensemble prediction ---
        final_count = 0.4 * base_prediction + 0.4 * hw_forecast + 0.2 * (base_prediction * tod_multiplier)

        # --- Confidence calculation ---
        confidence = self._calculate_confidence(len(recent_events))

        # --- Spatial prediction ---
        pickups, dropoffs = self._predict_spatial_distribution(final_count)

        # --- Trend factor ---
        trend = 1.0
        if self._hw_initialized and abs(self._hw_level) > 1e-6:
            trend = (self._hw_level + self._hw_trend) / self._hw_level

        return ArrivalPrediction(
            time_window_start=now,
            time_window_end=now + horizon,
            predicted_count=final_count,
            confidence=confidence,
            predicted_pickups=pickups,
            predicted_dropoffs=dropoffs,
            historical_avg=recent_rate * 60.0,  # per minute
            trend_factor=trend,
        )

    def _calculate_confidence(self, sample_size: int) -> float:
        """Calculate prediction confidence based on data quality."""
        # More samples = higher confidence
        sample_confidence = min(1.0, sample_size / 50.0)

        # Model initialization confidence
        model_confidence = 0.8 if self._hw_initialized else 0.5

        # Variance-based penalty
        if len(self._bucket_counts) >= 5:
            recent = list(self._bucket_counts)[-5:]
            mean = sum(recent) / len(recent)
            if mean > 1e-6:
                var = sum((x - mean) ** 2 for x in recent) / len(recent)
                cv = math.sqrt(var) / mean
                variance_confidence = max(0.3, 1.0 - min(cv, 1.0))
            else:
                variance_confidence = 0.7
        else:
            variance_confidence = 0.5

        return 0.35 * sample_confidence + 0.35 * model_confidence + 0.30 * variance_confidence

    def _predict_spatial_distribution(
        self, total_predicted: float,
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Predict which nodes will be most active."""
        pickups: Dict[str, float] = {}
        dropoffs: Dict[str, float] = {}

        if self._total_arrivals == 0:
            return pickups, dropoffs

        # Use empirical frequencies scaled by total prediction
        top_k = 20  # Top-20 nodes
        sorted_pickups = sorted(self._pickup_counts.items(), key=lambda x: -x[1])[:top_k]
        sorted_dropoffs = sorted(self._dropoff_counts.items(), key=lambda x: -x[1])[:top_k]

        total_picked = sum(c for _, c in sorted_pickups) or 1
        total_dropped = sum(c for _, c in sorted_dropoffs) or 1

        for node, count in sorted_pickups:
            pickups[node] = total_predicted * count / total_picked

        for node, count in sorted_dropoffs:
            dropoffs[node] = total_predicted * count / total_dropped

        return pickups, dropoffs

    @property
    def stats(self) -> dict:
        """Return predictor statistics."""
        return {
            "total_recorded": self._total_arrivals,
            "history_size": len(self._history),
            "model_initialized": self._hw_initialized,
            "hw_level": round(self._hw_level, 2),
            "hw_trend": round(self._hw_trend, 4),
            "unique_pickup_nodes": len(self._pickup_counts),
            "unique_dropoff_nodes": len(self._dropoff_counts),
            "arrival_rate_per_min": round(
                len([e for e in self._history
                     if time.time() - e.timestamp <= 60.0]) /
                max(60.0, 1.0), 3
            ),
        }
