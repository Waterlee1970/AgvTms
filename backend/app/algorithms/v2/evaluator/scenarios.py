"""
场景生成器 - AGV+TMS混合样本场景
=================================

支持场景: 仓库/工厂/港口/医院/越库/高压测试/边界情况/AGV+TMS混合
"""

from __future__ import annotations

import math
import random
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime


class ScenarioType(Enum):
    WAREHOUSE = "warehouse"
    FACTORY = "factory"
    PORT = "port"
    HOSPITAL = "hospital"
    CROSS_DOCKING = "cross_docking"
    STRESS_TEST = "stress_test"
    EDGE_CASE = "edge_case"
    MIXED_REALISTIC = "mixed"


class ScenarioDifficulty(Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXTREME = "extreme"


@dataclass
class ScenarioMetadata:
    scenario_id: str
    name: str
    scenario_type: ScenarioType
    difficulty: ScenarioDifficulty
    description: str = ""
    num_nodes: int = 0
    num_edges: int = 0
    num_agvs: int = 0
    num_tasks: int = 0
    expected_makespan_range: Tuple[float, float] = (0.0, float('inf'))
    tags: List[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['scenario_type'] = self.scenario_type.value
        d['difficulty'] = self.difficulty.value
        return d


@dataclass
class AGVTMS_Scenario:
    """完整的AGV-TMS混合场景"""
    metadata: ScenarioMetadata
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    agvs: List[Dict[str, Any]] = field(default_factory=list)
    conveyor_tasks: Optional[List[Dict]] = None
    conveyor_segments: Optional[List[Dict]] = None
    time_windows: Optional[List[Dict]] = None
    ground_truth: Optional[Dict] = None

    def to_dict(self):
        return {"metadata": self.metadata.to_dict(), "nodes": self.nodes, "edges": self.edges,
                "tasks": self.tasks, "agvs": self.agvs,
                "conveyor_tasks": self.conveyor_tasks, "conveyor_segments": self.conveyor_segments}

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data):
        md = data["metadata"]
        md["scenario_type"] = ScenarioType(md["scenario_type"])
        md["difficulty"] = ScenarioDifficulty(md["difficulty"])
        if isinstance(md.get("expected_makespan_range"), list):
            md["expected_makespan_range"] = tuple(md["expected_makespan_range"])
        return cls(metadata=ScenarioMetadata(**md), nodes=data.get("nodes", []),
                   edges=data.get("edges", []), tasks=data.get("tasks", []), agvs=data.get("agvs", []),
                   conveyor_tasks=data.get("conveyor_tasks"), conveyor_segments=data.get("conveyor_segments"))


class ScenarioGenerator:
    """参数化场景生成器"""

    PRESETS = {
        "small_warehouse": dict(grid_size=(8,12), num_agvs=5, num_tasks=15,
                                stype=ScenarioType.WAREHOUSE, diff=ScenarioDifficulty.EASY),
        "medium_warehouse": dict(grid_size=(15,20), num_agvs=15, num_tasks=40,
                                 stype=ScenarioType.WAREHOUSE, diff=ScenarioDifficulty.MEDIUM),
        "large_warehouse": dict(grid_size=(25,35), num_agvs=30, num_tasks=80,
                                stype=ScenarioType.WAREHOUSE, diff=ScenarioDifficulty.HARD),
        "factory_floor": dict(grid_size=(20,25), num_agvs=20, num_tasks=50,
                              stype=ScenarioType.FACTORY, diff=ScenarioDifficulty.MEDIUM, has_conveyor=True),
        "port_terminal": dict(grid_size=(30,40), num_agvs=25, num_tasks=60,
                              stype=ScenarioType.PORT, diff=ScenarioDifficulty.HARD),
        "hospital_logistics": dict(grid_size=(18,22), num_agvs=10, num_tasks=30,
                                   stype=ScenarioType.HOSPITAL, diff=ScenarioDifficulty.MEDIUM),
        "stress_test": dict(grid_size=(15,20), num_agvs=50, num_tasks=150,
                            stype=ScenarioType.STRESS_TEST, diff=ScenarioDifficulty.EXTREME),
        "mixed_agv_tms": dict(grid_size=(20,28), num_agvs=20, num_tasks=45,
                              num_ctasks=15, num_csegs=6,
                              stype=ScenarioType.MIXED_REALISTIC, diff=ScenarioDifficulty.MEDIUM,
                              has_conveyor=True),
    }

    def __init__(self, seed=None, **defaults):
        self.rng = random.Random(seed)
        self.defaults = defaults

    def generate(self, preset_name=None, **kwargs):
        params = self._resolve(preset_name, kwargs)
        stype = params.pop("stype", ScenarioType.WAREHOUSE)
        diff = params.pop("diff", ScenarioDifficulty.MEDIUM)

        gens = {ScenarioType.WAREHOUSE: self._gen_warehouse,
                ScenarioType.FACTORY: self._gen_factory,
                ScenarioType.PORT: self._gen_port,
                ScenarioType.HOSPITAL: self._gen_hospital,
                ScenarioType.CROSS_DOCKING: self._gen_crossdock,
                ScenarioType.STRESS_TEST: self._gen_stress,
                ScenarioType.EDGE_CASE: self._gen_edgecase,
                ScenarioType.MIXED_REALISTIC: self._gen_mixed}

        gen_func = gens.get(stype, self._gen_warehouse)
        scene = gen_func(**params)
        if params.get("has_conveyor") and not scene.conveyor_tasks:
            self._add_conveyor(scene,
                               num_ctasks=params.get("num_ctasks", 10),
                               num_csegs=params.get("num_csegs", 4))
        return scene

    def _resolve(self, preset, overrides):
        p = dict(self.defaults)
        if preset and preset in self.PRESETS:
            p.update(self.PRESETS[preset])
        p.update(overrides)
        return p

    # --- 辅助方法 ---

    def _create_grid(self, rows, cols):
        nodes = [{"id": f"N_{r:03d}_{c:03d}", "x": c*2.0, "y": r*2.0, "name": f"({r},{c})"}
                 for r in range(rows) for c in range(cols)]
        edges = []
        for r in range(rows):
            for c in range(cols):
                idx = r * cols + c
                if c < cols-1:
                    edges.append({"from": nodes[idx]["id"], "to": nodes[idx+1]["id"],
                                 "weight": 1.0, "id": f"E_{idx}_{idx+1}"})
                if r < rows-1:
                    edges.append({"from": nodes[idx]["id"], "to": nodes[idx+cols]["id"],
                                 "weight": 1.0, "id": f"E_{idx}_{idx+cols}"})
        return nodes, edges

    def _meta(self, name, stype, diff, nn, ne, na, nt, tags=None):
        return ScenarioMetadata(
            scenario_id=f"{stype.value}_{self.rng.randint(10000,99999)}",
            name=name, scenario_type=stype, difficulty=diff,
            num_nodes=nn, num_edges=ne, num_agvs=na, num_tasks=nt,
            tags=tags or [], created_at=datetime.now().isoformat())

    def _rnd_choice_other(self, lst, exclude):
        others = [x for x in lst if x != exclude]
        return self.rng.choice(others) if others else (lst[0] if lst else None)

    def _rnd_priority(self):
        return self.rng.choices([1,3,5,7,9], weights=[5,25,40,20,10])[0]

    # --- 各类型生成器 ---

    def _gen_warehouse(self, grid_size=(15,20), num_agvs=15, num_tasks=40, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)

        shelves = []; perims = []
        for n in nodes:
            r, c = divmod(int(n['y'])//2, 1) if False else (int(n['y'])//2, int(n['x'])//2)
            r, c = int(n['y']/2), int(n['x']/2) if isinstance(n['y'], (int,float)) else (0,0)
            # 简化：用索引位置
            idx = nodes.index(n); r, c = divmod(idx, cols)
            if r == 0 or r == rows-1 or c == 0 or c == cols-1:
                n["type"] = "perimeter"; perims.append(n)
            elif c % 3 == 0:
                n["type"] = "aisle"
            else:
                n["type"] = "shelf"; shelves.append(n)

        agvs = []
        for i in range(num_agvs):
            agvs.append({"id": f"AGV-{i+1:03d}", "current_node_id": self.rng.choice(nodes)["id"],
                        "battery_level": round(self.rng.uniform(50,100), 1),
                        "status": "idle", "capacity": 1.0})

        tasks = []
        dropoffs = perims or [n for n in nodes if n.get('x',0) >= cols*1.4] or nodes
        for i in range(num_tasks):
            src = self.rng.choice(shelves) if shelves else self.rng.choice(nodes)
            dst = self.rng.choice(dropoffs)
            tasks.append({"id": f"TASK-{i+1:04d}", "pickup_node_id": src["id"],
                         "dropoff_node_id": dst["id"], "priority": self._rnd_priority(),
                         "status": "pending", "estimated_duration": round(self.rng.uniform(30,180), 1)})

        return AGVTMS_Scenario(
            metadata=self._meta(f"Warehouse{rows}x{cols}", ScenarioType.WAREHOUSE,
                               kw.get('diff', ScenarioDifficulty.MEDIUM),
                               len(nodes), len(edges), num_agvs, num_tasks, ["grid_layout"]),
            nodes=nodes, edges=edges, tasks=tasks, agvs=agvs)

    def _gen_factory(self, grid_size=(20,25), num_agvs=20, num_tasks=50, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)
        workstations, buffers, charging = [], [], []

        for i, n in enumerate(nodes):
            idx = i; r, c = divmod(idx, cols)
            if 2 <= r <= rows//3 and 2 <= c <= cols//3:
                n["type"] = "workstation"; workstations.append(n)
            elif rows*2//3 <= r <= rows-3 and c >= cols*2//3:
                n["type"] = "buffer"; buffers.append(n)
            elif r == 0 and c % 5 == 0:
                n["type"] = "charging"; charging.append(n)
            else:
                n["type"] = "corridor"

        starts = (charging + workstations) or [nodes[0]]
        agvs = [{"id": f"AGV-{i+1:03d}", "current_node_id": self.rng.choice(starts)["id"],
                 "battery_level": round(self.rng.uniform(40,95), 1),
                 "status": "idle", "capacity": round(self.rng.uniform(0.5,2.0), 1)}
                for i in range(num_agvs)]

        tasks = []
        for i in range(num_tasks):
            if i < num_tasks*0.7 and len(workstations) >= 2:
                s, d = self.rng.choice(workstations), self._rnd_choice_other(workstations, self.rng.choice(workstations))
                tt, pri = "transfer", 5
            else:
                s = self.rng.choice(workstations) if workstations else self.rng.choice(nodes)
                d = self.rng.choice(buffers) if buffers else self.rng.choice(nodes)
                tt, pri = "delivery", 7
            tasks.append({"id": f"TASK-{i+1:04d}", "pickup_node_id": s["id"],
                         "dropoff_node_id": d["id"], "priority": pri,
                         "status": "pending", "task_type": tt,
                         "estimated_duration": round(self.rng.uniform(20,120), 1)})

        return AGVTMS_Scenario(
            metadata=self._meta(f"Factory{rows}x{cols}", ScenarioType.FACTORY,
                               kw.get('diff', ScenarioDifficulty.MEDIUM),
                               len(nodes), len(edges), num_agvs, num_tasks, ["workstation"]),
            nodes=nodes, edges=edges, tasks=tasks, agvs=agvs)

    def _gen_port(self, grid_size=(30,40), num_agvs=25, num_tasks=60, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)
        quay, yard, gate = [], [], []

        for i, n in enumerate(nodes):
            r, c = divmod(i, cols)
            if r <= 2:
                n["type"] = "quay_crane"; quay.append(n)
            elif rows//4 <= r <= rows*3//4 and cols//4 <= c <= cols*3//4:
                n["type"] = "yard_block"; yard.append(n)
            elif r >= rows-3:
                n["type"] = "gate"; gate.append(n)
            else:
                n["type"] = "roadway"

        starts = quay[:max(1,len(quay))] + yard[:max(1,len(yard))] or [nodes[0]]
        agvs = [{"id": f"AGV-{i+1:03d}", "current_node_id": self.rng.choice(starts)["id"],
                 "battery_level": round(self.rng.uniform(60,98), 1),
                 "status": "idle", "capacity": 1.0, "speed": round(self.rng.uniform(2.0,4.0), 2)}
                for i in range(num_agvs)]

        tasks = []
        for i in range(num_tasks):
            rv = self.rng.random()
            if rv < 0.4 and quay and yard:
                s, d, tt = self.rng.choice(quay), self.rng.choice(yard), "discharge"
            elif rv < 0.75 and yard and quay:
                s, d, tt = self.rng.choice(yard), self.rng.choice(quay), "load"
            elif yard and gate:
                s, d, tt = self.rng.choice(yard), self.rng.choice(gate), "gate_pickup"
            else:
                s, d, tt = self.rng.choice(nodes), self.rng.choice(nodes), "transfer"
            tasks.append({"id": f"TASK-{i+1:04d}", "pickup_node_id": s["id"],
                         "dropoff_node_id": d["id"], "priority": self._rnd_priority(),
                         "status": "pending", "task_type": tt,
                         "container_id": f"CNTR-{self.rng.randint(100000,999999)}",
                         "estimated_duration": round(self.rng.uniform(60,300), 1)})

        return AGVTMS_Scenario(
            metadata=self._meta(f"Port{rows}x{cols}", ScenarioType.PORT,
                               kw.get('diff', ScenarioDifficulty.HARD),
                               len(nodes), len(edges), num_agvs, num_tasks, ["container"]),
            nodes=nodes, edges=edges, tasks=tasks, agvs=agvs)

    def _gen_hospital(self, grid_size=(18,22), num_agvs=10, num_tasks=30, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)
        pharm, wards, lab, surg, supply = [], [], [], [], []

        for i, n in enumerate(nodes):
            r, c = divmod(i, cols)
            if 1<=r<=3 and 1<=c<=4:
                n["type"]="pharmacy"; pharm.append(n)
            elif r<=5 and c>=cols-5:
                n["type"]="laboratory"; lab.append(n)
            elif rows//3<=r<=rows*2//3 and 1<=c<=5:
                n["type"]="ward"; wards.append(n)
            elif r>=rows-4 and cols//3<=c<=cols*2//3:
                n["type"]="surgery"; surg.append(n)
            elif r==0 and c==cols//2:
                n["type"]="supply_center"; supply.append(n)
            else:
                n["type"]="corridor"

        start = supply[0] if supply else nodes[0]
        agvs = [{"id":f"AGV-{i+1:03d}", "current_node_id": start["id"],
                 "battery_level":round(self.rng.uniform(70,100),1), "status":"idle",
                 "capacity":1.0, "speed":1.0} for i in range(num_agvs)]

        ttypes = ["medicine","specimen","supply","meal"]
        tsrcs = {"medicine":(pharm,wards), "specimen":(wards,lab),
                 "supply":(supply,wards+surg), "meal":(supply,wards)}
        twgts = [0.35,0.25,0.25,0.15]

        tasks = []
        for i in range(num_tasks):
            tt = self.rng.choices(ttypes, weights=twgts)[0]
            ss, ds = tsrcs.get(tt, (nodes,nodes))
            s = self.rng.choice(ss) if ss else self.rng.choice(nodes)
            d = self.rng.choice(ds) if ds else self.rng.choice(nodes)
            urgent = (tt=="specimen") or (tt=="medicine" and self.rng.random()<0.2)
            tasks.append({"id":f"TASK-{i+1:04d}","pickup_node_id":s["id"],
                         "dropoff_node_id":d["id"],"priority":9 if urgent else self._rnd_priority(),
                         "status":"pending","task_type":tt,"is_urgent":urgent,
                         "estimated_duration":round(self.rng.uniform(10,60) if urgent else self.rng.uniform(30,90),1)})

        return AGVTMS_Scenario(
            metadata=self._meta(f"Hospital{rows}x{cols}", ScenarioType.HOSPITAL,
                               kw.get('diff', ScenarioDifficulty.MEDIUM),
                               len(nodes),len(edges),num_agvs,num_tasks, ["urgent","quiet"]),
            nodes=nodes,edges=edges,tasks=tasks,agvs=agvs)

    def _gen_crossdock(self, grid_size=(16,24), num_agvs=12, num_tasks=35, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)
        recv, ship, sort_a, stage = [], [], [], []

        for i, n in enumerate(nodes):
            r, c = divmod(i, cols)
            if r==0 and c%3==0:
                n["type"]="receiving_door"; recv.append(n)
            elif r==rows-1 and c%3==1:
                n["type"]="shipping_door"; ship.append(n)
            elif rows//3 <= r <= rows*2//3:
                if c <= cols//2:
                    n["type"]="sortation"; sort_a.append(n)
                else:
                    n["type"]="staging"; stage.append(n)
            else:
                n["type"]="conveyor_path"

        agvs = [{"id":f"AGV-{i+1:03d}","current_node_id":self.rng.choice(recv if recv else nodes)["id"],
                 "battery_level":round(self.rng.uniform(55,95),1),"status":"idle",
                 "capacity":1.0,"speed":round(self.rng.uniform(1.5,2.5),2)}
                for i in range(num_agvs)]

        tasks = [{"id":f"TASK-{i+1:04d}","pickup_node_id":self.rng.choice(recv if recv else nodes)["id"],
                  "dropoff_node_id":self.rng.choice(ship if ship else nodes)["id"],
                  "priority":self._rnd_priority(),"status":"pending",
                  "deadline_minutes":round(self.rng.uniform(15,120),1)}
                 for i in range(num_tasks)]

        return AGVTMS_Scenario(
            metadata=self._meta(f"CrossDock{rows}x{cols}", ScenarioType.CROSS_DOCKING,
                               kw.get('diff', ScenarioDifficulty.MEDIUM),
                               len(nodes),len(edges),num_agvs,num_tasks, ["just_in_time"]),
            nodes=nodes,edges=edges,tasks=tasks,agvs=agvs)

    def _gen_stress(self, grid_size=(15,20), num_agvs=50, num_tasks=150, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)
        for n in nodes: n["type"] = "floor"

        agvs = [{"id":f"AGV-{i+1:03d}","current_node_id":self.rng.choice(nodes)["id"],
                 "battery_level":round(self.rng.uniform(30,100),1),
                 "status":self.rng.choices(["idle","busy","charging"],weights=[70,20,10])[0],
                 "capacity":1.0} for i in range(num_agvs)]

        tasks = [{"id":f"TASK-{i+1:04d}",
                  "pickup_node_id":self.rng.choice(nodes)["id"],
                  "dropoff_node_id":self.rng.choice(nodes)["id"],
                  "priority":self._rnd_priority(),"status":"pending"}
                 for i in range(num_tasks)]

        return AGVTMS_Scenario(
            metadata=self._meta(f"StressTest{num_agvs}AGVs", ScenarioType.STRESS_TEST,
                               ScenarioDifficulty.EXTREME, len(nodes),len(edges),
                               num_agvs,num_tasks,["high_load","congestion"]),
            nodes=nodes,edges=edges,tasks=tasks,agvs=agvs)

    def _gen_edgecase(self, grid_size=(10,12), num_agvs=6, num_tasks=12, **kw):
        rows, cols = grid_size
        nodes, edges = self._create_grid(rows, cols)

        # 制造瓶颈：只有一条窄通道连接两个区域
        bottleneck_node = None
        for n in nodes:
            idx = nodes.index(n); r, c = divmod(idx, cols)
            if r == rows//2 and c == cols//2:
                n["type"] = "bottleneck"; bottleneck_node = n
            elif r < rows//2:
                n["type"] = "zone_a"
            else:
                n["type"] = "zone_b"

        # 所有AGV在zone A，所有任务目标在zone B（强制通过瓶颈）
        zone_a = [n for n in nodes if n.get("type")=="zone_a"]
        zone_b = [n for n in nodes if n.get("type")=="zone_b"]

        agvs = [{"id":f"AGV-{i+1:03d}","current_node_id":self.rng.choice(zone_a if zone_a else nodes)["id"],
                 "battery_level":80,"status":"idle","capacity":1.0}
                for i in range(num_agvs)]

        tasks = [{"id":f"TASK-{i+1:04d}",
                  "pickup_node_id":self.rng.choice(zone_a if zone_a else nodes)["id"],
                  "dropoff_node_id":self.rng.choice(zone_b if zone_b else nodes)["id"],
                  "priority":7,"status":"pending"} for i in range(num_tasks)]

        # 一个AGV故障
        if agvs:
            agvs[0]["status"] = "fault"
            agvs[0]["battery_level"] = 5

        return AGVTMS_Scenario(
            metadata=self._meta("EdgeCase-Bottleneck+Fault", ScenarioType.EDGE_CASE,
                               ScenarioDifficulty.MEDIUM, len(nodes),len(edges),
                               num_agvs,num_tasks,["bottleneck","fault_recovery"]),
            nodes=nodes,edges=edges,tasks=tasks,agvs=agvs)

    def _gen_mixed(self, grid_size=(20,28), num_agvs=20, num_tasks=45,
                   num_ctasks=15, num_csegs=6, **kw):
        """AGV + TMS 混合场景：AGV负责物料搬运，TMS(输送线)负责批量流转"""
        base = self._gen_factory(grid_size, num_agvs, num_tasks, **kw)
        return base  # conveyor will be added by caller via _add_conveyor

    def _add_conveyor(self, scenario, num_ctasks=10, num_csegs=4):
        """为场景添加输送线组件"""
        if not scenario.nodes:
            return
        # 在地图上选择一些节点作为输送线路段（取中间三分之一节点）
        all_sorted = sorted(scenario.nodes, key=lambda n: n.get('x',0) + n.get('y',0))
        mid_start, mid_end = len(all_sorted)//3, 2*len(all_sorted)//3
        mid_nodes = all_sorted[mid_start:mid_end]
        seg_nodes = mid_nodes[::max(1, len(mid_nodes)//num_csegs)][:num_csegs]

        scenario.conveyor_segments = []
        for i, node in enumerate(seg_nodes):
            seg = {"id": f"CONV_SEG_{i+1}", "node_id": node["id"],
                   "speed_mps": round(self.rng.uniform(0.3, 0.8), 2),
                   "capacity": self.rnd_int(5, 15), "status": "running"}
            scenario.conveyor_segments.append(seg)

        scenario.conveyor_tasks = []
        for i in range(num_ctasks):
            src_seg = self.rng.choice(scenario.conveyor_segments)
            dst_seg = self._rnd_choice_other(scenario.conveyor_segments, src_seg)
            scenario.conveyor_tasks.append({
                "id": f"CTASK-{i+1:04d}",
                "from_segment_id": src_seg["id"],
                "to_segment_id": dst_seg["id"],
                "quantity": self.rnd_int(1, 10),
                "priority": self._rnd_priority(),
                "status": "pending",
                "item_type": self.rng.choice(["box","pallet","bin"]),
            })

    def rnd_int(self, lo, hi):
        return self.rng.randint(lo, hi)


# ==================== 批量生成 ====================

def generate_scenario_suite(preset_names=None, seed=42, count_per_type=3) -> List[AGVTMS_Scenario]:
    """
    生成一套完整的多类型场景集

    Args:
        preset_names: 要生成的预设名称列表，None表示全部
        seed: 基础种子
        count_per_type: 每种类型生成的变体数量

    Returns:
        场景列表
    """
    generator = ScenarioGenerator(seed=seed)
    presets = preset_names or list(ScenarioGenerator.PRESETS.keys())
    scenarios = []

    for pname in presets:
        for i in range(count_per_type):
            gen = ScenarioGenerator(seed=seed + i * 1000 + hash(pname) % 10000)
            try:
                scene = gen.generate(preset_name=pname)
                scenarios.append(scene)
            except Exception as e:
                print(f"[WARN] Failed to generate {pname} variant {i}: {e}")

    return scenarios
