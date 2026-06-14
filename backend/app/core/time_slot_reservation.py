"""
时间片预约机制 — 参考 openTCS Scheduling 时间片预约.

openTCS: TreeMap<Long, Set<Path>> 提前锁定未来时间段
AGV-TMS: 按路径 ID 管理时间段预约, 避免运行时争抢

功能:
  - reserve(path_id, start, end, agv_id): 预约路径时间段
  - release(path_id, agv_id): 释放某 AGV 的所有预约
  - is_available(path_id, start, end): 检查可用性
  - get_reservations(path_id): 查询预约列表

适用场景:
  - JIT 准时制生产: 提前锁定关键路径
  - 多 AGV 无碰撞: 路径搜索时检查预约
  - 死锁预防: 避免循环等待
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Reservation:
    """单个预约记录"""
    path_id: str
    start_time: float
    end_time: float
    agv_id: str
    order_id: str = ""

    def overlaps(self, other_start: float, other_end: float) -> bool:
        """检查是否与给定时间段重叠"""
        return self.start_time < other_end and self.end_time > other_start


class TimeSlotReservation:
    """
    时间片预约管理器.

    数据结构: Dict[path_id, List[Reservation]]
    每个 path_id 的预约列表按 start_time 排序, 支持二分查找快速检查。
    """

    def __init__(self):
        self._reservations: Dict[str, List[Reservation]] = {}

    def reserve(
        self,
        path_id: str,
        start_time: float,
        end_time: float,
        agv_id: str,
        order_id: str = "",
    ) -> bool:
        """
        预约路径时间段.

        Returns:
            True = 预约成功, False = 时间冲突
        """
        if start_time >= end_time:
            return False

        reservations = self._reservations.setdefault(path_id, [])

        # 检查冲突 (允许同一 AGV 重叠预约)
        for r in reservations:
            if r.agv_id == agv_id:
                continue  # 同一 AGV 不冲突
            if r.overlaps(start_time, end_time):
                logger.debug(
                    "Reservation conflict on %s: AGV %s [%s-%s] vs AGV %s [%s-%s]",
                    path_id, r.agv_id, r.start_time, r.end_time, agv_id, start_time, end_time
                )
                return False

        # 插入并保持排序
        new_res = Reservation(path_id, start_time, end_time, agv_id, order_id)
        idx = bisect_left([r.start_time for r in reservations], start_time)
        reservations.insert(idx, new_res)

        logger.debug("Reserved %s for AGV %s [%s-%s]", path_id, agv_id, start_time, end_time)
        return True

    def release(self, path_id: str, agv_id: str) -> int:
        """
        释放某 AGV 在某路径上的所有预约.

        Returns:
            释放的预约数量
        """
        reservations = self._reservations.get(path_id, [])
        before = len(reservations)
        self._reservations[path_id] = [r for r in reservations if r.agv_id != agv_id]
        released = before - len(self._reservations[path_id])
        if released > 0:
            logger.debug("Released %d reservations on %s for AGV %s", released, path_id, agv_id)
        return released

    def release_all(self, agv_id: str) -> int:
        """释放某 AGV 在所有路径上的预约"""
        total = 0
        for path_id in list(self._reservations.keys()):
            total += self.release(path_id, agv_id)
        return total

    def is_available(
        self,
        path_id: str,
        start_time: float,
        end_time: float,
        agv_id: str = "",
    ) -> bool:
        """检查路径在指定时间段是否可用"""
        reservations = self._reservations.get(path_id, [])
        for r in reservations:
            if r.agv_id == agv_id:
                continue
            if r.overlaps(start_time, end_time):
                return False
        return True

    def get_reservations(self, path_id: str) -> List[Reservation]:
        """获取路径的所有预约"""
        return list(self._reservations.get(path_id, []))

    def get_agv_reservations(self, agv_id: str) -> List[Reservation]:
        """获取某 AGV 的所有预约"""
        result = []
        for reservations in self._reservations.values():
            result.extend(r for r in reservations if r.agv_id == agv_id)
        return result

    def cleanup_expired(self, current_time: float) -> int:
        """清理已过期的预约 (end_time < current_time)"""
        total_removed = 0
        for path_id in list(self._reservations.keys()):
            before = len(self._reservations[path_id])
            self._reservations[path_id] = [
                r for r in self._reservations[path_id] if r.end_time >= current_time
            ]
            total_removed += before - len(self._reservations[path_id])
        if total_removed > 0:
            logger.debug("Cleaned up %d expired reservations", total_removed)
        return total_removed

    def get_stats(self) -> Dict[str, int]:
        """获取预约统计"""
        total = sum(len(r) for r in self._reservations.values())
        return {
            "total_paths": len(self._reservations),
            "total_reservations": total,
            "paths_with_reservations": sum(1 for r in self._reservations.values() if r),
        }


# ==================== 全局单例 ====================

reservation_manager = TimeSlotReservation()
