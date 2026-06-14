"""
Three-Layer Hybrid Orchestrator V2.

Strategic Layer (决策层):  MIP/CP global optimization, day/week-level planning
Tactical Layer (调度层):   Meta-heuristics + RL, minute-level task dispatch
Operational Layer (执行层): A*+TW + traffic control, millisecond-level routing

Coordination via rolling-horizon optimization + Lagrange relaxation.
"""

from .orchestrator import (
    HybridOrchestratorV2,
    OrchestratorMode, OrchestratorConfig, OrchestratorSnapshot,
)

__all__ = [
    'HybridOrchestratorV2',
    'OrchestratorMode', 'OrchestratorConfig', 'OrchestratorSnapshot',
]
