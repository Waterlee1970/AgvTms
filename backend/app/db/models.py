"""
SQLAlchemy ORM models for persistence.

Maps the Pydantic schemas to database tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# JSONB may not be available on SQLite (dev fallback); use a portable JSON type
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType
    _USE_JSONB = True
except ImportError:
    _USE_JSONB = False

# Fallback: use generic JSON for non-PG
from sqlalchemy import JSON

from .database import Base


def _json_column(**kwargs):
    """Return a JSON column that uses JSONB on PostgreSQL, JSON elsewhere."""
    if _USE_JSONB:
        return mapped_column(JSONB, **kwargs)
    return mapped_column(JSON, **kwargs)


class MapRecord(Base):
    """Map graph storage (complete MapGraph serialized as JSON)."""
    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    graph_data: Mapped[dict] = _json_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgvRecord(Base):
    """AGV master data and last-known state."""
    __tablename__ = "agvs"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    vehicle_type: Mapped[str] = mapped_column(String(50), default="standard")
    state: Mapped[dict] = _json_column(nullable=False, default=dict)
    last_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskRecord(Base):
    """Transport tasks."""
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(20), default="agv")
    task_data: Mapped[dict] = _json_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    assigned_agv: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ScheduleResultRecord(Base):
    """Persisted scheduling results for historical query."""
    __tablename__ = "schedule_results"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    result_data: Mapped[dict] = _json_column(nullable=False)
    assignments: Mapped[dict] = _json_column(nullable=False, default=list)
    metrics: Mapped[dict] = _json_column(nullable=False, default=dict)
    agv_paths: Mapped[dict] = _json_column(nullable=False, default=dict)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    makespan: Mapped[float] = mapped_column(Float, default=0.0)
    algorithm_version: Mapped[str] = mapped_column(String(10), default="v1")
    runtime_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AlgorithmConfigRecord(Base):
    """Algorithm configuration storage with active version tracking."""
    __tablename__ = "algorithm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(10), nullable=False)
    config_data: Mapped[dict] = _json_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConveyorSegmentRecord(Base):
    """Conveyor line segments."""
    __tablename__ = "conveyor_segments"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    segment_data: Mapped[dict] = _json_column(nullable=False, default=dict)
    from_node: Mapped[str] = mapped_column(String(50), nullable=False)
    to_node: Mapped[str] = mapped_column(String(50), nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=0.5)
    length: Mapped[float] = mapped_column(Float, default=10.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
