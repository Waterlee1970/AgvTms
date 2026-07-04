"""
AGV 场景数据生成器 — 用于 Locust 压测和大规模模拟

用法:
  python generate_agv_scenario.py --help
  python generate_agv_scenario.py --preset medium
  python generate_agv_scenario.py --size 100 --seed 42 --output agvs_100.json
  python generate_agv_scenario.py --preset large --inject-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ==================== 数据模型 ====================

@dataclass
class AgvConfig:
    """AGV 配置模板"""
    agv_type: str            # 车型
    payload_kg: float        # 额定载重 kg
    speed_max_mps: float     # 最大速度 m/s
    battery_wh: int          # 电池容量 Wh
    charge_power_w: int      # 充电功率 W
    length_mm: float         # 车长 mm
    width_mm: float          # 车宽 mm
    turn_radius_m: float     # 转弯半径 m


@dataclass
class AgvData:
    """单个 AGV 完整数据"""
    id: str
    name: str
    type: str
    x: float                # 初始X坐标 m
    y: float                # 初始Y坐标 m
    angle: float            # 初始朝向 deg
    speed: float            # 当前速度 m/s
    battery_level: float    # 电量百分比
    state: str              # 当前状态
    payload_current_kg: float  # 当前载重 kg
    is_charging: bool       # 是否在充电
    current_task_id: Optional[str] = None
    error_code: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ==================== AGV 类型预设 ====================

AGV_TYPES: Dict[str, AgvConfig] = {
    "forklift": AgvConfig(
        agv_type="forklift", payload_kg=1500.0, speed_max_mps=2.0,
        battery_wh=400, charge_power_w=200,
        length_mm=2500, width_mm=1000, turn_radius_m=1.5,
    ),
    "pallet": AgvConfig(
        agv_type="pallet", payload_kg=800.0, speed_max_mps=1.5,
        battery_wh=300, charge_power_w=150,
        length_mm=1800, width_mm=800, turn_radius_m=1.0,
    ),
    "conveyor": AgvConfig(
        agv_type="conveyor", payload_kg=300.0, speed_max_mps=1.0,
        battery_wh=200, charge_power_w=100,
        length_mm=1200, width_mm=600, turn_radius_m=0.6,
    ),
    "heavy": AgvConfig(
        agv_type="heavy", payload_kg=3000.0, speed_max_mps=1.2,
        battery_wh=600, charge_power_w=300,
        length_mm=3500, width_mm=1400, turn_radius_m=2.0,
    ),
    "mini": AgvConfig(
        agv_type="mini", payload_kg=50.0, speed_max_mps=2.5,
        battery_wh=100, charge_power_w=50,
        length_mm=600, width_mm=400, turn_radius_m=0.4,
    ),
}

# 状态分布权重 (基于真实场景)
STATE_WEIGHTS = [
    ("idle", 0.25),
    ("moving", 0.35),
    ("loading", 0.08),
    ("unloading", 0.07),
    ("charging", 0.15),
    ("executing", 0.08),
    ("error", 0.02),  # 少量故障模拟
]


# ==================== 规模预设 ====================

SIZE_PRESETS: Dict[str, Dict[str, Any]] = {
    "small": {"agvs": 10, "map_width": 80, "map_height": 60},
    "medium": {"agvs": 50, "map_width": 200, "map_height": 150},
    "large": {"agvs": 100, "map_width": 300, "map_height": 220},
    "xlarge": {"agvs": 200, "map_width": 500, "map_height": 380},
    "stress": {"agvs": 500, "map_width": 800, "map_height": 600},
}


# ==================== 核心生成函数 ====================

def generate_agv_fleet(
    num_agvs: int,
    map_width: float = 200.0,
    map_height: float = 150.0,
    seed: Optional[int] = None,
) -> List[AgvData]:
    """
    生成 AGV 车队配置.
    
    Args:
        num_agvs: AGV 数量
        map_width: 地图宽度(米)
        map_height: 地图高度(米)
        seed: 随机种子
    
    Returns:
        AgvData 列表
    """
    if seed is not None:
        random.seed(seed)
    
    fleet = []
    type_list = list(AGV_TYPES.keys())
    # 混合车型权重: pallet最多, forklift次之
    type_weights = [0.35, 0.30, 0.15, 0.12, 0.08]  # pallet/forklift/conveyor/heavy/mini
    
    for i in range(1, num_agvs + 1):
        agv_type = random.choices(type_list, weights=type_weights)[0]
        config = AGV_TYPES[agv_type]
        
        # 状态按权重随机选择
        state_choices, state_ws = zip(*STATE_WEIGHTS)
        state = random.choices(state_choices, weights=state_ws)[0]
        
        # 电量根据状态调整
        if state == "charging":
            battery = round(random.uniform(15, 40), 1)
        elif state == "error":
            battery = round(random.uniform(5, 20), 1)
        else:
            battery = round(random.triangular(35, 100, 85), 1)
        
        agv = AgvData(
            id=f"agv_{i:04d}",
            name=f"AGV-{i:04d}",
            type=agv_type,
            x=round(random.uniform(5, map_width - 5), 2),
            y=round(random.uniform(5, map_height - 5), 2),
            angle=round(random.uniform(-180, 180), 1),
            speed=round(random.uniform(0, config.speed_max_mps * 0.8), 2),
            battery_level=battery,
            state=state,
            payload_current_kg=round(random.uniform(0, config.payload_kg * 0.7), 1),
            is_charging=(state == "charging"),
        )
        
        fleet.append(agv)
    
    return fleet


def calculate_fleet_statistics(fleet: List[AgvData]) -> Dict[str, Any]:
    """计算车队统计数据"""
    if not fleet:
        return {}
    
    from collections import Counter
    
    total = len(fleet)
    type_counts = Counter(a.type for a in fleet)
    state_counts = Counter(a.state for a in fleet)
    
    avg_battery = sum(a.battery_level for a in fleet) / total
    avg_speed = sum(a.speed for a in fleet) / total
    moving_count = sum(1 for a in fleet if a.state == "moving")
    
    return {
        "total_agvs": total,
        "type_distribution": dict(type_counts),
        "state_distribution": dict(state_counts),
        "avg_battery_pct": round(avg_battery, 1),
        "avg_speed_mps": round(avg_speed, 2),
        "moving_count": moving_count,
        "utilization_pct": round(moving_count / total * 100, 1),
        "error_count": state_counts.get("error", 0),
    }


def inject_to_api(fleet: List[AgvData], base_url: str) -> bool:
    """通过 API 注入 AGV 数据到运行中的服务"""
    try:
        import requests
        
        url = f"{base_url.rstrip('/')}/api/v1/agvs/batch"
        
        payload = {
            "agvs": [a.to_dict() for a in fleet],
            "mode": "upsert",
        }
        
        resp = requests.post(url, json=payload, timeout=30)
        print(f"  API inject: {resp.status_code} - {resp.text[:200]}")
        return 200 <= resp.status_code < 300
        
    except ImportError:
        print("  [WARN] requests not available, skipping API injection")
        return False
    except Exception as e:
        print(f"  [WARN] API injection failed: {e}")
        return False


# ==================== CLI 入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="Generate AGV scenario data for benchmarking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list-presets
  %(prog)s --preset medium
  %(prog)s --size 100 --output my_scenario.json
  %(prog)s --preset large --inject-url http://localhost:8000
""",
    )
    
    parser.add_argument("--size", "-n", type=int, help="Number of AGVs to generate")
    parser.add_argument("--preset", "-p", choices=list(SIZE_PRESETS.keys()),
                        help="Use predefined size preset")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--inject-url", help="Also inject data to running API at URL")
    parser.add_argument("--list-presets", action="store_true", help="List all presets and exit")
    parser.add_argument("--width", type=float, default=None, help="Map width (meters)")
    parser.add_argument("--height", type=float, default=None, help="Map height (meters)")
    
    args = parser.parse_args()
    
    if args.list_presets:
        print("\nPreset configurations:")
        print("-" * 70)
        print(f"{'Preset':<12} {'AGVs':<8} {'Map(m)':<16} Description")
        print("-" * 70)
        for name, cfg in SIZE_PRESETS.items():
            desc_map = {
                "small": "Quick smoke test",
                "medium": "Standard baseline",
                "large": "Load test",
                "xlarge": "Stress test",
                "stress": "Extreme pressure",
            }
            print(f"{name:<12} {cfg['agvs']:<8} {cfg['map_width']}x{cfg['map_height']:<9} {desc_map.get(name, '')}")
        print()
        return 0
    
    # 确定规模参数
    if args.preset and args.size:
        print("[WARN] Both --preset and --size given, using size value")
        num = args.size
        w = args.width or 200.0
        h = args.height or 150.0
    elif args.preset:
        preset_cfg = SIZE_PRESETS[args.preset]
        num = preset_cfg["agvs"]
        w = args.width or preset_cfg["map_width"]
        h = args.height or preset_cfg["map_height"]
    elif args.size:
        num = args.size
        w = args.width or 200.0
        h = args.height or 150.0
    else:
        # 默认使用 medium
        preset_cfg = SIZE_PRESETS["medium"]
        num = preset_cfg["agvs"]
        w = preset_cfg["map_width"]
        h = preset_cfg["map_height"]
    
    # 输出路径
    output_file = args.output or f"agv_scenario_{num}.json"
    
    print(f"\nGenerating AGV scenario:")
    print(f"  Count:   {num}")
    print(f"  Map:     {w}m x {h}m")
    print(f"  Seed:    {args.seed}")
    print(f"  Output:  {output_file}")
    
    # 生成
    fleet = generate_agv_fleet(num, w, h, seed=args.seed)
    stats = calculate_fleet_statistics(fleet)
    
    # 组装输出
    result = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "2.3",
            "generator": "generate_agv_scenario.py",
            "seed": args.seed,
            "config": {"num_agvs": num, "map_width_m": w, "map_height_m": h},
        },
        "statistics": stats,
        "agvs": [a.to_dict() for a in fleet],
    }
    
    # 写入文件
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Generated {num} AGVs → {output_file}")
    print(f"\nStatistics:")
    for k, v in stats.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for sk, sv in v.items():
                print(f"    {sk}: {sv}")
        else:
            print(f"  {k}: {v}")
    
    # API 注入
    if args.inject_url:
        print(f"\nInjecting to {args.inject_url}...")
        success = inject_to_api(fleet, args.inject_url)
        if success:
            print("✓ Injection successful")
        else:
            print("✗ Injection failed")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
