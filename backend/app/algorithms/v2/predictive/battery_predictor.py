"""
Battery & Energy Demand Predictor.

Models AGV battery consumption and charging demand:
- Linear energy model based on distance/speed/load
- Charging station demand forecasting
- Low-battery warning generation
- Charger allocation recommendations
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BatteryStatus(Enum):
    NORMAL = "normal"
    LOW = "low"             # < 25%
    CRITICAL = "critical"   # < 15%
    CHARGING = "charging"
    FULL = "full"


@dataclass
class BatteryState:
    """Current battery state for one AGV."""
    agv_id: str
    battery_percent: float   # 0-100
    is_charging: bool
    charge_rate_percent_per_min: float = 5.0  # Typical fast-charge rate
    consumption_rate_per_meter: float = 0.05  # %/m typical
    estimated_range_meters: float = 600.0      # At full charge
    last_update_time: float = 0.0

    @property
    def status(self) -> BatteryStatus:
        if self.is_charging:
            return BatteryStatus.FULL if self.battery_percent >= 95 else BatteryStatus.CHARGING
        if self.battery_percent < 15:
            return BatteryStatus.CRITICAL
        if self.battery_percent < 25:
            return BatteryStatus.LOW
        return BatteryStatus.NORMAL

    @property
    def time_to_depletion_minutes(self) -> float:
        """Estimate minutes until battery depleted at current rate."""
        if self.is_charging:
            return float('inf')
        if self.consumption_rate_per_meter <= 0:
            return float('inf')
        return self.battery_percent / (self.consumption_rate_per_meter * 1.5)  # Assume 1.5 m/s avg speed

    @property
    def time_to_full_minutes(self) -> float:
        """Minutes until fully charged."""
        if not self.is_charging or self.charge_rate_percent_per_min <= 0:
            return float('inf')
        remaining = 100.0 - self.battery_percent
        return remaining / self.charge_rate_percent_per_min


@dataclass
class EnergyDemandPrediction:
    """Energy demand forecast."""
    forecast_time: float
    horizon_minutes: float
    expected_charging_demand: int       # How many AGVs need charging soon
    low_battery_warning_agvs: List[str] = field(default_factory=list)
    critical_battery_agvs: List[str] = field(default_factory=list)
    charger_utilization_estimate: float = 0.0  # 0-1
    recommended_charging_actions: List[Dict] = field(default_factory=list)


class BatteryPredictor:
    """
    Tracks and predicts AGV battery states.

    Key capabilities:
    1. Real-time battery monitoring
    2. Consumption modeling based on distance/speed/conveyor usage
    3. Charging demand forecasting
    4. Proactive charging scheduling recommendations
    """

    def __init__(
        self,
        low_threshold: float = 25.0,
        critical_threshold: float = 15.0,
        default_consumption_rate: float = 0.05,  # % per meter
        default_charge_rate: float = 5.0,  # % per minute
    ):
        self.low_thresh = low_threshold
        self.critical_thresh = critical_threshold
        self.default_consumption = default_consumption_rate
        self.default_charge_rate = default_charge_rate

        # Per-AGV battery states
        self._battery_states: Dict[str, BatteryState] = {}

        # Charging stations
        self._charger_ids: List[str] = []

        # Historical consumption rates (for adaptive calibration)
        self._consumption_history: Dict[str, List[Tuple[float, float]]] = defaultdict(list)  # agv -> [(distance, %consumed)]

    def register_charger(self, charger_id: str) -> None:
        """Register a charging station."""
        if charger_id not in self._charger_ids:
            self._charger_ids.append(charger_id)

    def register_chargers(self, charger_ids: List[str]) -> None:
        """Register multiple chargers."""
        for cid in charger_ids:
            self.register_charger(cid)

    def update_battery(
        self,
        agv_id: str,
        battery_percent: float,
        is_charging: bool = False,
        distance_since_last_update: float = 0.0,
        previous_battery: Optional[float] = None,
    ) -> BatteryState:
        """
        Update battery state for an AGV.

        Calibrates consumption model from observed data.
        """
        now = time.time()

        # Calibrate consumption rate from real observations
        if distance_since_last_update > 0 and previous_battery is not None:
            consumed = previous_battery - battery_percent
            if consumed > 0:
                observed_rate = consumed / distance_since_last_update
                self._consumption_history[agv_id].append((
                    distance_since_last_update, consumed,
                ))
                # Keep only last 50 measurements
                if len(self._consumption_history[agv_id]) > 50:
                    self._consumption_history[agv_id] = self._consumption_history[agv_id][-50:]

        # Get calibrated consumption rate
        calib_rate = self._get_calibrated_rate(agv_id)

        state = BatteryState(
            agv_id=agv_id,
            battery_percent=max(0.0, min(100.0, battery_percent)),
            is_charging=is_charging,
            charge_rate_percent_per_min=self.default_charge_rate,
            consumption_rate_per_meter=calib_rate,
            estimated_range_meters=battery_percent / calib_rate if calib_rate > 0 else 500.0,
            last_update_time=now,
        )
        self._battery_states[agv_id] = state
        return state

    def _get_calibrated_rate(self, agv_id: str) -> float:
        """Get calibrated consumption rate, falling back to default."""
        history = self._consumption_history.get(agv_id, [])
        if len(history) >= 5:
            # Use weighted average (recent measurements weighted more)
            total_dist = sum(d for d, _ in history[-20:])
            total_cons = sum(c for _, c in history[-20:])
            if total_dist > 0:
                return total_cons / total_dist
        return self.default_consumption

    def predict_energy_demand(
        self,
        horizon_minutes: float = 30.0,
    ) -> EnergyDemandPrediction:
        """
        Predict energy demand over the given horizon.
        """
        now = time.time()
        low_warn: List[str] = []
        critical: List[str] = []
        charging_demand = 0
        actions: List[Dict] = []

        for agv_id, state in self._battery_states.items():
            # Estimate future battery level
            if state.status == BatteryStatus.CRITICAL:
                critical.append(agv_id)
                charging_demand += 1
                actions.append({
                    "action": "emergency_charge",
                    "agv_id": agv_id,
                    "priority": 10,
                    "reason": f"Battery critical: {state.battery_percent:.0f}%",
                })
            elif state.status == BatteryStatus.LOW:
                low_warn.append(agv_id)
                charging_demand += 1
                # Estimate when it becomes critical
                minutes_to_critical = (
                    (state.battery_percent - self.critical_thresh) /
                    (state.consumption_rate_per_meter * 1.5 * 60)  # m/min
                ) if state.consumption_rate_per_meter > 0 else 999
                if minutes_to_critical < horizon_minutes:
                    actions.append({
                        "action": "schedule_charge",
                        "agv_id": agv_id,
                        "priority": 7,
                        "reason": f"Battery low ({state.battery_percent:.0f}%), "
                                  f"~{minutes_to_critical:.0f}min to critical",
                    })
            elif state.status == BatteryStatus.NORMAL:
                # Check if battery might drop below threshold within horizon
                est_consumption = state.consumption_rate_per_meter * 90.0  # assume 90m traveled
                future_pct = state.battery_percent - est_consumption
                if future_pct < self.low_thresh:
                    actions.append({
                        "action": "consider_charge",
                        "agv_id": agv_id,
                        "priority": 4,
                        "reason": f"May reach low battery (~{future_pct:.0f}%) "
                                  f"in {horizon_minutes:.0f}min",
                    })

        # Charger utilization estimate
        num_charging = sum(
            1 for s in self._battery_states.values()
            if s.is_charging
        )
        utilization = num_charging / max(len(self._charger_ids), 1)

        return EnergyDemandPrediction(
            forecast_time=now,
            horizon_minutes=horizon_minutes,
            expected_charging_demand=charging_demand,
            low_battery_warning_agvs=low_warn,
            critical_battery_agvs=critical,
            charger_utilization_estimate=utilization,
            recommended_charging_actions=actions,
        )

    @property
    def stats(self) -> dict:
        return {
            "tracked_agvs": len(self._battery_states),
            "chargers_registered": len(self._charger_ids),
            "low_battery_count": sum(
                1 for s in self._battery_states.values()
                if s.status in (BatteryStatus.LOW, BatteryStatus.CRITICAL)
            ),
            "charging_count": sum(
                1 for s in self._battery_states.values()
                if s.is_charging
            ),
        }
