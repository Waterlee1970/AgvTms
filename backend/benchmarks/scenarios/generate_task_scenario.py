"""
任务场景数据生成器 — 用于 Locust 压测和大规模模拟

用法:
  python generate_task_scenario.py --help
  python generate_task_scenario.py --size-preset medium
  python generate_task_scenario.py --tasks 500 --ratio 0.3 --output tasks_500.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ==================== 枚举和数据模型 ====================

class TaskStatus(str, Enum):
    """任务状态枚举 — 基于真实业务比例"""
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class TaskData:
    """任务数据"""
    id: str
    priority: TaskPriority
    status: TaskStatus
    pickup_point: str
    dropoff_point: str
    cargo_type: str
    cargo_weight_kg: float
    assigned_agv_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    urgent: bool = False
    
    # 时间字段（用于分析）
    assigned_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "priority": self.priority.value,
            "status": self.status.value,
            "pickup_point": self.pickup_point,
            "dropoff_point": self.dropoff_point,
            "cargo_type": self.cargo_type,
            "cargo_weight_kg": self.cargo_weight_kg,
            "urgent": self.urgent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.assigned_agv_id:
            d["assigned_agv_id"] = self.assigned_agv_id
        return d


# ==================== 业务常量 ====================

# 取货点池 (模拟仓库布局)
PICKUP_POINTS = [
    "RECV-DOCK-A", "RECV-DOCK-B",          # 收货码头
    "STAGING-A1", "STAGING-A2", "STAGING-B1", "STAGING-B2",  # 暂存区
    "RACK-HIGH-A01", "RACK-HIGH-A02",       # 高位货架取货
    "RACK-HIGH-B01", "RACK-HIGH-B02",
    "INSPECTION-IN",                       # 质检入口
    "BUFFER-LINE-1", "BUFFER-LINE-2",      # 线边缓冲区
]

# 卸货点池
DROPOFF_POINTS = [
    "SHIP-DOCK-1", "SHIP-DOCK-2",          # 发货码头
    "LINE-WORKCELL-A", "LINE-WORKCELL-B",  # 工位上料点
    "DISPATCH-STAGING-1", "DISPATCH-STAGING-2",  # 分拣暂存
    "QUALITY-CHECK",                       # 质检站
    "PACKING-A", "PACKING-B",              # 打包区
    "CHARGING-ZONE-A",                     # 充电对接区
    "REWORK-AREA",                         # 返工区
    "SCRAP-BIN",                           # 废品区
    "STORAGE-FINAL-A01", "STORAGE-FINAL-B01",  # 成品存储
]

# 货物类型及典型重量范围(kg)
CARGO_TYPES: Dict[str, tuple] = {
    "box_small": (1, 10),
    "box_large": (10, 50),
    "pallet": (200, 1200),
    "cylinder": (20, 100),
    "bag": (5, 30),
    "fragile": (2, 15),
}

# 任务状态真实分布 (基于电力计量仓储场景)
STATUS_DISTRIBUTION: List[tuple] = [
    (TaskStatus.PENDING, 0.38),
    (TaskStatus.ASSIGNED, 0.18),
    (TaskStatus.IN_PROGRESS, 0.19),
    (TaskStatus.COMPLETED, 0.17),
    (TaskStatus.FAILED, 0.05),
    (TaskStatus.CANCELLED, 0.03),
]

# 优先级分布
PRIORITY_DISTRIBUTION: List[tuple] = [
    (TaskPriority.LOW, 0.15),
    (TaskPriority.NORMAL, 0.45),
    (TaskPriority.HIGH, 0.28),
    (TaskPriority.URGENT, 0.12),
]

# AGV ID 范围（用于分配）
AGV_ID_RANGE = range(1, 101)


# ==================== 规模预设 ====================

TASK_SIZE_PRESETS: Dict[str, int] = {
    "small": 50,
    "medium": 200,
    "large": 500,
    "xlarge": 1000,
}


# ==================== 核心生成函数 ====================

def generate_tasks(
    num_tasks: int,
    high_priority_ratio: float = 0.4,
    pending_only: bool = False,
    seed: Optional[int] = None,
) -> List[TaskData]:
    """
    生成任务列表.
    
    Args:
        num_tasks: 任务数量
        high_priority_ratio: 高优先级任务比例
        pending_only: 是否仅生成待处理状态的任务
        seed: 随机种子
    """
    if seed is not None:
        random.seed(seed)
    
    tasks: List[TaskData] = []
    base_time = datetime.now(timezone.utc)
    
    # 可用AGV列表（部分AGV可能离线）
    active_agvs = [f"agv_{i:04d}" for i in range(1, min(num_tasks // 3 + 5, 51))]
    offline_agvs = [f"agv_{i:04d}" for i in range(51, 61)]
    all_agvs = active_agvs + offline_agvs
    
    now_ts = base_time.isoformat()
    
    for i in range(1, num_tasks + 1):
        # 优先级
        pri_choices, pri_ws = zip(*PRIORITY_DISTRIBUTION)
        priority = random.choices(pri_choices, weights=pri_ws)[0]
        
        # 状态
        if pending_only:
            status = TaskStatus.PENDING
        else:
            st_choices, st_ws = zip(*STATUS_DISTRIBUTION)
            status = random.choices(st_choices, weights=st_ws)[0]
        
        # 货物类型与重量
        cargo_type, (w_min, w_max) = random.choice(list(CARGO_TYPES.items()))
        cargo_weight = round(random.uniform(w_min, w_max), 1)
        
        # 起止点（避免相同）
        pickup = random.choice(PICKUP_POINTS)
        dropoff = random.choice(DROPOFF_POINTS)
        while dropoff == pickup and len(DROPOFF_POINTS) > 1:
            dropoff = random.choice(DROPOFF_POINTS)
        
        # 时间戳
        created_offset = random.randint(-7200, 0)  # 过去2小时内创建
        created_at = base_time.replace(second=random.randint(0, 59)).isoformat()
        updated_at = base_time.isoformat()
        
        task = TaskData(
            id=f"task_{i:06d}",
            priority=priority,
            status=status,
            pickup_point=pickup,
            dropoff_point=dropoff,
            cargo_type=cargo_type,
            cargo_weight_kg=cargo_weight,
            urgent=(priority == TaskPriority.URGENT),
            created_at=created_at,
            updated_at=updated_at,
        )
        
        # 根据状态补充额外信息
        if status != TaskStatus.PENDING:
            task.assigned_agv_id = random.choice(active_agvs)
            task.assigned_at = base_time.isoformat()
            
            if status in (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED, TaskStatus.FAILED):
                task.started_at = base_time.isoformat()
            
            if status == TaskStatus.COMPLETED:
                task.completed_at = base_time.isoformat()
        
        tasks.append(task)
    
    return tasks


def calculate_task_stats(tasks: List[TaskData]) -> Dict[str, Any]:
    """计算任务统计数据"""
    from collections import Counter
    
    total = len(tasks)
    if total == 0:
        return {}
    
    status_counts = Counter(t.status.value for t in tasks)
    priority_counts = Counter(t.priority.value for t in tasks)
    cargo_counts = Counter(t.cargo_type for t in tasks)
    pickup_counts = Counter(t.pickup_point for t in tasks)
    
    assigned_count = sum(1 for t in tasks if t.assigned_agv_id)
    urgent_count = sum(1 for t in tasks if t.urgent)
    avg_weight = sum(t.cargo_weight_kg for t in tasks) / total
    total_weight = sum(t.cargo_weight_kg for t in tasks)
    
    return {
        "total_tasks": total,
        "status_distribution": dict(status_counts),
        "priority_distribution": dict(priority_counts),
        "cargo_type_distribution": dict(cargo_counts),
        "top_pickup_points": dict(pickup_counts.most_common(5)),
        "assigned_rate_pct": round(assigned_count / total * 100, 1),
        "urgent_rate_pct": round(urgent_count / total * 100, 1),
        "avg_cargo_weight_kg": round(avg_weight, 1),
        "total_weight_tonnes": round(total_weight / 1000, 2),
    }


def get_pending_only_subset(tasks: List[TaskData]) -> List[TaskData]:
    """获取仅含 pending 状态的子集（方便直接POST注入）"""
    return [t for t in tasks if t.status == TaskStatus.PENDING]


def inject_to_api(tasks: List[TaskData], base_url: str, mode: str = "create") -> bool:
    """通过 API 注入任务数据"""
    try:
        import requests
        
        url = f"{base_url.rstrip('/')}/api/v1/tasks/batch"
        
        payload = {
            "tasks": [t.to_dict() for t in tasks],
            "mode": mode,
        }
        
        resp = requests.post(url, json=payload, timeout=30)
        print(f"  API inject ({len(tasks)} tasks): {resp.status_code} - {resp.text[:200]}")
        return 200 <= resp.status_code < 300
        
    except Exception as e:
        print(f"  [WARN] API injection failed: {e}")
        return False


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="Generate task scenario data for benchmarking")
    parser.add_argument("--tasks", "-n", type=int, help="Number of tasks to generate")
    parser.add_argument("--size-preset", "-p", choices=list(TASK_SIZE_PRESETS.keys()),
                        help="Use predefined size preset")
    parser.add_argument("--ratio", "-r", type=float, default=0.4,
                        help="High priority ratio (default: 0.4)")
    parser.add_argument("--pending-only", action="store_true",
                        help="Only generate pending-status tasks")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--inject-url", help="Inject to API at this URL")
    
    args = parser.parse_args()
    
    # 确定数量
    if args.tasks:
        n = args.tasks
    elif args.size_preset:
        n = TASK_SIZE_PRESETS[args.size_preset]
    else:
        n = TASK_SIZE_PRESETS["medium"]  # 默认200条
    
    output_file = args.output or f"tasks_{n}.json"
    
    print(f"\nGenerating task scenario:")
    print(f"  Count:    {n}")
    print(f"  Priority: {args.ratio*:.0%}% high")
    print(f"  Pending only: {args.pending_only}")
    print(f"  Seed:     {args.seed}")
    
    tasks = generate_tasks(n, args.ratio, args.pending_only, args.seed)
    stats = calculate_task_stats(tasks)
    
    result = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "2.3",
            "generator": "generate_task_scenario.py",
            "config": {
                "num_tasks": n,
                "high_priority_ratio": args.ratio,
                "pending_only": args.pending_only,
                "seed": args.seed,
            },
        },
        "statistics": stats,
        "pending_subset_size": len(get_pending_only_subset(tasks)),
        "tasks": [t.to_dict() for t in tasks],
    }
    
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Generated {n} tasks → {output_file}")
    print(f"\nStatistics:")
    for k, v in stats.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for sk, sv in list(v.items())[:8]:
                print(f"    {sk}: {sv}")
        else:
            print(f"  {k}: {v}")
    
    if args.inject_url:
        inject_tasks = get_pending_only_subset(tasks) if not args.pending_only else tasks
        print(f"\nInjecting {len(inject_tasks)} tasks to {args.inject_url}...")
        success = inject_to_api(inject_tasks, args.inject_url)
        print(f"{'✓' if success else '✗'} Inject {'successful' if 'success' else 'failed'}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
