"""
Unified Predictive Engine — integrates all predictors into one interface.

Provides a single entry point for the orchestrator to query predictions:
- Task arrival prediction
- Congestion hotspot forecasting
- Battery/energy demand prediction
- Combined risk assessment
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Import sub-predictors
from .task_predictor import TaskArrivalPredictor, TaskArrivalEvent, ArrivalPrediction
from .congestion_predictor import CongestionPredictor, CongestionForecast
from .battery_predictor import BatteryPredictor, BatteryState, EnergyDemandPrediction


@dataclass
class PredictiveInsight:
    """Combined predictive insight for decision-making."""
    timestamp: float
    horizon_seconds: float

    # Task arrival
    expected_new_tasks: float
    task_confidence: float
    busy_pickup_nodes: Dict[str, float]

    # Congestion
    congestion_score: float
    congestion_hotspots: List[Dict[str, Any]]

    # Battery
    charging_demand: int
    critical_battery_agvs: List[str]
    charging_recommendations: List[Dict[str, Any]]

    # Overall risk
    overall_risk_score: float  # 0-1
    recommended_actions: List[str]


class PredictiveEngine:
    """
    Unified predictive engine combining all prediction models.

    Usage:
        engine = PredictiveEngine()
        engine.set_graph(nodes_set, edge_list)
        engine.record_task_arrival(event)
        engine.record_agv_state(positions, paths, batteries)
        insight = engine.predict(horizon=300)
    """

    def __init__(self):
        self.task_predictor = TaskArrivalPredictor()
        self.congestion_predictor = CongestionPredictor()
        self.battery_predictor = BatteryPredictor()

        self._initialized = False

    def set_graph_topology(
        self,
        nodes: set,
        edges: List[tuple],
        charger_ids: Optional[List[str]] = None,
    ) -> None:
        """Initialize graph-dependent predictors."""
        self.congestion_predictor.set_graph_topology(nodes, edges)
        if charger_ids:
            self.battery_predictor.register_chargers(charger_ids)
        self._initialized = True

    def record_task_arrival(
        self,
        task_id: str,
        pickup_node: str,
        dropoff_node: str,
        priority: int = 5,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a new task arrival event."""
        self.task_predictor.record_arrival(TaskArrivalEvent(
            timestamp=timestamp or time.time(),
            task_id=task_id,
            pickup_node=pickup_node,
            dropoff_node=dropoff_node,
            priority=priority,
        ))

    def record_agv_state(
        self,
        agv_positions: Dict[str, str],
        agv_paths: Optional[Dict[str, List[str]]] = None,
        agv_batteries: Optional[Dict[str, float]] = None,
    ) -> None:
        """Record current AGV fleet state."""
        # Congestion
        self.congestion_predictor.record_state_snapshot(agv_positions, agv_paths)

        # Battery
        if agv_batteries:
            for agv_id, batt_pct in agv_batteries.items():
                self.battery_predictor.update_battery(
                    agv_id, batt_pct, is_charging=False
                )

    def predict(self, horizon_seconds: float = 300.0) -> PredictiveInsight:
        """
        Generate unified prediction across all dimensions.

        Returns combined insight with actionable recommendations.
        """
        t_pred = time.time()

        # Run all predictors in sequence
        task_pred = self.task_predictor.predict(horizon_seconds=horizon_seconds)
        cong_pred = self.congestion_predictor.predict()
        energy_pred = self.battery_predictor.predict_energy_demand(
            horizon_minutes=horizon_seconds / 60.0
        )

        # Build hotspot summaries
        hotspots = [
            {
                "node_id": h.node_id,
                "severity": round(h.severity, 3),
                "cause": h.cause,
                "peak_time_s": h.peak_time - t_pred,
            }
            for h in cong_pred.hotspots[:10]
        ]

        # Build recommendations
        recommendations: List[str] = []

        if task_pred.predicted_count > 5 and task_pred.confidence > 0.6:
            recommendations.append(
                f"Expect ~{task_pred.predicted_count:.0f} tasks in next "
                f"{horizon_seconds:.0f}s (confidence={task_pred.confidence:.0%})"
            )

        if cong_pred.overall_congestion_score > 0.5:
            recommendations.append(
                f"High congestion risk (score={cong_pred.overall_congestion_score:.2f}) "
                f"at {len(cong_pred.hotspots)} hotspots"
            )

        for action in energy_pred.recommended_charging_actions:
            if action["priority"] >= 7:
                rec_str = (
                    f"[{action['action'].upper()}] AGV {action['agv_id']}: "
                    f"{action['reason']}"
                )
                recommendations.append(rec_str)

        # Overall risk score (weighted combination)
        task_risk = min(task_pred.predicted_count / 20.0, 1.0)  # Saturates at 20 tasks
        cong_risk = cong_pred.overall_congestion_score
        energy_risk = min(len(energy_pred.critical_battery_agvs) / 5.0, 1.0)

        overall_risk = 0.35 * task_risk + 0.40 * cong_risk + 0.25 * energy_risk

        return PredictiveInsight(
            timestamp=t_pred,
            horizon_seconds=horizon_seconds,

            expected_new_tasks=task_pred.predicted_count,
            task_confidence=task_pred.confidence,
            busy_pickup_nodes=dict(list(task_pred.predicted_pickups.items())[:10]),

            congestion_score=cong_pred.overall_congestion_score,
            congestion_hotspots=hotspots,

            charging_demand=energy_pred.expected_charging_demand,
            critical_battery_agvs=energy_pred.critical_battery_agvs,
            charging_recommendations=energy_pred.recommended_charging_actions[:5],

            overall_risk_score=round(min(overall_risk, 1.0), 3),
            recommended_actions=recommendations,
        )

    @property
    def stats(self) -> dict:
        return {
            "task_predictor": self.task_predictor.stats,
            "congestion_predictor": self.congestion_predictor.stats,
            "battery_predictor": self.battery_predictor.stats,
            "initialized": self._initialized,
        }


# Export convenience imports
__all__ = [
    'PredictiveEngine',
    'PredictiveInsight',
    'TaskArrivalPredictor',
    'CongestionPredictor',
    'BatteryPredictor',
]
