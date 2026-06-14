"""
Benchmark Scenario Generator for AGV-TMS performance testing.

Generates realistic factory layouts at different scales:
- small:  20 AGVs,  200 nodes,  50 tasks  (development)
- medium: 100 AGVs, 1000 nodes, 500 tasks (integration)
- large:  500 AGVs, 5000 nodes, 2000 tasks (load test)

Grid-based factory layout with:
- Horizontal aisles (paths)
- Vertical cross-aisles (crossings)
- Pickup/dropoff stations at grid intersections
- Charging stations along walls
- Conveyor lines connecting work zones
"""

from __future__ import annotations

import json
import logging
import math
import random
import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.models.schemas import (
    AgvRunStatus, AgvStatus, AgvTask, AgvTaskStatus,
    ConveyorSegment, ConveyorTask,
    EdgeDirection, MapEdge, MapNode, NodeType,
)

logger = logging.getLogger(__name__)


@dataclass
class ScenarioConfig:
    """Configuration for a benchmark scenario."""
    name: str
    grid_width: int            # number of horizontal aisles
    grid_height: int           # number of vertical aisles
    node_spacing: float = 3.0  # meters between nodes
    num_agvs: int = 20
    num_tasks: int = 50
    num_conveyor_lines: int = 2
    conveyor_segments_per_line: int = 3
    seed: int = 42

    @property
    def total_nodes(self) -> int:
        """Total nodes = grid_points + pickup/dropoff + charge stations."""
        grid_points = self.grid_width * self.grid_height
        stations = self.num_agvs // 2  # pickup + dropoff pairs
        charges = self.num_agvs // 4
        return grid_points + stations * 2 + charges


@dataclass
class BenchmarkScenario:
    """A complete benchmark scenario."""
    config: ScenarioConfig
    nodes: List[MapNode]
    edges: List[MapEdge]
    agvs: List[AgvStatus]
    tasks: List[AgvTask]
    conveyor_segments: List[ConveyorSegment]
    conveyor_tasks: List[ConveyorTask]
    metadata: dict = field(default_factory=dict)


class ScenarioGenerator:
    """Generates benchmark scenarios for performance testing."""

    # Predefined scenario templates
    SCENARIOS = {
        "small": ScenarioConfig(
            name="small", grid_width=15, grid_height=15,
            num_agvs=20, num_tasks=50, num_conveyor_lines=2,
            conveyor_segments_per_line=3, seed=42,
        ),
        "medium": ScenarioConfig(
            name="medium", grid_width=35, grid_height=30,
            num_agvs=100, num_tasks=500, num_conveyor_lines=5,
            conveyor_segments_per_line=5, seed=123,
        ),
        "large": ScenarioConfig(
            name="large", grid_width=70, grid_height=70,
            num_agvs=500, num_tasks=2000, num_conveyor_lines=10,
            conveyor_segments_per_line=8, seed=456,
        ),
        "stress": ScenarioConfig(
            name="stress", grid_width=100, grid_height=100,
            num_agvs=1000, num_tasks=5000, num_conveyor_lines=20,
            conveyor_segments_per_line=10, seed=789,
        ),
    }

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def generate(self, scenario_name: str = "small") -> BenchmarkScenario:
        """Generate a complete benchmark scenario."""
        config = self.SCENARIOS.get(scenario_name)
        if config is None:
            raise ValueError(f"Unknown scenario: {scenario_name}. "
                           f"Available: {list(self.SCENARIOS.keys())}")

        self.rng = random.Random(config.seed)

        logger.info(f"Generating {scenario_name} scenario: "
                     f"{config.total_nodes} nodes, {config.num_agvs} AGVs, "
                     f"{config.num_tasks} tasks")

        # Phase 1: Generate grid layout
        nodes, edges = self._generate_grid(config)

        # Phase 2: Add pickup/dropoff stations
        nodes, pickup_ids, dropoff_ids = self._add_stations(nodes, edges, config)

        # Phase 3: Add charging stations
        nodes, charge_ids = self._add_charging_stations(nodes, config)

        # Phase 4: Generate conveyor lines
        conveyor_segments, conveyor_tasks = self._generate_conveyors(
            nodes, config
        )

        # Phase 5: Generate AGVs (placed at random nodes)
        agvs = self._generate_agvs(config, nodes, charge_ids)

        # Phase 6: Generate tasks
        tasks = self._generate_tasks(config, pickup_ids, dropoff_ids)

        # Calculate metadata
        total_edge_distance = sum(e.distance for e in edges)

        return BenchmarkScenario(
            config=config,
            nodes=nodes,
            edges=edges,
            agvs=agvs,
            tasks=tasks,
            conveyor_segments=conveyor_segments,
            conveyor_tasks=conveyor_tasks,
            metadata={
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "total_edge_distance": total_edge_distance,
                "pickup_stations": len(pickup_ids),
                "dropoff_stations": len(dropoff_ids),
                "charge_stations": len(charge_ids),
                "avg_node_degree": len(edges) / max(len(nodes), 1) * 2,
            },
        )

    def _generate_grid(
        self, config: ScenarioConfig
    ) -> Tuple[List[MapNode], List[MapEdge]]:
        """Generate a grid-based factory layout."""
        nodes: List[MapNode] = []
        edges: List[MapEdge] = []
        node_index = 1
        spacing = config.node_spacing

        # Create grid nodes
        grid_ids: Dict[Tuple[int, int], str] = {}
        for row in range(config.grid_height):
            for col in range(config.grid_width):
                node_id = f"N{node_index:04d}"
                node_index += 1
                x = col * spacing
                y = row * spacing

                # Determine node type
                if row == 0 or row == config.grid_height - 1:
                    ntype = NodeType.PATH
                elif col == 0 or col == config.grid_width - 1:
                    ntype = NodeType.PATH
                elif row % 3 == 0 and col % 3 == 0:
                    ntype = NodeType.CROSS
                else:
                    ntype = NodeType.PATH

                capacity = 2 if ntype == NodeType.CROSS else 1

                nodes.append(MapNode(
                    id=node_id, name=f"Grid({row},{col})",
                    x=x, y=y, type=ntype, capacity=capacity,
                ))
                grid_ids[(row, col)] = node_id

        # Create horizontal edges (rightward)
        for row in range(config.grid_height):
            for col in range(config.grid_width - 1):
                frm = grid_ids[(row, col)]
                to = grid_ids[(row, col + 1)]
                direction = EdgeDirection.BIDIRECTIONAL

                # Some edges can be one-way for realism
                if self.rng.random() < 0.05:
                    direction = EdgeDirection.FORWARD

                edges.append(MapEdge(
                    from_node=frm, to_node=to,
                    distance=spacing,
                    direction=direction,
                    speed_limit=self.rng.uniform(1.0, 2.0),
                    is_conveyor=False,
                ))

        # Create vertical edges (downward)
        for row in range(config.grid_height - 1):
            for col in range(config.grid_width):
                frm = grid_ids[(row, col)]
                to = grid_ids[(row + 1, col)]
                edges.append(MapEdge(
                    from_node=frm, to_node=to,
                    distance=spacing,
                    direction=EdgeDirection.BIDIRECTIONAL,
                    speed_limit=self.rng.uniform(1.0, 1.5),
                    is_conveyor=False,
                ))

        return nodes, edges

    def _add_stations(
        self,
        nodes: List[MapNode],
        edges: List[MapEdge],
        config: ScenarioConfig,
    ) -> Tuple[List[MapNode], List[str], List[str]]:
        """Add pickup and dropoff stations to the layout."""
        existing_ids = {n.id for n in nodes}
        pickup_ids: List[str] = []
        dropoff_ids: List[str] = []
        node_index = len(nodes) + 1
        spacing = config.node_spacing
        margin = 1

        num_stations = config.num_agvs // 2

        for i in range(num_stations):
            # Pickup station at grid edge
            p_id = f"N{node_index:04d}"
            node_index += 1
            row = self.rng.randint(margin, config.grid_height - margin - 1)
            p_x = -spacing
            p_y = row * spacing
            nodes.append(MapNode(
                id=p_id, name=f"Pickup_{i+1}",
                x=p_x, y=p_y, type=NodeType.PICKUP, capacity=1,
            ))
            pickup_ids.append(p_id)

            # Connect to nearest grid node
            grid_col_0 = None
            for n in nodes:
                if n.id in existing_ids and abs(n.y - p_y) < 0.01 and abs(n.x - 0) < 0.01:
                    grid_col_0 = n.id
                    break
            if grid_col_0:
                edges.append(MapEdge(
                    from_node=p_id, to_node=grid_col_0,
                    distance=spacing,
                    direction=EdgeDirection.BIDIRECTIONAL,
                    speed_limit=1.0,
                ))

            # Dropoff station at opposite edge
            d_id = f"N{node_index:04d}"
            node_index += 1
            d_row = self.rng.randint(margin, config.grid_height - margin - 1)
            d_x = (config.grid_width) * spacing
            d_y = d_row * spacing
            nodes.append(MapNode(
                id=d_id, name=f"Dropoff_{i+1}",
                x=d_x, y=d_y, type=NodeType.DROPOFF, capacity=1,
            ))
            dropoff_ids.append(d_id)

            # Connect to nearest grid node
            grid_col_end = None
            for n in nodes:
                if n.id in existing_ids and abs(n.y - d_y) < spacing and abs(n.x - (config.grid_width - 1) * spacing) < spacing:
                    grid_col_end = n.id
                    break
            if grid_col_end:
                edges.append(MapEdge(
                    from_node=grid_col_end, to_node=d_id,
                    distance=spacing,
                    direction=EdgeDirection.BIDIRECTIONAL,
                    speed_limit=1.0,
                ))

        return nodes, pickup_ids, dropoff_ids

    def _add_charging_stations(
        self,
        nodes: List[MapNode],
        config: ScenarioConfig,
    ) -> Tuple[List[MapNode], List[str]]:
        """Add charging stations along factory walls."""
        charge_ids: List[str] = []
        node_index = len(nodes) + 1
        spacing = config.node_spacing

        num_chargers = max(2, config.num_agvs // 4)
        for i in range(num_chargers):
            c_id = f"N{node_index:04d}"
            node_index += 1

            # Place along bottom wall
            c_x = (i + 1) * spacing * 5
            c_y = -spacing

            nodes.append(MapNode(
                id=c_id, name=f"Charge_{i+1}",
                x=c_x, y=c_y, type=NodeType.CHARGE, capacity=2,
            ))
            charge_ids.append(c_id)

        return nodes, charge_ids

    def _generate_conveyors(
        self,
        nodes: List[MapNode],
        config: ScenarioConfig,
    ) -> Tuple[List[ConveyorSegment], List[ConveyorTask]]:
        """Generate conveyor line segments and tasks."""
        segments: List[ConveyorSegment] = []
        tasks: List[ConveyorTask] = []
        spacing = config.node_spacing

        for line in range(config.num_conveyor_lines):
            start_y = (line + 1) * spacing * 3
            prev_id: Optional[str] = None
            prev_task_id: Optional[str] = None

            for seg in range(config.conveyor_segments_per_line):
                seg_id = f"CONV_{line}_{seg}"
                from_id = f"CONV_N_{line}_{seg}"
                to_id = f"CONV_N_{line}_{seg+1}"

                # Add conveyor nodes
                nodes.append(MapNode(
                    id=from_id, name=f"Conv{line}In{seg}",
                    x=seg * 10.0, y=start_y,
                    type=NodeType.CONVEYOR_IN,
                ))
                if seg == config.conveyor_segments_per_line - 1:
                    nodes.append(MapNode(
                        id=to_id, name=f"Conv{line}Out{seg}",
                        x=(seg + 1) * 10.0, y=start_y,
                        type=NodeType.CONVEYOR_OUT,
                    ))

                segments.append(ConveyorSegment(
                    id=seg_id, name=f"Line{line}Seg{seg}",
                    from_node=from_id, to_node=to_id,
                    speed=0.5, length=10.0,
                    direction=EdgeDirection.FORWARD,
                    max_capacity=3,
                ))

                # Generate a conveyor task
                task_id = f"CT_{line}_{seg}"
                predecessors = [prev_task_id] if prev_task_id else []
                tasks.append(ConveyorTask(
                    id=task_id, segment_id=seg_id,
                    duration=self.rng.uniform(5.0, 15.0),
                    predecessor_ids=predecessors,
                    priority=self.rng.randint(1, 5),
                    cargo_id=f"CARGO_{line}_{seg}",
                ))
                prev_task_id = task_id

        return segments, tasks

    def _generate_agvs(
        self,
        config: ScenarioConfig,
        nodes: List[MapNode],
        charge_ids: List[str],
    ) -> List[AgvStatus]:
        """Generate AGV fleet with random starting positions."""
        agvs: List[AgvStatus] = []
        available_nodes = [n for n in nodes if n.type not in
                         (NodeType.CONVEYOR_IN, NodeType.CONVEYOR_OUT)]

        for i in range(config.num_agvs):
            start_node = self.rng.choice(available_nodes)
            battery = self.rng.uniform(50.0, 100.0)

            # 30% of AGVs start at charging stations
            if self.rng.random() < 0.3 and charge_ids:
                start_node_id = self.rng.choice(charge_ids)
                start_node = next(n for n in nodes if n.id == start_node_id)
                battery = self.rng.uniform(80.0, 100.0)

            agvs.append(AgvStatus(
                id=f"AGV_{i+1:03d}",
                name=f"AGV-{i+1}",
                x=start_node.x, y=start_node.y,
                battery=battery,
                status=AgvRunStatus.IDLE,
                current_node=start_node.id,
                speed=0.0,
                capacity=self.rng.choice([1, 2]),
                path=[],
            ))

        return agvs

    def _generate_tasks(
        self,
        config: ScenarioConfig,
        pickup_ids: List[str],
        dropoff_ids: List[str],
    ) -> List[AgvTask]:
        """Generate transport tasks."""
        tasks: List[AgvTask] = []
        now = datetime.now()

        for i in range(config.num_tasks):
            pickup = self.rng.choice(pickup_ids)
            dropoff = self.rng.choice(dropoff_ids)
            # Avoid trivial pickup=dropoff
            while dropoff == pickup:
                dropoff = self.rng.choice(dropoff_ids)

            priority = self.rng.choices(
                [1, 2, 3, 5, 7, 10],
                weights=[20, 25, 20, 15, 10, 10],
                k=1,
            )[0]

            deadline_offset = self.rng.randint(300, 3600)  # 5-60 min
            deadline = now + timedelta(seconds=deadline_offset)

            tasks.append(AgvTask(
                id=f"TASK_{i+1:04d}",
                pickup_node=pickup,
                dropoff_node=dropoff,
                priority=priority,
                status=AgvTaskStatus.PENDING,
                create_time=now,
                deadline=deadline,
                estimated_duration=self.rng.uniform(30.0, 300.0),
                cargo_type=self.rng.choice(["standard", "heavy", "fragile"]),
            ))

        return tasks


def generate_all_scenarios(output_dir: str = "./benchmark_data") -> dict:
    """Generate all predefined scenarios and save to disk."""
    os.makedirs(output_dir, exist_ok=True)
    generator = ScenarioGenerator()

    results = {}
    for name in ScenarioGenerator.SCENARIOS:
        scenario = generator.generate(name)
        filename = f"{output_dir}/scenario_{name}.json"

        # Serialize to JSON-compatible dict
        data = {
            "config": {
                "name": scenario.config.name,
                "grid_width": scenario.config.grid_width,
                "grid_height": scenario.config.grid_height,
                "node_spacing": scenario.config.node_spacing,
                "num_agvs": len(scenario.agvs),
                "num_tasks": len(scenario.tasks),
                "num_conveyor_lines": scenario.config.num_conveyor_lines,
            },
            "nodes": [n.model_dump() for n in scenario.nodes],
            "edges": [e.model_dump(mode='json') for e in scenario.edges],
            "agvs": [a.model_dump(mode='json') for a in scenario.agvs],
            "tasks": [t.model_dump(mode='json') for t in scenario.tasks],
            "conveyor_segments": [s.model_dump(mode='json') for s in scenario.conveyor_segments],
            "conveyor_tasks": [t.model_dump(mode='json') for t in scenario.conveyor_tasks],
            "metadata": scenario.metadata,
        }

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2, default=str)

        results[name] = {
            "file": filename,
            "nodes": len(scenario.nodes),
            "edges": len(scenario.edges),
            "agvs": len(scenario.agvs),
            "tasks": len(scenario.tasks),
        }
        logger.info(f"Generated {name}: {filename} "
                     f"({len(scenario.nodes)} nodes, {len(scenario.edges)} edges)")

    # Save manifest
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "scenarios": results,
    }
    with open(f"{output_dir}/manifest.json", 'w') as f:
        json.dump(manifest, f, indent=2, default=str)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    output = sys.argv[1] if len(sys.argv) > 1 else "./benchmark_data"
    result = generate_all_scenarios(output)
    print(f"\nGenerated {len(result)} scenarios in {output}")
    for name, info in result.items():
        print(f"  {name}: {info['nodes']} nodes, {info['edges']} edges, "
              f"{info['agvs']} AGVs, {info['tasks']} tasks")
