"""
Seed database with demo data.

Run as:  python -m app.db.seed        # Insert if not exists
         python -m app.db.seed --reset # Drop & recreate

Extracted from MapService._init_demo_map and ScheduleService._init_demo_data.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from typing import List

# Ensure backend/ is on sys.path
import os
_parent = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from app.db.database import Base, get_engine, init_db, _check_db_available
from app.db.models import (
    AgvRecord,
    AlgorithmConfigRecord,
    ConveyorSegmentRecord,
    MapRecord,
    TaskRecord,
)
from app.models.schemas import (
    AgvStatus,
    AgvTask,
    ConveyorSegment,
    ConveyorTask,
    MapEdge,
    MapGraph,
    MapNode,
)
from sqlalchemy import delete, select, update

logger = logging.getLogger(__name__)


def build_demo_graph() -> MapGraph:
    """
    Build a complex factory layout supporting 15 AGVs + 10 conveyor stations.

    Layout (top-down view, X=horizontal, Y=vertical):
      Y=0:   [P00..P07] 8 pickup stations (top row)
      Y=10:  [M1_00..M1_07] midlane 1 (path nodes)
      Y=20:  [CV0..CV9] 10 conveyor work stations (main conveyor line)
      Y=25:  [CB0A,CB0B, CB1A,CB1B, ...] parallel buffer lines (5 pairs)
      Y=30:  [M2_00..M2_07] midlane 2 (path nodes)
      Y=40:  [D00..D07] 8 dropoff stations (bottom row)
      Y=-5:  [CH0..CH3] 4 charging stations
      X=-5:  [CE] conveyor entry
      X=85:  [CX] conveyor exit

    Total: ~90 nodes, ~120 edges, supports 15 AGVs and 10 conveyor stations
    with 5 parallel buffer lines.
    """
    nodes: List[MapNode] = []
    edges: List[MapEdge] = []

    # ---- Row 0 (Y=0): 8 Pickup stations ----
    for i in range(8):
        nodes.append(MapNode(
            id=f"N_P{i:02d}", name=f"取货点{i+1}",
            x=float(i * 12), y=0.0, type="pickup",
        ))

    # ---- Row 1 (Y=10): Midlane 1 (path nodes) ----
    for i in range(8):
        nodes.append(MapNode(
            id=f"N_M1_{i:02d}", name=f"路径1-{i+1}",
            x=float(i * 12), y=10.0, type="path",
        ))

    # ---- Row 2 (Y=20): 10 Conveyor work stations (main line) ----
    for i in range(10):
        nodes.append(MapNode(
            id=f"CV{i:02d}", name=f"输送工站{i+1}",
            x=float(i * 9 + 2), y=20.0, type="conveyor_in",
            conveyor_id=f"conv_main_{i:02d}",
        ))

    # ---- Row 2.5 (Y=25): 5 parallel buffer lines (pairs A/B) ----
    # Each buffer pair connects two adjacent conveyor stations with a bypass
    for i in range(5):
        # Buffer line A (upper bypass)
        nodes.append(MapNode(
            id=f"CB{i}A", name=f"缓存线{i+1}A",
            x=float(i * 18 + 6), y=25.0, type="conveyor_in",
            conveyor_id=f"conv_buf_{i}_A",
        ))
        # Buffer line B (lower bypass)
        nodes.append(MapNode(
            id=f"CB{i}B", name=f"缓存线{i+1}B",
            x=float(i * 18 + 12), y=25.0, type="conveyor_in",
            conveyor_id=f"conv_buf_{i}_B",
        ))

    # ---- Row 3 (Y=30): Midlane 2 (path nodes) ----
    for i in range(8):
        nodes.append(MapNode(
            id=f"N_M2_{i:02d}", name=f"路径2-{i+1}",
            x=float(i * 12), y=30.0, type="path",
        ))

    # ---- Row 4 (Y=40): 8 Dropoff stations ----
    for i in range(8):
        nodes.append(MapNode(
            id=f"N_D{i:02d}", name=f"卸货点{i+1}",
            x=float(i * 12), y=40.0, type="dropoff",
        ))

    # ---- Conveyor entry/exit ----
    nodes.append(MapNode(id="N_CE", name="输送线入口", x=-5.0, y=20.0, type="conveyor_in"))
    nodes.append(MapNode(id="N_CX", name="输送线出口", x=89.0, y=20.0, type="conveyor_out"))

    # ---- 4 Charging stations ----
    for i in range(4):
        nodes.append(MapNode(
            id=f"N_CH{i}", name=f"充电站{i+1}",
            x=float(i * 30 + 5), y=-5.0, type="charge",
        ))

    # ---- 4 Intersection nodes ----
    for i in range(4):
        nodes.append(MapNode(
            id=f"N_INT{i}", name=f"交叉口{i+1}",
            x=float(i * 30 + 10), y=15.0, type="cross",
        ))

    # ==== EDGES ====

    # Vertical: Pickup → Midlane1 → Intersection → Midlane2 → Dropoff
    for i in range(8):
        edges.append(MapEdge(
            from_node=f"N_P{i:02d}", to_node=f"N_M1_{i:02d}",
            distance=10.0, direction="bidirectional",
        ))
        edges.append(MapEdge(
            from_node=f"N_M1_{i:02d}", to_node=f"N_M2_{i:02d}",
            distance=20.0, direction="bidirectional",
        ))
        edges.append(MapEdge(
            from_node=f"N_M2_{i:02d}", to_node=f"N_D{i:02d}",
            distance=10.0, direction="bidirectional",
        ))

    # Horizontal: Pickup row
    for i in range(7):
        edges.append(MapEdge(
            from_node=f"N_P{i:02d}", to_node=f"N_P{i+1:02d}",
            distance=12.0, direction="bidirectional",
        ))
    # Horizontal: Midlane1
    for i in range(7):
        edges.append(MapEdge(
            from_node=f"N_M1_{i:02d}", to_node=f"N_M1_{i+1:02d}",
            distance=12.0, direction="bidirectional",
        ))
    # Horizontal: Midlane2
    for i in range(7):
        edges.append(MapEdge(
            from_node=f"N_M2_{i:02d}", to_node=f"N_M2_{i+1:02d}",
            distance=12.0, direction="bidirectional",
        ))
    # Horizontal: Dropoff row
    for i in range(7):
        edges.append(MapEdge(
            from_node=f"N_D{i:02d}", to_node=f"N_D{i+1:02d}",
            distance=12.0, direction="bidirectional",
        ))

    # Intersections → Midlane1 connections
    for i in range(4):
        idx = i * 2 + 1
        if idx < 8:
            edges.append(MapEdge(
                from_node=f"N_INT{i}", to_node=f"N_M1_{idx:02d}",
                distance=5.0, direction="bidirectional",
            ))

    # Horizontal: Intersections
    for i in range(3):
        edges.append(MapEdge(
            from_node=f"N_INT{i}", to_node=f"N_INT{i+1}",
            distance=30.0, direction="bidirectional",
        ))

    # ---- Conveyor main line: CE → CV00 → CV01 → ... → CV09 → CX ----
    edges.append(MapEdge(
        from_node="N_CE", to_node="CV00",
        distance=7.0, direction="forward", is_conveyor=True, speed_limit=0.8,
    ))
    for i in range(9):
        edges.append(MapEdge(
            from_node=f"CV{i:02d}", to_node=f"CV{i+1:02d}",
            distance=9.0, direction="forward", is_conveyor=True, speed_limit=0.8,
        ))
    edges.append(MapEdge(
        from_node="CV09", to_node="N_CX",
        distance=7.0, direction="forward", is_conveyor=True, speed_limit=0.8,
    ))

    # ---- Parallel buffer lines: CV(i*2) → CBiA → CBiB → CV(i*2+2) ----
    for i in range(5):
        start_idx = i * 2
        end_idx = i * 2 + 2
        if end_idx < 10:
            # Buffer A: forward bypass
            edges.append(MapEdge(
                from_node=f"CV{start_idx:02d}", to_node=f"CB{i}A",
                distance=6.0, direction="forward", is_conveyor=True, speed_limit=0.6,
            ))
            edges.append(MapEdge(
                from_node=f"CB{i}A", to_node=f"CV{end_idx:02d}",
                distance=6.0, direction="forward", is_conveyor=True, speed_limit=0.6,
            ))
            # Buffer B: reverse bypass (parallel)
            edges.append(MapEdge(
                from_node=f"CV{end_idx:02d}", to_node=f"CB{i}B",
                distance=6.0, direction="backward", is_conveyor=True, speed_limit=0.6,
            ))
            edges.append(MapEdge(
                from_node=f"CB{i}B", to_node=f"CV{start_idx:02d}",
                distance=6.0, direction="backward", is_conveyor=True, speed_limit=0.6,
            ))

    # ---- AGV access to conveyor stations (from Midlane1) ----
    for i in range(0, 10, 2):
        # Connect nearest midlane node to conveyor station
        ml_idx = min(i, 7)
        edges.append(MapEdge(
            from_node=f"N_M1_{ml_idx:02d}", to_node=f"CV{i:02d}",
            distance=10.0, direction="bidirectional",
        ))

    # ---- Charging station connections ----
    for i, ch in enumerate(["N_CH0", "N_CH1", "N_CH2", "N_CH3"]):
        target_p = min(i * 2, 7)
        edges.append(MapEdge(
            from_node=ch, to_node=f"N_P{target_p:02d}",
            distance=5.0, direction="bidirectional",
        ))

    return MapGraph(
        name="Complex Factory Layout (15 AGV + 10 Conveyor Stations)",
        version=2,
        nodes=nodes,
        edges=edges,
    )


def build_demo_agvs() -> List[AgvStatus]:
    """Build 15 demo AGVs positioned across the factory."""
    agvs = []
    # 8 AGVs at pickup stations
    pickup_nodes = [f"N_P{i:02d}" for i in range(8)]
    # 4 AGVs at midlane intersections
    midlane_nodes = [f"N_M1_{i:02d}" for i in [1, 3, 5, 7]]
    # 3 AGVs at dropoff area
    dropoff_nodes = [f"N_D{i:02d}" for i in [0, 2, 5]]

    positions = pickup_nodes + midlane_nodes + dropoff_nodes
    # XY coordinates matching the map
    pos_xy = {
        **{f"N_P{i:02d}": (float(i * 12), 0.0) for i in range(8)},
        **{f"N_M1_{i:02d}": (float(i * 12), 10.0) for i in range(8)},
        **{f"N_D{i:02d}": (float(i * 12), 40.0) for i in range(8)},
    }

    for i in range(15):
        node = positions[i]
        x, y = pos_xy.get(node, (0.0, 0.0))
        # Vary battery levels: some high, some medium, one low
        battery = [95, 88, 72, 100, 85, 60, 90, 78, 92, 65, 82, 55, 98, 70, 88][i]
        agvs.append(AgvStatus(
            id=f"AGV{i+1:03d}",
            name=f"AGV-{i+1}号",
            x=x, y=y,
            battery=float(battery),
            current_node=node,
            status="idle",
            speed=1.5,
            capacity=1,
        ))
    return agvs


def build_demo_tasks() -> List[AgvTask]:
    """Build 100 demo tasks of various types and priorities.

    Distribution:
      - 15 high priority (8-10): urgent production / conveyor handoff
      - 35 medium-high priority (6-7): standard production
      - 30 medium priority (4-5): regular transport
      - 20 low priority (1-3): batch / non-urgent

    Source/destination pools:
      - Pickups: 8 pickup stations (N_P00~N_P07) + 10 conveyor stations (CV00~CV09)
      - Dropoffs: 8 dropoff stations (N_D00~N_D07) + 10 conveyor stations (CV00~CV09)
    """
    import random

    tasks: List[AgvTask] = []
    now = datetime.now()

    pickup_pool = [f"N_P{i:02d}" for i in range(8)] + [f"CV{i:02d}" for i in range(10)]
    dropoff_pool = [f"N_D{i:02d}" for i in range(8)] + [f"CV{i:02d}" for i in range(10)]
    charge_nodes = [f"N_CH{i}" for i in range(4)]

    rng = random.Random(42)

    # Priority distribution: 15 high + 35 med-high + 30 med + 20 low = 100
    priorities = (
        [10] * 5 + [9] * 5 + [8] * 5 +          # 15 high (8-10)
        [7] * 18 + [6] * 17 +                     # 35 med-high (6-7)
        [5] * 15 + [4] * 15 +                     # 30 medium (4-5)
        [3] * 7 + [2] * 7 + [1] * 6               # 20 low (1-3)
    )
    rng.shuffle(priorities)

    used_pairs = set()
    cargo_types = ["standard", "electronics", "heavy_machinery", "pharmaceutical",
                   "food_grade", "hazardous", "fragile", "raw_material"]

    for i in range(100):
        tid = f"T{i+1:03d}"

        # Pick pickup/dropoff ensuring variety and no self-loops
        for _attempt in range(20):
            pickup = rng.choice(pickup_pool)
            dropoff = rng.choice(dropoff_pool)
            if pickup != dropoff and (pickup, dropoff) not in used_pairs:
                used_pairs.add((pickup, dropoff))
                break
        else:
            # Fallback if too many duplicates
            pickup = rng.choice(pickup_pool)
            dropoff = rng.choice([d for d in dropoff_pool if d != pickup])

        priority = priorities[i] if i < len(priorities) else rng.randint(3, 7)
        cargo_type = rng.choice(cargo_types)

        # Some tasks have deadlines (high priority ones)
        deadline = None
        if priority >= 8 and rng.random() < 0.6:
            deadline = now + timedelta(hours=rng.uniform(1, 4))
        elif priority >= 6 and rng.random() < 0.3:
            deadline = now + timedelta(hours=rng.uniform(4, 8))

        tasks.append(AgvTask(
            id=tid,
            pickup_node=pickup,
            dropoff_node=dropoff,
            priority=priority,
            create_time=now,
            deadline=deadline,
            cargo_type=cargo_type,
        ))

    return tasks


def build_demo_conveyors() -> List[ConveyorSegment]:
    """
    Build 10 conveyor work stations + 5 parallel buffer lines.

    Main line: CE → CV00 → CV01 → ... → CV09 → CX (10 stations)
    Buffer lines: 5 parallel bypass pairs (CB0A/B, CB1A/B, ..., CB4A/B)
    """
    segments = []

    # Main conveyor line segments (10 stations → 11 segments including entry/exit)
    segments.append(ConveyorSegment(
        id="conv_entry", from_node="N_CE", to_node="CV00",
        speed=0.8, length=7.0, name="输送线-入口段",
    ))
    for i in range(9):
        segments.append(ConveyorSegment(
            id=f"conv_main_{i:02d}", from_node=f"CV{i:02d}", to_node=f"CV{i+1:02d}",
            speed=0.8, length=9.0, name=f"输送线-工站{i+1}→{i+2}",
        ))
    segments.append(ConveyorSegment(
        id="conv_exit", from_node="CV09", to_node="N_CX",
        speed=0.8, length=7.0, name="输送线-出口段",
    ))

    # Parallel buffer lines (5 pairs, each bypasses 2 stations)
    for i in range(5):
        start_idx = i * 2
        end_idx = i * 2 + 2
        if end_idx < 10:
            # Buffer A (forward direction)
            segments.append(ConveyorSegment(
                id=f"conv_buf_{i}_A", from_node=f"CV{start_idx:02d}", to_node=f"CB{i}A",
                speed=0.6, length=6.0, name=f"缓存线{i+1}A-入",
                max_capacity=3,
            ))
            segments.append(ConveyorSegment(
                id=f"conv_buf_{i}_A2", from_node=f"CB{i}A", to_node=f"CV{end_idx:02d}",
                speed=0.6, length=6.0, name=f"缓存线{i+1}A-出",
                max_capacity=3,
            ))
            # Buffer B (reverse direction - parallel return)
            segments.append(ConveyorSegment(
                id=f"conv_buf_{i}_B", from_node=f"CV{end_idx:02d}", to_node=f"CB{i}B",
                speed=0.6, length=6.0, name=f"缓存线{i+1}B-入",
                max_capacity=3,
            ))
            segments.append(ConveyorSegment(
                id=f"conv_buf_{i}_B2", from_node=f"CB{i}B", to_node=f"CV{start_idx:02d}",
                speed=0.6, length=6.0, name=f"缓存线{i+1}B-出",
                max_capacity=3,
            ))

    return segments


async def seed(reset: bool = False) -> None:
    """Seed the database with demo data."""
    if not await _check_db_available():
        logger.warning("Database not available, skipping seed")
        return

    await init_db()
    from app.db.database import get_session_factory
    factory = get_session_factory()

    async with factory() as session:
        if reset:
            logger.info("Resetting database...")
            await session.execute(delete(ScheduleResultRecord))
            await session.execute(delete(TaskRecord))
            await session.execute(delete(AgvRecord))
            await session.execute(delete(ConveyorSegmentRecord))
            await session.execute(delete(MapRecord))
            await session.execute(delete(AlgorithmConfigRecord))
            await session.commit()

        # --- Map ---
        existing_map = await session.execute(select(MapRecord).where(MapRecord.is_active == True))
        if not existing_map.scalars().first():
            graph = build_demo_graph()
            record = MapRecord(
                name=graph.name,
                version=str(graph.version),
                graph_data=graph.model_dump(mode="json"),
                is_active=True,
            )
            session.add(record)
            logger.info("Seeded demo map: %d nodes, %d edges", len(graph.nodes), len(graph.edges))

        # --- AGVs ---
        existing_agvs = await session.execute(select(AgvRecord))
        if not existing_agvs.scalars().first():
            for agv in build_demo_agvs():
                session.add(AgvRecord(
                    id=agv.id,
                    name=agv.name,
                    vehicle_type="standard",
                    state=agv.model_dump(mode="json"),
                ))
            logger.info("Seeded 4 demo AGVs")

        # --- Tasks ---
        existing_tasks = await session.execute(select(TaskRecord))
        if not existing_tasks.scalars().first():
            for task in build_demo_tasks():
                session.add(TaskRecord(
                    id=task.id,
                    task_type="agv",
                    task_data=task.model_dump(mode="json"),
                    status=task.status,
                ))
            logger.info("Seeded 4 demo tasks")

        # --- Conveyors ---
        existing_convs = await session.execute(select(ConveyorSegmentRecord))
        if not existing_convs.scalars().first():
            for seg in build_demo_conveyors():
                session.add(ConveyorSegmentRecord(
                    id=seg.id,
                    segment_data=seg.model_dump(mode="json"),
                    from_node=seg.from_node,
                    to_node=seg.to_node,
                    speed=seg.speed,
                    length=seg.length,
                ))
            logger.info("Seeded 6 conveyor segments")

        # --- Algorithm Config ---
        existing_configs = await session.execute(select(AlgorithmConfigRecord))
        if not existing_configs.scalars().first():
            from app.models.schemas import AlgorithmConfig
            v1_config = AlgorithmConfig()
            v2_config = AlgorithmConfig()  # V2 reuses the same config structure
            session.add(AlgorithmConfigRecord(
                version="v1",
                config_data=v1_config.model_dump(mode="json"),
                is_active=False,
            ))
            session.add(AlgorithmConfigRecord(
                version="v2",
                config_data=v2_config.model_dump(mode="json"),
                is_active=True,
            ))
            logger.info("Seeded algorithm configs (v1 + v2, v2 active)")

        await session.commit()

    logger.info("✅ Seed complete")


def main():
    parser = argparse.ArgumentParser(description="Seed AGV-TMS database")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all data")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    main()
