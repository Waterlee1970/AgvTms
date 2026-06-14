"""
Nonlinear Programming for Conveyor Line Task Scheduling.

非线性规划 - 用于输送线任务排序优化

Formulates conveyor scheduling as a constrained nonlinear optimization problem:
- Decision variables: start times for each task on each conveyor segment
- Objective: minimize total completion time + energy consumption
- Constraints: capacity limits, precedence, no overlap, segment availability

Uses scipy.optimize.minimize with SLSQP (Sequential Least Squares Programming).

Mathematical formulation:
    minimize  Σ(w1 * t_i_complete + w2 * E_i)
    subject to:
        t_j >= t_i + d_i          (precedence)
        t_i >= 0                   (non-negative start)
        Σ x_ik(t) <= C_k          (capacity at time t on segment k)
        t_i ∈ R                    (continuous start time)

Where:
- t_i: start time of task i
- d_i: duration of task i
- E_i: energy consumed by task i
- C_k: capacity of conveyor segment k
- x_ik(t): indicator if task i is on segment k at time t
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize, Bounds, LinearConstraint, NonlinearConstraint

from ..models.schemas import (
    ConveyorSegment,
    ConveyorTask,
    ConveyorTimelineEntry,
    NlpConfig,
    NlpSolver,
)

logger = logging.getLogger(__name__)


@dataclass
class NlpResult:
    """Result of NLP optimization."""
    timeline: List[ConveyorTimelineEntry]
    total_completion_time: float
    total_energy: float
    objective_value: float
    success: bool
    message: str
    iterations: int
    runtime_ms: float


class ConveyorNlpOptimizer:
    """
    Nonlinear Programming optimizer for conveyor line scheduling.

    Models the conveyor system as a flow shop with parallel segments.
    Each task must pass through one or more conveyor segments in sequence.
    """

    def __init__(self, config: NlpConfig):
        self.config = config

    def _build_objective(
        self,
        tasks: List[ConveyorTask],
        segments: Dict[str, ConveyorSegment],
        task_segment_map: Dict[str, List[str]],
        w_time: float = 1.0,
        w_energy: float = 0.5,
    ) -> Callable[[np.ndarray], float]:
        """
        Build the objective function.

        f(x) = w_time * Σ completion_time_i + w_energy * Σ energy_i
        
        Where x is the flattened vector of task start times on each segment.
        For simplicity, each task has one start time variable (first segment).
        Durations propagate through subsequent segments.
        """
        n = len(tasks)
        task_ids = [t.id for t in tasks]

        def objective(x: np.ndarray) -> float:
            total = 0.0
            for i, task in enumerate(tasks):
                start = x[i]
                seg_ids = task_segment_map.get(task.id, [task.segment_id])
                total_duration = 0.0
                total_energy = 0.0
                for seg_id in seg_ids:
                    seg = segments.get(seg_id)
                    if seg:
                        seg_duration = seg.length / max(seg.speed, 0.01)
                        total_duration += seg_duration
                        total_energy += seg.energy_consumption * seg_duration

                completion = start + total_duration
                total += w_time * completion + w_energy * total_energy
            return float(total)

        return objective

    def _build_bounds(self, tasks: List[ConveyorTask]) -> Bounds:
        """Build variable bounds: all start times >= 0."""
        n = len(tasks)
        return Bounds([0.0] * n, [np.inf] * n)

    def _build_precedence_constraints(
        self, tasks: List[ConveyorTask], task_segment_map: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Build precedence constraints.
        
        For each task with predecessors:
            t_task >= t_pred + d_pred
        """
        constraints = []
        task_index = {t.id: i for i, t in enumerate(tasks)}

        for task in tasks:
            for pred_id in task.predecessor_ids:
                if pred_id in task_index:
                    j = task_index[task.id]
                    i = task_index[pred_id]

                    # Calculate predecessor duration
                    pred = next((t for t in tasks if t.id == pred_id), None)
                    if pred:
                        seg_ids = task_segment_map.get(pred_id, [pred.segment_id])
                        pred_duration = 0.0
                        for seg_id in seg_ids:
                            seg = next((s for s in self._segments_list if s.id == seg_id), None)
                            if seg:
                                pred_duration += seg.length / max(seg.speed, 0.01)

                        # Constraint: x[j] - x[i] >= pred_duration
                        A = np.zeros(len(tasks))
                        A[j] = 1.0
                        A[i] = -1.0
                        constraints.append(
                            LinearConstraint(A, lb=pred_duration, ub=np.inf)
                        )

        return constraints

    def _build_capacity_constraints(
        self,
        tasks: List[ConveyorTask],
        segments: Dict[str, ConveyorSegment],
        task_segment_map: Dict[str, List[str]],
    ) -> List[NonlinearConstraint]:
        """
        Build capacity constraints using nonlinear constraints.
        
        At any time t, the number of tasks on a segment must not exceed its capacity.
        
        This is approximated by constraining overlapping tasks:
            For any two tasks i, j on same segment:
                |t_i - t_j| >= min_overlap_gap
        """
        constraints = []

        for seg_id, seg in segments.items():
            seg_tasks = [t for t in tasks if seg_id in task_segment_map.get(t.id, [t.segment_id])]
            if len(seg_tasks) <= 1:
                continue

            # Capacity constraint: at most max_capacity tasks simultaneously
            # Simplified: ensure sequential processing with gap
            task_indices = {t.id: i for i, t in enumerate(tasks)}

            def make_capacity_fn(seg_tasks_inner, task_indices_inner, capacity, seg_duration):
                def capacity_fn(x):
                    n = len(x)
                    violations = 0.0
                    for i, t1 in enumerate(seg_tasks_inner):
                        t1_idx = task_indices_inner[t1.id]
                        for t2 in seg_tasks_inner[i + 1:]:
                            t2_idx = task_indices_inner[t2.id]
                            overlap = seg_duration - abs(x[t1_idx] - x[t2_idx])
                            if overlap > 0:
                                violations += overlap
                    return -violations  # negative means constraint satisfied (>= 0)
                return capacity_fn

            seg_duration = seg.length / max(seg.speed, 0.01)
            capacity_fn = make_capacity_fn(seg_tasks, task_indices, seg.max_capacity, seg_duration)
            constraints.append(
                NonlinearConstraint(capacity_fn, lb=-np.inf, ub=0.0)
            )

        return constraints

    def optimize(
        self,
        tasks: List[ConveyorTask],
        segments: List[ConveyorSegment],
        task_segment_map: Optional[Dict[str, List[str]]] = None,
    ) -> NlpResult:
        """
        Run NLP optimization for conveyor scheduling.

        Args:
            tasks: Conveyor tasks to schedule
            segments: Available conveyor segments
            task_segment_map: Mapping of task_id -> list of segment_ids to traverse

        Returns:
            NlpResult with optimized timeline
        """
        start_time = time.perf_counter()

        if not tasks:
            return NlpResult(
                timeline=[], total_completion_time=0.0, total_energy=0.0,
                objective_value=0.0, success=True, message="No tasks to schedule",
                iterations=0, runtime_ms=0.0,
            )

        self._segments_list = segments
        segments_dict = {s.id: s for s in segments}

        # Default: each task uses its primary segment
        if task_segment_map is None:
            task_segment_map = {t.id: [t.segment_id] for t in tasks}

        n = len(tasks)

        # Build optimization problem
        objective = self._build_objective(tasks, segments_dict, task_segment_map)
        bounds = self._build_bounds(tasks)

        # Initial guess: sequential assignment (each task starts after previous)
        x0 = np.zeros(n)
        cumulative = 0.0
        for i, task in enumerate(tasks):
            seg_ids = task_segment_map.get(task.id, [task.segment_id])
            x0[i] = cumulative
            for seg_id in seg_ids:
                seg = segments_dict.get(seg_id)
                if seg:
                    cumulative += seg.length / max(seg.speed, 0.01)

        # Build constraints
        constraints = self._build_precedence_constraints(tasks, task_segment_map)
        capacity_constraints = self._build_capacity_constraints(tasks, segments_dict, task_segment_map)
        constraints.extend(capacity_constraints)

        # Select solver method
        solver_map = {
            NlpSolver.SLSQP: "SLSQP",
            NlpSolver.COBYLA: "COBYLA",
            NlpSolver.IPOPT: "SLSQP",  # fallback: ipopt not bundled with scipy
        }
        method = solver_map.get(self.config.solver, "SLSQP")

        try:
            result = minimize(
                objective,
                x0,
                method=method,
                bounds=bounds,
                constraints=constraints if method == "SLSQP" else [],
                options={
                    "maxiter": self.config.max_iter,
                    "ftol": self.config.tolerance,
                    "disp": self.config.verbose,
                },
            )

            success = result.success
            message = result.message
            iterations = result.nit if hasattr(result, 'nit') else 0
            x_opt = result.x

        except Exception as e:
            logger.warning(f"NLP optimization failed: {e}, using initial solution")
            success = False
            message = str(e)
            iterations = 0
            x_opt = x0

        # Build timeline from optimized start times
        timeline = []
        total_completion = 0.0
        total_energy = 0.0

        for i, task in enumerate(tasks):
            start = max(0.0, float(x_opt[i]))
            seg_ids = task_segment_map.get(task.id, [task.segment_id])
            cumulative_time = start

            for seg_id in seg_ids:
                seg = segments_dict.get(seg_id)
                if seg is None:
                    continue
                seg_duration = seg.length / max(seg.speed, 0.01)
                end = cumulative_time + seg_duration
                timeline.append(ConveyorTimelineEntry(
                    task_id=task.id,
                    segment_id=seg_id,
                    start_time=cumulative_time,
                    end_time=end,
                    cargo_id=task.cargo_id,
                ))
                total_energy += seg.energy_consumption * seg_duration
                cumulative_time = end

            total_completion = max(total_completion, cumulative_time)

        objective_value = float(result.fun) if success else float(objective(x_opt))
        runtime_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            f"NLP complete: completion_time={total_completion:.2f}s, "
            f"energy={total_energy:.2f}kW, success={success}, runtime={runtime_ms:.0f}ms"
        )

        return NlpResult(
            timeline=timeline,
            total_completion_time=total_completion,
            total_energy=total_energy,
            objective_value=objective_value,
            success=success,
            message=message,
            iterations=iterations,
            runtime_ms=runtime_ms,
        )


def run_nlp(
    tasks: List[ConveyorTask],
    segments: List[ConveyorSegment],
    task_segment_map: Optional[Dict[str, List[str]]] = None,
    config: Optional[NlpConfig] = None,
) -> NlpResult:
    """
    Convenience function to run NLP conveyor scheduling optimization.
    """
    cfg = config or NlpConfig()
    optimizer = ConveyorNlpOptimizer(cfg)
    return optimizer.optimize(tasks, segments, task_segment_map)
